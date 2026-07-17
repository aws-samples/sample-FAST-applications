#!/usr/bin/env python3

"""
E2E verification for the strands-mcp gateway target.

Hits the AgentCore Gateway with an M2M token elevated to the finance
department (same path as test-ltm-mcp.py), then:
  1. tools/list  -> confirm strands-mcp___search_docs and
     strands-mcp___fetch_doc are listed
  2. tools/call search_docs(query="bedrock model") -> expect ranked results
     (the upstream server queries strandsagents.com over the internet, so
     this also proves the public-network runtime can reach it)

No actor-id header is needed: these doc tools are not scoped to a user.

Usage:
    STACK_NAME=fast-aws-specialist uv run test-scripts/test-strands-mcp.py
"""

from __future__ import annotations

import json
import os
import sys

import boto3
import requests

SEARCH_TOOL = "strands-mcp___search_docs"
FETCH_TOOL = "strands-mcp___fetch_doc"


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
    claims Cedar evaluates; finance is permitted by 05-strands-mcp.cedar.
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


def _post_jsonrpc(gateway_url: str, token: str, payload: dict) -> tuple[int, dict]:
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }
    response = requests.post(gateway_url, headers=headers, json=payload, timeout=120)
    return response.status_code, response.json() if response.content else {}


def _tool_call_text(body: dict) -> str:
    content = body.get("result", {}).get("content", [])
    return content[0].get("text", "") if content else ""


def main() -> int:
    region = os.environ.get("AWS_REGION", "us-east-1")
    stack = os.environ.get("STACK_NAME", "fast-aws-specialist")
    user_id = os.environ.get("VERIFIED_USER_ID", "strands-e2e-test-user")

    gateway_url = _get_ssm(stack, "gateway_url", region)
    client_id = _get_ssm(stack, "machine_client_id", region)
    cognito_provider = _get_ssm(stack, "cognito_provider", region)
    secret = _get_secret(f"/{stack}/machine_client_secret", region)
    token_url = f"https://{cognito_provider}/oauth2/token"

    print(f"Gateway : {gateway_url}")
    print(f"Stack   : {stack} / user_id: {user_id}")
    print()

    print("[1/3] Fetching M2M token (finance via verified_groups)...")
    token = _fetch_token(token_url, client_id, secret, user_id, "finance")
    print("      OK")
    print()

    print("[2/3] tools/list ...")
    status, body = _post_jsonrpc(
        gateway_url, token, {"jsonrpc": "2.0", "id": "list", "method": "tools/list"}
    )
    tools = [t["name"] for t in body.get("result", {}).get("tools", [])]
    search_listed = SEARCH_TOOL in tools
    fetch_listed = FETCH_TOOL in tools
    print(f"      HTTP {status}, total tools={len(tools)}")
    print(f"      {SEARCH_TOOL}: {'LISTED' if search_listed else 'MISSING'}")
    print(f"      {FETCH_TOOL}: {'LISTED' if fetch_listed else 'MISSING'}")
    if not (search_listed and fetch_listed):
        print(f"      tools: {tools}")
    print()

    print("[3/3] tools/call search_docs(query='bedrock model') ...")
    status, body = _post_jsonrpc(
        gateway_url,
        token,
        {
            "jsonrpc": "2.0",
            "id": "call-search",
            "method": "tools/call",
            "params": {
                "name": SEARCH_TOOL,
                "arguments": {"query": "bedrock model", "k": 3},
            },
        },
    )
    text = _tool_call_text(body)
    print(f"      HTTP {status}")
    print(f"      text: {text[:300]}")
    search_ok = (
        status == 200
        and "error" not in body
        and not body.get("result", {}).get("isError", False)
        and "strandsagents.com" in text
    )
    print(f"      -> {'OK' if search_ok else 'FAIL'}")
    print()

    print("=" * 60)
    print(f" tools/list lists search_docs  : {'OK' if search_listed else 'FAIL'}")
    print(f" tools/list lists fetch_doc    : {'OK' if fetch_listed else 'FAIL'}")
    print(f" search_docs -> results        : {'OK' if search_ok else 'FAIL'}")
    print("=" * 60)

    return 0 if (search_listed and fetch_listed and search_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
