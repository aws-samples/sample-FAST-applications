"""
Custom Resource Lambda for managing AgentCore Gateway Cedar Policy lifecycle.

This Lambda is invoked by CloudFormation during stack deployment to manage a
Policy Engine and **multiple Cedar Policies** attached to it. AgentCore's
CreatePolicy API accepts only one Cedar statement per call, so the CDK side
splits the policy authoring into one *.cedar file per statement and passes
them to this Lambda as `PolicyDocuments` (list).

Lifecycle:
  Create  -> create Policy Engine, create one Policy per document, attach
             Policy Engine to Gateway with ENFORCE mode.
  Update  -> delete all managed Policies (matched by engine_name prefix),
             create a fresh set from the new document list, verify the
             engine is still attached to the Gateway.
  Delete  -> detach Policy Engine from Gateway, delete all managed Policies,
             delete the Policy Engine.

PhysicalResourceId stores only the engine id; individual policy ids are
discovered at runtime via list_policies because (a) the engine_name prefix
uniquely identifies our managed policies and (b) keeping all policy ids in
the PhysicalResourceId could exceed CloudFormation's length budget when the
file count grows.

Waiter Strategy:
- Policy creation uses the policy_active waiter. Policy deletion uses the
  policy_deleted waiter. Policy Engine creation uses the policy_engine_active
  waiter. Policy Engine deletion uses the policy_engine_deleted waiter.
- Gateway operations currently use a custom polling loop as the
  bedrock-agentcore-control service does not provide an official waiter for
  gateway status changes.
"""

import logging
import time
import uuid

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

client = boto3.client("bedrock-agentcore-control")

# Polling configuration for gateway
GATEWAY_POLL_INTERVAL_SECONDS = 5
GATEWAY_TIMEOUT_SECONDS = 300


def handler(event: dict, context: dict) -> dict:
    """
    CloudFormation Custom Resource handler for Cedar Policy lifecycle.

    Args:
        event: CloudFormation event containing RequestType and ResourceProperties.
        context: Lambda context object.

    Returns:
        Response dict with PhysicalResourceId and optional Data attributes.
    """
    request_type = event["RequestType"]
    props = event["ResourceProperties"]

    logger.info(f"Request type: {request_type}")
    logger.info(f"Gateway ID: {props['GatewayIdentifier']}")

    try:
        if request_type == "Create":
            return handle_create(props)
        elif request_type == "Update":
            return handle_update(event, props)
        elif request_type == "Delete":
            return handle_delete(event, props)
        else:
            raise ValueError(f"Unknown request type: {request_type}")

    except Exception as e:
        logger.error(f"Error handling {request_type}: {str(e)}", exc_info=True)
        raise


def handle_create(props: dict) -> dict:
    """
    Create Policy Engine, one Cedar Policy per document, and attach to Gateway.

    Steps:
      1. Create Policy Engine -> wait for ACTIVE
      2. For each document in `PolicyDocuments`, create a Cedar Policy and wait
         for ACTIVE
      3. Attach Policy Engine to Gateway -> wait for READY

    Args:
        props: ResourceProperties from CloudFormation event.

    Returns:
        Response with PhysicalResourceId set to the Policy Engine id.
    """
    gateway_id = props["GatewayIdentifier"]
    policy_documents = _normalize_policy_documents(props)
    description = props.get("Description", "Cedar policy for AgentCore Gateway")
    engine_name = props["PolicyEngineName"]

    # Step 1: Create Policy Engine
    logger.info(f"Creating Policy Engine: {engine_name}")
    engine_response = client.create_policy_engine(
        name=engine_name,
        description=f"Policy engine for gateway {gateway_id}",
        clientToken=str(uuid.uuid4()),
    )
    policy_engine_id = engine_response["policyEngineId"]
    logger.info(f"Policy Engine created: {policy_engine_id}")

    # Wait for Policy Engine to become ACTIVE using official waiter
    logger.info(f"Waiting for Policy Engine {policy_engine_id} to become ACTIVE...")
    waiter = client.get_waiter("policy_engine_active")
    waiter.wait(policyEngineId=policy_engine_id)
    logger.info(f"Policy Engine {policy_engine_id} is now ACTIVE")

    # Get the Policy Engine ARN for attaching to gateway
    engine_details = client.get_policy_engine(policyEngineId=policy_engine_id)
    policy_engine_arn = engine_details["policyEngineArn"]

    # Step 2: Create one Cedar Policy per document
    policy_ids = _create_policies(
        policy_engine_id, engine_name, description, policy_documents
    )

    # Step 3: Attach Policy Engine to Gateway
    _attach_policy_engine_to_gateway(gateway_id, policy_engine_arn)

    return {
        "PhysicalResourceId": policy_engine_id,
        "Data": {
            "PolicyEngineId": policy_engine_id,
            "PolicyIds": ",".join(policy_ids),
            "PolicyCount": str(len(policy_ids)),
            "PolicyEngineArn": policy_engine_arn,
        },
    }


