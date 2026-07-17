"""AgentCore Gateway MCP client with OAuth2 authentication.

Auth approach: the M2M token for the Gateway is obtained via AgentCore Identity
(``@requires_access_token`` + Token Vault), which runs server-side and is
reachable through the ``bedrock-agentcore`` VPC endpoint — no NAT needed, so the
runtime can stay closed-network.

User identity propagation: the validated user's JWT ``sub`` AND their
``cognito:groups`` (both read from the pre-validated access token) are forwarded
to the Cognito Client Credentials token endpoint as ``ClientMetadata``. The V3
Pre-Token Lambda reads the groups and injects the ``department``/``role`` claims
the Gateway's Cedar policies evaluate. Passing the groups (which the token
already proves) means the Lambda does NOT need to call AdminListGroupsForUser —
no extra IAM permission, no extra API round trip, and no UserPool <-> Lambda
circular dependency in the CDK stack. This keeps per-user authorization working
without the runtime calling the public Cognito hosted domain directly.

The forwarding path is specific, and the filtering happens at the Cognito layer
(not AgentCore): ``GetResourceOauth2Token`` passes every ``custom_parameters``
entry through to the Cognito ``/oauth2/token`` request body unchanged (SDK:
``bedrock_agentcore.services.identity.get_token`` -> ``customParameters``).
Cognito then forwards only the body parameter named ``aws_client_metadata`` (a
URL-encoded JSON string) to the Pre-Token Lambda as ``ClientMetadata``, and
ignores any other parameter. So the sub MUST travel inside the
``aws_client_metadata`` JSON; a sibling ``verified_user_id`` entry is accepted
by AgentCore but dropped by Cognito.

Refs (verified against first-party sources):
- Cognito token endpoint, ``aws_client_metadata`` parameter (M2M only, trigger
  v3+): https://docs.aws.amazon.com/cognito/latest/developerguide/token-endpoint.html
- ``GetResourceOauth2Token`` ``customParameters`` (passed through, does not
  override standard params):
  https://docs.aws.amazon.com/bedrock-agentcore/latest/APIReference/API_GetResourceOauth2Token.html
"""

import json
import logging
import os

from bedrock_agentcore.identity.auth import requires_access_token
from mcp.client.streamable_http import streamablehttp_client
from strands.tools.mcp import MCPClient
from utils.ssm import get_ssm_parameter

logger = logging.getLogger(__name__)


def create_gateway_mcp_client(user_id: str, groups: list[str]) -> MCPClient:
    """Create an MCP client for AgentCore Gateway with user identity propagation.

    The token fetch runs INSIDE the lambda factory so a fresh token is obtained
    on every MCP reconnection (prevents stale-token 401s).

    Args:
        user_id (str): The authenticated user's ID (JWT ``sub``), propagated
            inside ``aws_client_metadata`` for auditing/logging.
        groups (list[str]): The user's ``cognito:groups`` from the validated
            token, propagated inside ``aws_client_metadata`` so the Pre-Token
            Lambda can derive the Cedar ``department``/``role`` claims without
            an AdminListGroupsForUser call.
    """
    stack_name = os.environ.get("STACK_NAME")
    if not stack_name:
        raise ValueError("STACK_NAME environment variable is required")
    if not stack_name.replace("-", "").replace("_", "").isalnum():
        raise ValueError("Invalid STACK_NAME format")

    gateway_url = get_ssm_parameter(f"/{stack_name}/gateway_url")
    logger.info("[GATEWAY] URL: %s", gateway_url)

    # Build the decorator inside the function so the closure captures this
    # request's user_id — the decorator binds its arguments at definition time,
    # and custom_parameters must vary per user.
    #
    # force_authentication is left False: the Token Vault keys its M2M session on
    # the custom_parameters, so a different verified_user_id yields a different
    # token rather than a reused one. This was verified empirically — invoking
    # finance -> guest -> finance -> guest back to back, the Pre-Token Lambda
    # resolved the correct department each time and the low-privilege user never
    # inherited the high-privilege user's token (no cross-user contamination).
    # Forcing re-auth would only add a redundant Cognito round trip per call.
    #
    # The identity MUST be nested inside the reserved "aws_client_metadata" JSON
    # (see the module docstring): Cognito forwards only that body parameter to
    # the Pre-Token Lambda. Sibling top-level entries reach Cognito but are
    # ignored there. verified_groups carries the user's proven group membership
    # so the Lambda can map it to department/role with no API call.
    #
    # aws_client_metadata must be a FLAT string->string map: Cognito's
    # ClientMetadata is Map<String,String>, so a nested array value (e.g.
    # ["finance"]) is rejected with `invalid_aws_client_metadata` (verified
    # 2026-05-30). The groups are therefore joined into a comma-separated string
    # and split back apart in the Pre-Token Lambda.
    @requires_access_token(
        provider_name=os.environ["GATEWAY_CREDENTIAL_PROVIDER_NAME"],
        auth_flow="M2M",
        scopes=[],
        force_authentication=False,
        custom_parameters={
            "aws_client_metadata": json.dumps(
                {"verified_user_id": user_id, "verified_groups": ",".join(groups)}
            ),
        },
    )
    def _fetch_gateway_token(*, access_token: str = "") -> str:
        return access_token

    # The actor-id header rides through to MCP server targets hosted on
    # AgentCore Runtime (e.g. the ltm-mcp target): the Gateway
    # forwards it per the target's metadataConfiguration.allowedRequestHeaders
    # and the hosting runtime's allowlistedHeaders. The Gateway swaps the
    # Authorization header for its own SigV4 signature on the outbound call,
    # so the JWT's user_id claim never reaches the MCP server — this header is
    # the only channel that does. Its trust root is the same self-declaration
    # as aws_client_metadata above.
    return MCPClient(
        lambda: streamablehttp_client(
            url=gateway_url,
            headers={
                "Authorization": f"Bearer {_fetch_gateway_token()}",
                "X-Amzn-Bedrock-AgentCore-Runtime-Custom-Actor-Id": user_id,
            },
        ),
        prefix="gateway",
    )
