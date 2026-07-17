#!/usr/bin/env python3

"""End-to-end check that the S3 Files skills mount works (Phase 2b).

Invokes the VPC-mode runtime with a Cognito user token and asks the agent to
list its available skills. If the S3 Files mount + AgentSkills plugin work, the
streamed response (and the injected <available_skills> context the model echoes)
should name skills vendored under /mnt/skills (e.g. aws-cdk, aws-iam,
aws-serverless). This is the decisive proof that the mount is live, since the
get-agent-runtime API does not surface filesystemConfigurations.

Mutates only the admin user's password (USER_PASSWORD_AUTH), same as
test-runtime-vpc-e2e.py. Stack identifiers are resolved from the CloudFormation
outputs of STACK_NAME; the admin username defaults to the documented admin
alias and can be overridden with ADMIN_USERNAME.

Usage:
    STACK_NAME=fast-specialist-agent uv run test-scripts/test-skills-e2e.py
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

# A few skills we know are vendored, to look for in the response.
EXPECTED_SKILLS = ["aws-cdk", "aws-iam", "aws-serverless", "amazon-bedrock"]


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
    password = "SkillsE2e-Test-" + uuid.uuid4().hex[:12] + "!9"
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
    parts: list[str] = []
    for line in resp.iter_lines(decode_unicode=True):
        if line:
            parts.append(line if isinstance(line, str) else line.decode())
    return resp.status_code, "\n".join(parts)


def main() -> int:
    stack_name = os.environ.get("STACK_NAME", "fast-specialist-agent")
    cfg = get_stack_config(stack_name)
    region: str = cfg["region"]
    outputs: dict[str, str] = cfg["outputs"]
    runtime_arn: str = outputs["RuntimeArn"]
    user_pool_id: str = outputs["CognitoUserPoolId"]
    client_id: str = outputs["CognitoClientId"]
    admin_username = os.environ.get("ADMIN_USERNAME", DEFAULT_ADMIN_USERNAME)

    session_id = "skills-e2e-" + uuid.uuid4().hex + uuid.uuid4().hex[:4]
    print(f"Stack   : {stack_name}")
    print(f"Runtime : {runtime_arn}")
    print(f"Session : {session_id}")
    print()

    print("[1/2] Fetching Cognito access token...")
    token = _get_access_token(user_pool_id, client_id, admin_username, region)
    print("      OK")
    print()

    print("[2/2] Asking the agent to list its available skills...")
    prompt = (
        "List every skill available to you from your skills library. "
        "Output only the skill names, comma-separated."
    )
    status, body = _invoke(runtime_arn, region, token, prompt, session_id)
    print(f"      HTTP {status}")
    # Collect the streamed text into one blob for matching.
    text = body.lower()
    found = [s for s in EXPECTED_SKILLS if s in text]
    print(f"      response chars: {len(body)}")
    print(f"      sample: {body[:600]}")
    print()

    print("=" * 60)
    print(f" invoke HTTP 200      : {'OK' if status == 200 else 'FAIL'}")
    print(f" expected skills seen : {found if found else 'NONE'}")
    print(
        f" skills mount verdict : "
        f"{'OK (skills visible)' if len(found) >= 2 else 'INCONCLUSIVE/FAIL'}"
    )
    print("=" * 60)
    return 0 if (status == 200 and len(found) >= 2) else 1


if __name__ == "__main__":
    sys.exit(main())