def handle_update(event: dict, props: dict) -> dict:
    """
    Update Cedar Policies by deleting all managed policies in the Policy
    Engine and recreating one Policy per document.

    Also verifies the Policy Engine is still attached to the Gateway and
    re-attaches if needed. This handles cases where a previous failed
    deployment rollback may have detached the engine.

    Returns the SAME PhysicalResourceId to prevent CloudFormation from
    interpreting the update as a resource replacement (which would trigger
    a cleanup Delete that detaches the policy engine).

    Args:
        event: CloudFormation event.
        props: ResourceProperties from CloudFormation event.

    Returns:
        Response with original PhysicalResourceId and new policy IDs in Data.
    """
    physical_id = event["PhysicalResourceId"]
    policy_engine_id = _extract_engine_id(physical_id)

    gateway_id = props["GatewayIdentifier"]
    policy_documents = _normalize_policy_documents(props)
    description = props.get("Description", "Cedar policy for AgentCore Gateway")
    engine_name = props["PolicyEngineName"]

    # Delete every managed policy in the engine (matched by engine_name prefix)
    # so we start from a clean slate. _delete_managed_policies tolerates stale
    # ids and missing policies — handy after a previous failed deploy.
    _delete_managed_policies(policy_engine_id, None, props)

    # Create one Cedar Policy per document.
    new_policy_ids = _create_policies(
        policy_engine_id, engine_name, description, policy_documents
    )

    # Verify the Policy Engine is still attached to the Gateway.
    # A previous failed deployment rollback or manual change may have detached it.
    logger.info("Verifying Policy Engine is attached to Gateway...")
    gateway = client.get_gateway(gatewayIdentifier=gateway_id)
    pe_config = gateway.get("policyEngineConfiguration") or {}

    if not pe_config.get("arn"):
        logger.warning("Policy Engine is detached from Gateway — re-attaching...")
        engine_details = client.get_policy_engine(policyEngineId=policy_engine_id)
        policy_engine_arn = engine_details["policyEngineArn"]
        _attach_policy_engine_to_gateway(gateway_id, policy_engine_arn)
        logger.info("Policy Engine re-attached to Gateway successfully")
    else:
        logger.info("Policy Engine is attached to Gateway")

    # CRITICAL: Return the SAME PhysicalResourceId so CloudFormation does not
    # treat the update as a replacement (which would call Delete on the old
    # physical id and detach the Policy Engine).
    return {
        "PhysicalResourceId": physical_id,
        "Data": {
            "PolicyEngineId": policy_engine_id,
            "PolicyIds": ",".join(new_policy_ids),
            "PolicyCount": str(len(new_policy_ids)),
        },
    }


def handle_delete(event: dict, props: dict) -> dict:
    """
    Detach Policy Engine from Gateway, delete all managed Cedar Policies,
    and delete the Policy Engine.

    Steps:
      1. Detach Policy Engine from Gateway -> wait for READY
      2. Delete all managed Cedar Policies (handles stale IDs from prior updates)
      3. Delete Policy Engine

    Args:
        event: CloudFormation event.
        props: ResourceProperties from CloudFormation event.

    Returns:
        Response with PhysicalResourceId.
    """
    physical_id = event["PhysicalResourceId"]
    gateway_id = props["GatewayIdentifier"]
    policy_engine_id = _extract_engine_id(physical_id)

    if not policy_engine_id:
        logger.warning(f"Unexpected PhysicalResourceId format: {physical_id}")
        return {"PhysicalResourceId": physical_id}

    # Step 1: Detach Policy Engine from Gateway
    logger.info(f"Detaching Policy Engine from Gateway: {gateway_id}")
    try:
        gateway = client.get_gateway(gatewayIdentifier=gateway_id)
        # Omit policyEngineConfiguration entirely to detach
        client.update_gateway(
            gatewayIdentifier=gateway_id,
            name=gateway.get("name"),
            roleArn=gateway.get("roleArn"),
            protocolType=gateway.get("protocolType", "MCP"),
            authorizerType=gateway.get("authorizerType", "CUSTOM_JWT"),
            authorizerConfiguration=gateway.get("authorizerConfiguration"),
        )
        _wait_for_gateway_ready(gateway_id)
        logger.info("Policy Engine detached from Gateway")
    except Exception as e:
        logger.warning(f"Could not detach Policy Engine from Gateway: {e}")

    # Step 2: Delete all Cedar Policies managed by this Custom Resource.
    _delete_managed_policies(policy_engine_id, None, props)

    # Step 3: Delete Policy Engine
    logger.info(f"Deleting Policy Engine: {policy_engine_id}")
    try:
        client.delete_policy_engine(policyEngineId=policy_engine_id)
        waiter = client.get_waiter("policy_engine_deleted")
        waiter.wait(policyEngineId=policy_engine_id)
        logger.info(f"Policy Engine deleted: {policy_engine_id}")
    except Exception as e:
        logger.warning(f"Could not delete Policy Engine {policy_engine_id}: {e}")

    return {"PhysicalResourceId": physical_id}


