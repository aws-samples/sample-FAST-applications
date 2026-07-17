#!/usr/bin/env python3

"""
E2E verification for the web-search-tool gateway target.

Hits the AgentCore Gateway with an M2M token (same path as
test-strands-mcp.py), then:
  1. tools/list  -> confirm web-search-tool___WebSearch is listed
  2. tools/call WebSearch(query=...) as finance -> expect results with
     URLs/titles (Cedar 06-web-search.cedar permits finance)
  3. tools/call WebSearch(query=...) as guest -> expect Cedar deny
     (deny-by-default: guest is not in the permit's department set)

No actor-id header is needed: Web Search is not scoped to a user.

Usage:
    STACK_NAME=fast-aws-specialist uv run test-scripts/test-web-search.py
"""

from __future__ import annotations

import json
import os
import sys

import boto3
import requests

SEARCH_TOOL = "web-search-tool___WebSearch"


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
    claims Cedar evaluates; finance/engineering are permitted by
    06-web-search.cedar, guest is not.
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


def _is_denied(status: int, body: dict) -> bool:
    """Cedar deny surfaces as a JSON-RPC error mentioning authorization."""
    if status == 200 and "error" not in body:
        return False
    blob = json.dumps(body).lower()
    return any(
        kw in blob for kw in ("not authorized", "access denied", "forbidden", "denied")
    )


def main() -> int:
    region = os.environ.get("AWS_REGION", "us-east-1")
    stack = os.environ.get("STACK_NAME", "fast-aws-specialist")
    user_id = os.environ.get("VERIFIED_USER_ID", "web-search-e2e-test-user")

    gateway_url = _get_ssm(stack, "gateway_url", region)
    client_id = _get_ssm(stack, "machine_client_id", region)
    cognito_provider = _get_ssm(stack, "cognito_provider", region)
    secret = _get_secret(f"/{stack}/machine_client_secret", region)
    token_url = f"https://{cognito_provider}/oauth2/token"

    print(f"Gateway : {gateway_url}")
    print(f"Stack   : {stack} / user_id: {user_id}")
    print()

    # ---- finance: tools/list + tools/call should succeed ----
    print("[1/4] Fetching M2M token (finance via verified_groups)...")
    fin_token = _fetch_token(token_url, client_id, secret, user_id, "finance")
    print("      OK")
    print()

    print("[2/4] tools/list ...")
    status, body = _post_jsonrpc(
        gateway_url, fin_token, {"jsonrpc": "2.0", "id": "list", "method": "tools/list"}
    )
    tools = [t["name"] for t in body.get("result", {}).get("tools", [])]
    search_listed = SEARCH_TOOL in tools
    print(f"      HTTP {status}, total tools={len(tools)}")
    print(f"      {SEARCH_TOOL}: {'LISTED' if search_listed else 'MISSING'}")
    if not search_listed:
        print(f"      tools: {tools}")
    print()

    print(
        "[3/4] tools/call WebSearch(query='Amazon Bedrock AgentCore latest features') as finance ..."
    )
    status, body = _post_jsonrpc(
        gateway_url,
        fin_token,
        {
            "jsonrpc": "2.0",
            "id": "call-search",
            "method": "tools/call",
            "params": {
                "name": SEARCH_TOOL,
                "arguments": {
                    "query": "Amazon Bedrock AgentCore latest features",
                    "maxResults": 3,
                },
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
        # The response packs results as JSON; a URL/title implies real hits.
        and ("url" in text.lower() or "title" in text.lower() or "http" in text.lower())
    )
    print(f"      -> {'OK' if search_ok else 'FAIL'}")
    print()

    # ---- guest: tools/call should be denied by Cedar ----
    print("[4/4] tools/call WebSearch as guest -> expect Cedar deny ...")
    guest_token = _fetch_token(token_url, client_id, secret, user_id, "guest")
    status, body = _post_jsonrpc(
        gateway_url,
        guest_token,
        {
            "jsonrpc": "2.0",
            "id": "call-guest",
            "method": "tools/call",
            "params": {
                "name": SEARCH_TOOL,
                "arguments": {"query": "test", "maxResults": 1},
            },
        },
    )
    guest_denied = _is_denied(status, body)
    print(f"      HTTP {status}")
    print(f"      body: {json.dumps(body)[:300]}")
    print(f"      -> {'DENIED (OK)' if guest_denied else 'NOT DENIED (FAIL)'}")
    print()

    print("=" * 60)
    print(f" tools/list lists WebSearch     : {'OK' if search_listed else 'FAIL'}")
    print(f" finance WebSearch -> results   : {'OK' if search_ok else 'FAIL'}")
    print(f" guest WebSearch -> Cedar deny  : {'OK' if guest_denied else 'FAIL'}")
    print("=" * 60)

    return 0 if (search_listed and search_ok and guest_denied) else 1


if __name__ == "__main__":
    sys.exit(main())
