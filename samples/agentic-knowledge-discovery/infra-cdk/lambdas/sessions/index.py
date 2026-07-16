# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Sessions API Lambda (DynamoDB-backed, strongly consistent).

Persists and serves the conversation transcript the frontend renders, so a
resumed session restores the exact view. The sidebar list reads the base table
with a strongly-consistent query (no GSI), so a save or delete is reflected
immediately on the next read.

Shared table item shapes (pk / sk):
- USER#<userId>  / SESS#<sessionId>  -> { userId, title, updatedAt }
- SESSION#<id>   / MSG#<index>       -> { userId, data }  (one message object, JSON)

Routes (Cognito-authorized; user comes from the JWT `sub`, never the path):
- GET    /sessions                 list the caller's sessions, newest first
- GET    /sessions/{sessionId}     return a session's messages, in order
- PUT    /sessions/{sessionId}     replace a session's messages + index row
- DELETE /sessions/{sessionId}     delete a session
"""

import json
import os
from typing import Any, Dict, List

import boto3
from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.event_handler import APIGatewayRestResolver, CORSConfig
from aws_lambda_powertools.logging.correlation_paths import API_GATEWAY_REST
from aws_lambda_powertools.utilities.typing import LambdaContext
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

TABLE_NAME = os.environ["TABLE_NAME"]
CORS_ALLOWED_ORIGINS = os.environ.get("CORS_ALLOWED_ORIGINS", "*")

cors_origins = [o.strip() for o in CORS_ALLOWED_ORIGINS.split(",") if o.strip()]
primary_origin = cors_origins[0] if cors_origins else "*"
extra_origins = cors_origins[1:] if len(cors_origins) > 1 else None

cors_config = CORSConfig(
    allow_origin=primary_origin,
    extra_origins=extra_origins,
    allow_headers=["Content-Type", "Authorization"],
    allow_credentials=True,
)

table = boto3.resource("dynamodb").Table(TABLE_NAME)

tracer = Tracer()
logger = Logger()
app = APIGatewayRestResolver(cors=cors_config)

MAX_MESSAGES = 500


def _caller() -> str:
    """The Cognito sub of the authenticated caller."""
    authorizer = app.current_event.request_context.authorizer
    claims = authorizer.get("claims", {}) if authorizer else {}
    return claims.get("sub") if claims else None


def _user_pk(user_id: str) -> str:
    return f"USER#{user_id}"


def _session_pk(session_id: str) -> str:
    return f"SESSION#{session_id}"


@app.get("/sessions")
def list_sessions() -> Dict[str, Any]:
    user_id = _caller()
    if not user_id:
        return {"error": "Unauthorized"}, 401

    resp = table.query(
        KeyConditionExpression=Key("pk").eq(_user_pk(user_id))
        & Key("sk").begins_with("SESS#"),
        ConsistentRead=True,
    )
    sessions = [
        {
            "sessionId": item["sk"].split("#", 1)[1],
            "title": item.get("title", "New conversation"),
            "lastActivity": item.get("updatedAt"),
        }
        for item in resp.get("Items", [])
    ]
    sessions.sort(key=lambda s: s.get("lastActivity") or "", reverse=True)
    return {"sessions": sessions}


@app.get("/sessions/<session_id>")
def get_session(session_id: str) -> Dict[str, Any]:
    user_id = _caller()
    if not user_id:
        return {"error": "Unauthorized"}, 401

    resp = table.query(
        KeyConditionExpression=Key("pk").eq(_session_pk(session_id))
        & Key("sk").begins_with("MSG#"),
        ConsistentRead=True,
    )
    messages: List[Any] = []
    for item in resp.get("Items", []):
        if item.get("userId") and item["userId"] != user_id:
            return {"error": "Forbidden"}, 403
        try:
            messages.append(json.loads(item["data"]))
        except (KeyError, ValueError):
            continue
    return {"sessionId": session_id, "messages": messages}


@app.put("/sessions/<session_id>")
def save_session(session_id: str) -> Dict[str, Any]:
    user_id = _caller()
    if not user_id:
        return {"error": "Unauthorized"}, 401

    body = app.current_event.json_body or {}
    messages = body.get("messages", [])[:MAX_MESSAGES]
    title = (body.get("title") or "New conversation")[:80]
    now = body.get("updatedAt") or ""

    # Session index row under the user's partition (drives the sidebar).
    table.put_item(
        Item={
            "pk": _user_pk(user_id),
            "sk": f"SESS#{session_id}",
            "userId": user_id,
            "title": title,
            "updatedAt": now,
        }
    )
    # Messages under the session's partition.
    with table.batch_writer() as batch:
        for i, message in enumerate(messages):
            batch.put_item(
                Item={
                    "pk": _session_pk(session_id),
                    "sk": f"MSG#{i:06d}",
                    "userId": user_id,
                    "data": json.dumps(message),
                }
            )
    return {"success": True, "saved": len(messages)}


@app.delete("/sessions/<session_id>")
def delete_session(session_id: str) -> Dict[str, Any]:
    user_id = _caller()
    if not user_id:
        return {"error": "Unauthorized"}, 401

    # Remove the sidebar index row first so the list reflects the delete at once.
    table.delete_item(Key={"pk": _user_pk(user_id), "sk": f"SESS#{session_id}"})

    # Remove the session's message rows.
    resp = table.query(
        KeyConditionExpression=Key("pk").eq(_session_pk(session_id)),
        ConsistentRead=True,
    )
    items = resp.get("Items", [])
    if any(it.get("userId") and it["userId"] != user_id for it in items):
        return {"error": "Forbidden"}, 403
    with table.batch_writer() as batch:
        for it in items:
            batch.delete_item(Key={"pk": it["pk"], "sk": it["sk"]})
    return {"success": True, "deleted": len(items)}


@logger.inject_lambda_context(correlation_id_path=API_GATEWAY_REST)
def handler(event: dict, context: LambdaContext) -> dict:
    try:
        return app.resolve(event, context)
    except ClientError as e:
        logger.error("DynamoDB error: %s", e.response["Error"]["Message"])
        return {"statusCode": 500, "body": '{"error": "Internal server error"}'}