def _normalize_policy_documents(props: dict) -> list:
    """Return the list of Cedar statements from CloudFormation properties.

    Accepts either `PolicyDocuments` (list, preferred) or the legacy
    `PolicyDocument` (single string). Filters out empty documents.

    Args:
        props: ResourceProperties from CloudFormation event.

    Returns:
        Non-empty list of Cedar statement strings.

    Raises:
        ValueError: If neither key is present or the result is empty.
    """
    docs = props.get("PolicyDocuments")
    if docs is None:
        legacy = props.get("PolicyDocument")
        if legacy is None:
            raise ValueError(
                "Neither 'PolicyDocuments' nor 'PolicyDocument' was provided"
            )
        docs = [legacy]
    if not isinstance(docs, list):
        raise ValueError(f"'PolicyDocuments' must be a list, got {type(docs).__name__}")
    docs = [d for d in docs if isinstance(d, str) and d.strip()]
    if not docs:
        raise ValueError("PolicyDocuments is empty after stripping blanks")
    return docs


def _extract_engine_id(physical_id: str) -> str:
    """Extract the policy engine id from a PhysicalResourceId.

    Newer Custom Resource invocations store just the engine id; older ones
    used "<engine_id>|<policy_id>". Both forms are supported here so an
    in-place upgrade does not require a stack replacement.

    Args:
        physical_id: The CloudFormation PhysicalResourceId.

    Returns:
        The Policy Engine id, or "" if the format is unrecognised.
    """
    if not physical_id:
        return ""
    return physical_id.split("|", 1)[0]


def _create_policies(
    policy_engine_id: str,
    engine_name: str,
    description: str,
    policy_documents: list,
) -> list:
    """Create one Cedar Policy per document inside the engine and wait ACTIVE.

    Args:
        policy_engine_id: The Policy Engine identifier.
        engine_name: Used to build the policy name prefix
            (`{engine_name}_cp_*`) — the same prefix used by
            `_delete_managed_policies` to find managed policies.
        description: Description applied to every policy.
        policy_documents: List of Cedar statement strings.

    Returns:
        List of created Policy ids in the order they were created.
    """
    created_ids = []
    timestamp = int(time.time())
    waiter = client.get_waiter("policy_active")

    for index, document in enumerate(policy_documents):
        # Policy names cap at 48 chars. The `{engine_name}_cp` prefix is a
        # hard requirement (_delete_managed_policies matches on it), so when
        # the full `{engine_name}_cp_{ts}_{idx}` name exceeds the cap we
        # shorten the TIMESTAMP part and always keep the index — truncating
        # the tail (`policy_name[:48]`) would give every policy the same name
        # and CreatePolicy fails with ConflictException from the 2nd one.
        policy_name = f"{engine_name}_cp_{timestamp}_{index}"
        if len(policy_name) > 48:
            prefix = f"{engine_name}_cp_"
            budget = 48 - len(prefix)
            tail = f"{timestamp}_{index}"
            # Keep the end of the tail: the index (uniqueness within this
            # deploy) plus as many low-order timestamp digits as fit
            # (uniqueness across earlier failed deploys).
            policy_name = f"{prefix}{tail[-budget:]}"

        logger.info(
            f"Creating Cedar Policy [{index + 1}/{len(policy_documents)}]: "
            f"{policy_name}"
        )
        response = client.create_policy(
            policyEngineId=policy_engine_id,
            name=policy_name,
            description=description,
            definition={"cedar": {"statement": document}},
        )
        policy_id = response["policyId"]
        logger.info(f"Cedar Policy created: {policy_id}")
        waiter.wait(policyEngineId=policy_engine_id, policyId=policy_id)
        logger.info(f"Cedar Policy {policy_id} is now ACTIVE")
        created_ids.append(policy_id)

    return created_ids


