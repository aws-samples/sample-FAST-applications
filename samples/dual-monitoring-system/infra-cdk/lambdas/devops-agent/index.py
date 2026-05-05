"""
Proxy Lambda for the AWS DevOps Agent webhook.

Routes:
  POST /incident      — sign + forward incident to DevOps Agent webhook
  GET  /investigations — fetch investigation results from DevOps Agent API
"""

import base64
import hashlib
import hmac as hmac_lib
import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ssm = boto3.client("ssm")
secretsmanager = boto3.client("secretsmanager")

AGENT_SPACE_ID = os.environ.get("AGENT_SPACE_ID", "")
CORS_ALLOWED_ORIGIN = os.environ.get("CORS_ALLOWED_ORIGIN", "*")
DEVOPS_API_BASE = f"https://event-ai.{os.environ.get('AWS_REGION', 'us-east-1')}.api.aws"
DEVOPS_IAM_SERVICE = "execute-api"


def cors_response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": CORS_ALLOWED_ORIGIN,
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
        },
        "body": json.dumps(body),
    }


def _validate_https_url(url: str) -> None:
    """Ensure the URL uses the HTTPS scheme."""
    if not url.lower().startswith("https://"):
        raise ValueError(f"Only HTTPS URLs are allowed, got: {url}")


def signed_request(method: str, url: str, body: bytes = None) -> tuple[int, str]:
    """Make a SigV4-signed request to the DevOps Agent API."""
    _validate_https_url(url)

    session = boto3.Session()
    credentials = session.get_credentials().get_frozen_credentials()
    region = os.environ.get("AWS_REGION", "us-east-1")

    headers = {"Content-Type": "application/json"}
    aws_req = AWSRequest(method=method, url=url, data=body, headers=headers)
    SigV4Auth(credentials, DEVOPS_IAM_SERVICE, region).add_auth(aws_req)

    http_req = urllib.request.Request(
        url, data=body, headers=dict(aws_req.headers), method=method
    )
    try:
        with urllib.request.urlopen(http_req, timeout=10) as resp:  # noqa: S310
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def fetch_investigations() -> dict:
    """Fetch recent investigations from the DevOps Agent API."""
    url = f"{DEVOPS_API_BASE}/v1/agent-spaces/{AGENT_SPACE_ID}/investigations"
    status, body = signed_request("GET", url)
    logger.info(f"Investigations API: HTTP {status}")
    if status == 200:
        try:
            return {"ok": True, "data": json.loads(body)}
        except json.JSONDecodeError:
            return {"ok": True, "data": body}
    return {"ok": False, "httpStatus": status, "raw": body}


def handler(event: dict, context) -> dict:
    method = event.get("httpMethod", "")
    path = event.get("path", "")
    logger.info(f"{method} {path}")

    if method == "OPTIONS":
        return cors_response(200, {})

    # ── GET /investigations ────────────────────────────────────────────────────
    if method == "GET" and path.endswith("investigations"):
        result = fetch_investigations()
        return cors_response(200, result)

    # ── POST /incident ─────────────────────────────────────────────────────────
    if method != "POST":
        return cors_response(405, {"error": "Method not allowed"})

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return cors_response(400, {"error": "Invalid JSON body"})

    # Fetch webhook URL from SSM
    webhook_url_param = os.environ["WEBHOOK_URL_PARAM"]
    try:
        webhook_url = ssm.get_parameter(Name=webhook_url_param)["Parameter"]["Value"]
    except Exception as e:
        logger.error(f"SSM read failed: {e}")
        return cors_response(500, {"error": "Could not retrieve webhook URL"})

    # Fetch webhook secret from Secrets Manager
    secret_arn = os.environ["WEBHOOK_SECRET_ARN"]
    try:
        webhook_secret = secretsmanager.get_secret_value(SecretId=secret_arn)["SecretString"]
    except Exception as e:
        logger.error(f"Secrets Manager read failed: {e}")
        return cors_response(500, {"error": "Could not retrieve webhook secret"})

    # Build incident payload
    timestamp = datetime.now(timezone.utc).isoformat()
    incident = {
        "eventType": "incident",
        "incidentId": f"incident-{int(datetime.now(timezone.utc).timestamp() * 1000)}",
        "action": "created",
        "priority": body.get("priority", "HIGH"),
        "title": body.get("title", ""),
        "description": body.get("description", ""),
        "timestamp": timestamp,
        "service": body.get("service", "MyTestService"),
        "data": {
            "metadata": {
                "region": os.environ.get("AWS_REGION", "us-east-1"),
                "environment": "production",
            }
        },
    }

    payload = json.dumps(incident)

    # HMAC-SHA256 sign
    message = f"{timestamp}:{payload}".encode("utf-8")
    signature = base64.b64encode(
        hmac_lib.new(webhook_secret.encode("utf-8"), message, hashlib.sha256).digest()
    ).decode("utf-8")

    logger.info(f"Forwarding to webhook: {webhook_url}")

    _validate_https_url(webhook_url)

    req = urllib.request.Request(
        webhook_url,
        data=payload.encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-amzn-event-timestamp": timestamp,
            "x-amzn-event-signature": signature,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            webhook_body = resp.read().decode("utf-8")
            webhook_status = resp.status
    except urllib.error.HTTPError as e:
        webhook_body = e.read().decode("utf-8")
        webhook_status = e.code
    except Exception as e:
        logger.error(f"Webhook call failed: {e}")
        return cors_response(502, {"error": str(e)})

    logger.info(f"Webhook responded: HTTP {webhook_status}")

    # After firing, immediately try to fetch any existing investigations
    investigations = fetch_investigations()

    return cors_response(200, {
        "webhookStatus": webhook_status,
        "webhookResponse": webhook_body,
        "investigations": investigations,
        "incidentId": incident["incidentId"],
    })
