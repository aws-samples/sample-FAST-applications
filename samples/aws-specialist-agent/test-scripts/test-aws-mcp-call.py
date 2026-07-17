#!/usr/bin/env python3

"""
Verification harness for the experiment of removing execute-api:Invoke
from the Gateway role (2026-05-29).

Hits the AgentCore Gateway with an M2M token elevated to the finance
department (so Cedar V2 permits the destructive AWS MCP tools), runs:
  1. tools/list  -> confirm aws-mcp___aws___* tools are still listed
  2. aws-mcp___aws___list_regions  -> confirm a non-destructive read tool
  3. aws-mcp___aws___call_aws("aws s3 ls") -> the real test (tools/call)

If all three return HTTP 200 with non-error JSON-RPC results, the
execute-api:Invoke statement is unnecessary and can be removed.
"""

from __future__ import annotations

import json
import os
import sys

import boto3
import requests


def _get_secret(secret_name: str, region: str) -> str:
    sm = boto3.client("secretsmanager", region_name=region)
    return sm.get_secret_value(SecretId=secret_name)["SecretString"]


def _get_ssm(stack: str, key: str, region: str) -> str:
    ssm = boto3.client("ssm", region_name=region)
    return ssm.get_parameter(Name=f"/{stack}/{key}")["Parameter"]["Value"]


def _fetch_token_with_metadata(
    token_url: str, client_id: str, client_secret: str, verified_user_id: str
) -> str:
    """Token request that mirrors agent/utils/auth.py:get_gateway_access_token().

    The pre-token Lambda reads clientMetadata.verified_user_id and assigns
    department=finance for the admin alias. We reuse that path so the
    Cedar V2 policy permits aws-mcp___aws___call_aws.
    """
    response = requests.post(
        token_url,
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "aws_client_metadata": json.dumps({"verified_user_id": verified_user_id}),
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def _post_jsonrpc(gateway_url: str, token: str, payload: dict) -> tuple[int, dict]:
    response = requests.post(
        gateway_url,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        json=payload,
        timeout=120,
    )
    return response.status_code, response.json() if response.content else {}


def main() -> int:
    region = os.environ.get("AWS_REGION", "us-east-1")
    stack = os.environ.get("STACK_NAME", "fast-aws-specialist")

    params = {
        "gateway_url": _get_ssm(stack, "gateway_url", region),
        "machine_client_id": _get_ssm(stack, "machine_client_id", region),
        "cognito_provider": _get_ssm(stack, "cognito_provider", region),
    }
    secret = _get_secret(f"/{stack}/machine_client_secret", region)
    token_url = f"https://{params['cognito_provider']}/oauth2/token"

    verified_user_id = os.environ.get("VERIFIED_USER_ID", "admin@example.com")

    print(f"Gateway     : {params['gateway_url']}")
    print(f"Token URL   : {token_url}")
    print(f"verified_user_id : {verified_user_id}")
    print()

    print("[1/3] Fetching access token (M2M + finance via verified_user_id)...")
    token = _fetch_token_with_metadata(
        token_url, params["machine_client_id"], secret, verified_user_id
    )
    print("      OK")
    print()

    print("[2/3] tools/list ...")
    status, body = _post_jsonrpc(
        params["gateway_url"],
        token,
        {"jsonrpc": "2.0", "id": "list", "method": "tools/list"},
    )
    tools = body.get("result", {}).get("tools", [])
    aws_mcp_tools = [t["name"] for t in tools if t["name"].startswith("aws-mcp___")]
    print(
        f"      HTTP {status}, total tools={len(tools)}, aws-mcp tools={len(aws_mcp_tools)}"
    )
    if aws_mcp_tools:
        print(f"      sample: {aws_mcp_tools[:3]}")
    print()

    print("[3/3] tools/call -> aws-mcp___aws___list_regions ...")
    status, body = _post_jsonrpc(
        params["gateway_url"],
        token,
        {
            "jsonrpc": "2.0",
            "id": "list_regions",
            "method": "tools/call",
            "params": {
                "name": "aws-mcp___aws___list_regions",
                "arguments": {"service_code": "lambda"},
            },
        },
    )
    print(f"      HTTP {status}")
    print(f"      body : {json.dumps(body)[:400]}")
    list_regions_ok = status == 200 and "error" not in body
    print()

    print("[4/4] tools/call -> aws-mcp___aws___call_aws (aws s3 ls) ...")
    status, body = _post_jsonrpc(
        params["gateway_url"],
        token,
        {
            "jsonrpc": "2.0",
            "id": "s3_ls",
            "method": "tools/call",
            "params": {
                "name": "aws-mcp___aws___call_aws",
                "arguments": {"cli_command": "aws s3 ls"},
            },
        },
    )
    print(f"      HTTP {status}")
    print(f"      body : {json.dumps(body)[:600]}")
    call_aws_ok = status == 200 and "error" not in body
    print()

    print("=" * 60)
    print(
        f" tools/list         : {'OK' if aws_mcp_tools else 'FAIL'} "
        f"(aws-mcp tools = {len(aws_mcp_tools)})"
    )
    print(f" list_regions       : {'OK' if list_regions_ok else 'FAIL'}")
    print(f" call_aws (s3 ls)   : {'OK' if call_aws_ok else 'FAIL'}")
    print("=" * 60)

    return 0 if (aws_mcp_tools and list_regions_ok and call_aws_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