def _delete_managed_policies(
    policy_engine_id: str, known_policy_id: str | None, props: dict
) -> None:
    """
    Delete every Cedar policy managed by this Custom Resource.

    The "managed" set is identified by the engine_name prefix
    (`{engine_name}_cp`). This sweep handles all cases: a Create/Delete with a
    single known id, a multi-policy Update, and policies orphaned by an
    earlier failed deploy.

    Args:
        policy_engine_id: The Policy Engine identifier.
        known_policy_id: Optional id from a legacy PhysicalResourceId. May be
            None in the multi-policy world.
        props: ResourceProperties containing PolicyEngineName.
    """
    engine_name = props.get("PolicyEngineName", "")
    waiter = client.get_waiter("policy_deleted")

    # If a legacy single-id is supplied, try it first. Failures are non-fatal —
    # the listing pass below will catch anything that survives.
    if known_policy_id:
        logger.info(f"Deleting Cedar Policy: {known_policy_id}")
        try:
            client.delete_policy(
                policyEngineId=policy_engine_id,
                policyId=known_policy_id,
            )
            waiter.wait(policyEngineId=policy_engine_id, policyId=known_policy_id)
            logger.info(f"Policy deleted: {known_policy_id}")
        except client.exceptions.ResourceNotFoundException:
            logger.warning(f"Policy {known_policy_id} not found (stale ID)")
        except Exception as e:
            logger.warning(f"Could not delete policy {known_policy_id}: {e}")

    # Now sweep every policy whose name starts with our engine_name prefix.
    # This is the source of truth for "managed by this Custom Resource" since
    # we no longer track ids in the PhysicalResourceId.
    try:
        policies = client.list_policies(policyEngineId=policy_engine_id)
        for p in policies.get("policies", []):
            p_id = p["policyId"]
            p_name = p.get("name", "")
            if not p_name.startswith(f"{engine_name}_cp"):
                continue
            logger.info(f"Deleting managed policy: {p_id} ({p_name})")
            try:
                client.delete_policy(
                    policyEngineId=policy_engine_id,
                    policyId=p_id,
                )
                waiter.wait(policyEngineId=policy_engine_id, policyId=p_id)
                logger.info(f"Policy deleted: {p_id}")
            except client.exceptions.ResourceNotFoundException:
                logger.warning(f"Policy {p_id} not found (already deleted)")
            except Exception as e:
                logger.warning(f"Could not delete policy {p_id}: {e}")
    except Exception as e:
        logger.warning(f"Could not list policies in engine: {e}")


def _attach_policy_engine_to_gateway(gateway_id: str, policy_engine_arn: str) -> None:
    """
    Attach a Policy Engine to a Gateway and wait for the Gateway to become READY.

    Args:
        gateway_id: The Gateway identifier.
        policy_engine_arn: The Policy Engine ARN to attach.
    """
    logger.info(f"Attaching Policy Engine {policy_engine_arn} to Gateway {gateway_id}")

    gateway = client.get_gateway(gatewayIdentifier=gateway_id)

    client.update_gateway(
        gatewayIdentifier=gateway_id,
        name=gateway.get("name"),
        roleArn=gateway.get("roleArn"),
        protocolType=gateway.get("protocolType", "MCP"),
        authorizerType=gateway.get("authorizerType", "CUSTOM_JWT"),
        authorizerConfiguration=gateway.get("authorizerConfiguration"),
        policyEngineConfiguration={
            "arn": policy_engine_arn,
            "mode": "ENFORCE",
        },
    )

    _wait_for_gateway_ready(gateway_id)
    logger.info("Policy Engine attached to Gateway successfully")


def _wait_for_gateway_ready(gateway_id: str) -> None:
    """
    Poll until the Gateway reaches READY status.

    This uses a custom polling loop as the boto3 SDK provides official waiters
    for Policy Engine and Policy operations (policy_engine_active,
    policy_engine_deleted, policy_active, policy_deleted) but not for Gateway
    status changes.

    Args:
        gateway_id: The Gateway identifier to poll.

    Raises:
        RuntimeError: If the gateway fails or times out.
    """
    logger.info(f"Waiting for Gateway {gateway_id} to become READY...")
    start_time = time.time()

    while time.time() - start_time < GATEWAY_TIMEOUT_SECONDS:
        gateway = client.get_gateway(gatewayIdentifier=gateway_id)
        status = gateway.get("status")
        logger.info(f"Gateway status: {status}")

        if status == "READY":
            return

        if status in ("FAILED", "UPDATE_UNSUCCESSFUL"):
            raise RuntimeError(f"Gateway reached terminal state: {status}")

        time.sleep(GATEWAY_POLL_INTERVAL_SECONDS)

    raise RuntimeError(
        f"Gateway {gateway_id} did not become READY within {GATEWAY_TIMEOUT_SECONDS}s"
    )
