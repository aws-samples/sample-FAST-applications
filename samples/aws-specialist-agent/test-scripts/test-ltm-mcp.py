#!/usr/bin/env python3

"""
E2E verification for the ltm-mcp gateway target.

Hits the AgentCore Gateway with an M2M token elevated to the finance
department (same path as test-aws-mcp-call.py), then:
  1. tools/list  -> confirm ltm-mcp___list_long_term_memories is listed
  2. tools/call WITH the actor-id custom header -> expect a facts JSON
     (empty facts + note is fine on a fresh stack)
  3. tools/call WITHOUT the header -> expect the server's missing-header
     error (proves the header, not anything else, carries the identity)

Usage:
    STACK_NAME=fast-aws-specialist uv run test-scripts/test-ltm-mcp.py
"""

from __future__ import annotations

import json
import os
import sys

import boto3
import requests

ACTOR_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Custom-Actor-Id"
TOOL_NAME = "ltm-mcp___list_long_term_memories"


def _get_secret(secret_name: str, region: str) -> str:
    sm = boto3.client("secretsmanager", region_name=region)
    return sm.get_secret_value(SecretId=secret_name)["SecretString"]


def _get_ssm(stack: str, key: str, region: str) -> str:
    ssm = boto3.client("ssm", region_name=region)
    return ssm.get_parameter(Name=f"/{stack}/{key}")["Parameter"]["Value"]


def _fetch_token(
    token_url: str, client_id: str, client_secret: str, user_id: str, groups: str
) -> str:
    """Mirror tools/gateway.py: verified_user_id + verified_groups in metadata.

    The V3 Pre-Token Lambda maps verified_groups to the department/role
    claims Cedar evaluates; finance is permitted by 04-ltm-mcp.cedar.
    """
    response = requests.post(
        token_url,
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "aws_client_metadata": json.dumps(
                {"verified_user_id": user_id, "verified_groups": groups}
            ),
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def _post_jsonrpc(
    gateway_url: str, token: str, payload: dict, extra_headers: dict | None = None
) -> tuple[int, dict]:
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }
    if extra_headers:
        headers.update(extra_headers)
    response = requests.post(gateway_url, headers=headers, json=payload, timeout=120)
    return response.status_code, response.json() if response.content else {}


def _tool_call_text(body: dict) -> str:
    content = body.get("result", {}).get("content", [])
    return content[0].get("text", "") if content else ""


def main() -> int:
    region = os.environ.get("AWS_REGION", "us-east-1")
    stack = os.environ.get("STACK_NAME", "fast-aws-specialist")
    user_id = os.environ.get("VERIFIED_USER_ID", "ltm-e2e-test-user")

    gateway_url = _get_ssm(stack, "gateway_url", region)
    client_id = _get_ssm(stack, "machine_client_id", region)
    cognito_provider = _get_ssm(stack, "cognito_provider", region)
    secret = _get_secret(f"/{stack}/machine_client_secret", region)
    token_url = f"https://{cognito_provider}/oauth2/token"

    print(f"Gateway : {gateway_url}")
    print(f"Stack   : {stack} / user_id: {user_id}")
    print()

    print("[1/4] Fetching M2M token (finance via verified_groups)...")
    token = _fetch_token(token_url, client_id, secret, user_id, "finance")
    print("      OK")
    print()

    print("[2/4] tools/list ...")
    status, body = _post_jsonrpc(
        gateway_url, token, {"jsonrpc": "2.0", "id": "list", "method": "tools/list"}
    )
    tools = [t["name"] for t in body.get("result", {}).get("tools", [])]
    listed = TOOL_NAME in tools
    print(f"      HTTP {status}, total tools={len(tools)}")
    print(f"      {TOOL_NAME}: {'LISTED' if listed else 'MISSING'}")
    if not listed:
        print(f"      tools: {tools}")
    print()

    print("[3/4] tools/call WITH actor-id header ...")
    status, body = _post_jsonrpc(
        gateway_url,
        token,
        {
            "jsonrpc": "2.0",
            "id": "call-with-header",
            "method": "tools/call",
            "params": {"name": TOOL_NAME, "arguments": {}},
        },
        extra_headers={ACTOR_HEADER: user_id},
    )
    text = _tool_call_text(body)
    print(f"      HTTP {status}")
    print(f"      text: {text[:300]}")
    with_header_ok = (
        status == 200
        and "error" not in body
        and not body.get("result", {}).get("isError", False)
        and '"facts"' in text
    )
    print(f"      -> {'OK' if with_header_ok else 'FAIL'}")
    print()

    print("[4/4] tools/call WITHOUT actor-id header (expect identity error) ...")
    status, body = _post_jsonrpc(
        gateway_url,
        token,
        {
            "jsonrpc": "2.0",
            "id": "call-no-header",
            "method": "tools/call",
            "params": {"name": TOOL_NAME, "arguments": {}},
        },
    )
    text = _tool_call_text(body)
    print(f"      HTTP {status}")
    print(f"      text: {text[:300]}")
    no_header_ok = status == 200 and "Missing actor identity header" in text
    print(f"      -> {'OK (rejected as expected)' if no_header_ok else 'FAIL'}")
    print()

    print("=" * 60)
    print(f" tools/list lists {TOOL_NAME} : {'OK' if listed else 'FAIL'}")
    print(f" call with header -> facts     : {'OK' if with_header_ok else 'FAIL'}")
    print(f" call without header -> error  : {'OK' if no_header_ok else 'FAIL'}")
    print("=" * 60)

    return 0 if (listed and with_header_ok and no_header_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
