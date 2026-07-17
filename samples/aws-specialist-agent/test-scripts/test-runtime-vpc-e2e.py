#!/usr/bin/env python3

"""End-to-end check that the VPC-mode AgentCore Runtime actually works.

Phase 2a moved the runtime into private subnets. This exercises the full
private-network path in one invocation:
  - inbound: InvokeAgentRuntime with a real Cognito user access token
  - runtime cold start: ECR image pull via ecr.api/ecr.dkr + s3 gateway endpoint
  - outbound NAT: the agent's Approach-1 M2M token call to the public Cognito
    hosted domain, plus cognito-idp JWKS validation
  - outbound endpoints: Gateway (bedrock-agentcore.gateway) + Bedrock inference
    (bedrock-runtime)

It sets a permanent password on the existing admin user (USER_PASSWORD_AUTH),
fetches an access token, invokes the runtime, and asserts a non-error streamed
response. Read-only against infra; only mutates the admin user's password.

Stack identifiers (Runtime ARN, Cognito pool/client) are resolved from the
CloudFormation outputs of STACK_NAME, so this runs against any deployed
environment. The admin username defaults to the documented admin alias and can
be overridden with ADMIN_USERNAME.

Usage:
    STACK_NAME=fast-aws-specialist uv run test-scripts/test-runtime-vpc-e2e.py
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import boto3
import requests

# Add scripts directory to path for reliable imports
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from utils import authenticate_cognito, get_stack_config  # noqa: E402

DEFAULT_ADMIN_USERNAME = "admin@example.com"


def _get_access_token(
    user_pool_id: str, client_id: str, username: str, region: str
) -> str:
    """Set a throwaway permanent password on the admin user, return its token.

    Args:
        user_pool_id: Cognito User Pool ID from the stack outputs.
        client_id: Cognito app client ID from the stack outputs.
        username: Admin user's Cognito username (email).
        region: AWS region resolved from the stack ARN.

    Returns:
        A Cognito access token for the AgentCore Runtime JWT authorizer.
    """
    password = "VpcE2e-Test-" + uuid.uuid4().hex[:12] + "!9"
    idp = boto3.client("cognito-idp", region_name=region)
    idp.admin_set_user_password(
        UserPoolId=user_pool_id,
        Username=username,
        Password=password,
        Permanent=True,
    )
    access_token, _id_token, _user_id = authenticate_cognito(
        user_pool_id, client_id, username, password
    )
    return access_token


def _invoke(
    runtime_arn: str, region: str, access_token: str, prompt: str, session_id: str
) -> tuple[int, str]:
    """Invoke the VPC-mode runtime and collect the streamed response.

    Args:
        runtime_arn: AgentCore Runtime ARN from the stack outputs.
        region: AWS region resolved from the stack ARN.
        access_token: Cognito access token for the JWT authorizer.
        prompt: User prompt to send.
        session_id: AgentCore runtime session id (>= 33 chars).

    Returns:
        Tuple of (HTTP status code, joined streamed response text).
    """
    endpoint = f"https://bedrock-agentcore.{region}.amazonaws.com"
    escaped = requests.utils.quote(runtime_arn, safe="")
    url = f"{endpoint}/runtimes/{escaped}/invocations?qualifier=DEFAULT"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
    }
    payload = {"prompt": prompt, "runtimeSessionId": session_id}
    resp = requests.post(url, headers=headers, json=payload, stream=True, timeout=180)
    text_parts: list[str] = []
    for line in resp.iter_lines(decode_unicode=True):
        if line:
            text_parts.append(line if isinstance(line, str) else line.decode())
    return resp.status_code, "\n".join(text_parts)


def main() -> int:
    stack_name = os.environ.get("STACK_NAME", "fast-aws-specialist")
    cfg = get_stack_config(stack_name)
    region: str = cfg["region"]
    outputs: dict[str, str] = cfg["outputs"]
    runtime_arn: str = outputs["RuntimeArn"]
    user_pool_id: str = outputs["CognitoUserPoolId"]
    client_id: str = outputs["CognitoClientId"]
    admin_username = os.environ.get("ADMIN_USERNAME", DEFAULT_ADMIN_USERNAME)

    # Session id must be >= 33 chars per AgentCore runtime requirement.
    session_id = "vpc-e2e-" + uuid.uuid4().hex + uuid.uuid4().hex[:4]
    print(f"Stack      : {stack_name}")
    print(f"Runtime    : {runtime_arn}")
    print(f"Session    : {session_id}")
    print()

    print("[1/2] Fetching Cognito access token (admin user)...")
    token = _get_access_token(user_pool_id, client_id, admin_username, region)
    print("      OK")
    print()

    print("[2/2] Invoking VPC-mode runtime (exercises NAT + endpoints)...")
    prompt = "In one sentence, what AWS regions are there? Use your tools if helpful."
    status, body = _invoke(runtime_arn, region, token, prompt, session_id)
    print(f"      HTTP {status}")
    snippet = body[:800].replace("\n", " ")
    print(f"      body: {snippet}")
    print()

    ok = status == 200 and '"error"' not in body.lower()
    print("=" * 60)
    print(f" runtime invoke (VPC mode): {'OK' if ok else 'FAIL'}")
    print("=" * 60)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
