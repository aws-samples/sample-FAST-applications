# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Chat History API Lambda Handler.

Restores a past conversation's message body for the frontend by reading the
AgentCore Memory short-term events for a given session. The ``actorId`` is
ALWAYS the Cognito ``sub`` from the validated JWT (taken from the API Gateway
authorizer claims), never from the request — this prevents a user from reading
another user's history by passing a forged actorId.

Endpoint:
  GET /history?sessionId=<id>
    -> 200 {"status": "ok",      "messages": [{role, content, timestamp}, ...]}
    -> 200 {"status": "expired", "messages": []}   # session has no conversational
                                                    # events (Memory expiry 30d /
                                                    # empty-session 1d deletion)

The "expired" case is a normal-status 200 (not a 4xx/5xx) so the UI can show a
calm "history unavailable" notice rather than a red error banner.
"""

import json
import os
from typing import Any, Dict, List, Optional

import boto3
from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.event_handler import APIGatewayRestResolver, CORSConfig
from aws_lambda_powertools.logging.correlation_paths import API_GATEWAY_REST
from aws_lambda_powertools.utilities.typing import LambdaContext
from botocore.exceptions import ClientError

# Environment variables
MEMORY_ID = os.environ["MEMORY_ID"]
CORS_ALLOWED_ORIGINS = os.environ.get("CORS_ALLOWED_ORIGINS", "*")

# list_events returns at most 100 per call; page until nextToken is exhausted.
PAGE_SIZE = 100
# Safety cap so a pathological session can't make us page forever.
MAX_PAGES = 50

# Parse CORS origins - can be comma-separated list (mirrors feedback Lambda).
cors_origins = [
    origin.strip() for origin in CORS_ALLOWED_ORIGINS.split(",") if origin.strip()
]
primary_origin = cors_origins[0] if cors_origins else "*"
extra_origins = cors_origins[1:] if len(cors_origins) > 1 else None

cors_config = CORSConfig(
    allow_origin=primary_origin,
    extra_origins=extra_origins,
    allow_headers=["Content-Type", "Authorization"],
    allow_credentials=True,
)

# AgentCore Memory data-plane client (the "bedrock-agentcore" service, not control).
agentcore = boto3.client("bedrock-agentcore")

tracer = Tracer()
logger = Logger()
app = APIGatewayRestResolver(cors=cors_config)

MAX_SESSION_ID_LENGTH = 100

# Conversation roles we surface to the UI. The session manager also writes
# SESSION / AGENT state as blob payloads (not conversational), which we ignore
# here — only conversational text is part of the visible transcript.
_ROLE_MAP = {"USER": "user", "ASSISTANT": "assistant"}


def _extract_text(message: Dict[str, Any]) -> str:
    """Join the text blocks of a Strands message, skipping tool blocks.

    A Strands message's ``content`` is a list of blocks; only ``text`` blocks
    are part of the visible transcript (toolUse / toolResult blocks are dropped
    for the initial version — tool rendering is not restored in the UI).
    """
    parts = [
        block["text"]
        for block in message.get("content", [])
        if isinstance(block, dict) and isinstance(block.get("text"), str)
    ]
    return "".join(parts).strip()


def _decode_session_message(raw_text: str) -> Optional[Dict[str, Any]]:
    """Decode the SessionMessage JSON that the session manager stores in text.

    The Strands AgentCoreMemorySessionManager does NOT store plain text in
    ``conversational.content.text``; it stores ``json.dumps(session_message
    .to_dict())`` there (see bedrock_converter.message_to_payload). So the text
    field is a JSON document like::

        {"message": {"role": "user", "content": [{"text": "..."}]},
         "message_id": 0, "created_at": "...", ...}

    Return the inner ``message`` dict, or None if the text is not such a
    document (e.g. a legacy plain-text event) so the caller can fall back.
    """
    try:
        parsed = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError):
        return None
    if isinstance(parsed, dict) and isinstance(parsed.get("message"), dict):
        return parsed["message"]
    return None


def _get_actor_id() -> Optional[str]:
    """Return the Cognito sub from the authorizer claims, or None if absent."""
    request_context = app.current_event.request_context
    authorizer = request_context.authorizer
    claims = authorizer.get("claims", {}) if authorizer else {}
    return claims.get("sub") if claims else None


def _events_to_messages(events: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Flatten AgentCore Memory events into UI messages in chronological order.

    list_events returns newest-first, so the caller passes events already
    reversed. Each event's payload is a list of tagged-union entries. We keep
    only ``conversational`` entries; the session manager stores the full
    SessionMessage as JSON inside ``content.text`` (NOT plain text), so we
    decode it and extract the visible text blocks. Tool blocks and messages
    that are empty after dropping tool blocks (e.g. a pure toolUse turn) are
    skipped. SESSION / AGENT state is written as ``blob`` payloads and ignored.
    """
    messages: List[Dict[str, str]] = []
    for event in events:
        timestamp = event.get("eventTimestamp")
        ts_iso = timestamp.isoformat() if timestamp is not None else ""
        for entry in event.get("payload", []):
            conv = entry.get("conversational")
            if not conv:
                continue
            raw_text = (conv.get("content") or {}).get("text", "")

            inner = _decode_session_message(raw_text)
            if inner is not None:
                # Prefer the role stored in the SessionMessage (lower-case),
                # falling back to the conversational role (upper-case).
                role = inner.get("role") or _ROLE_MAP.get(conv.get("role", ""))
                text = _extract_text(inner)
            else:
                # Legacy / plain-text event: use the text and role as-is.
                role = _ROLE_MAP.get(conv.get("role", ""))
                text = raw_text.strip()

            if role not in ("user", "assistant") or not text:
                continue
            messages.append({"role": role, "content": text, "timestamp": ts_iso})
    return messages


@app.get("/history")
def get_history() -> Dict[str, Any]:
    """Handle GET /history?sessionId=... — restore a session's transcript."""
    actor_id = _get_actor_id()
    if not actor_id:
        return {"error": "Unauthorized"}, 401

    session_id = app.current_event.get_query_string_value(name="sessionId")
    if not session_id or len(session_id) > MAX_SESSION_ID_LENGTH:
        return {"error": "sessionId is required"}, 400
    if not session_id.replace("-", "").replace("_", "").isalnum():
        return {"error": "sessionId has invalid characters"}, 400

    try:
        all_events: List[Dict[str, Any]] = []
        next_token: Optional[str] = None
        for _ in range(MAX_PAGES):
            kwargs: Dict[str, Any] = {
                "memoryId": MEMORY_ID,
                "actorId": actor_id,
                "sessionId": session_id,
                "includePayloads": True,
                "maxResults": PAGE_SIZE,
            }
            if next_token:
                kwargs["nextToken"] = next_token
            response = agentcore.list_events(**kwargs)
            all_events.extend(response.get("events", []))
            next_token = response.get("nextToken")
            if not next_token:
                break

        # list_events is newest-first; reverse to chronological order.
        messages = _events_to_messages(list(reversed(all_events)))

        if not messages:
            # Either the session never had conversational turns, or its events
            # aged out (Memory expiry 30d / empty-session 1d deletion). The
            # index entry in DynamoDB may still exist; tell the UI to show a
            # "history unavailable" notice rather than an error.
            return {"status": "expired", "messages": []}

        return {"status": "ok", "messages": messages}

    except ClientError as e:
        logger.error(f"AgentCore Memory error: {e.response['Error']['Message']}")
        return {"error": "Internal server error"}, 500
    except Exception as e:
        logger.error(f"Error listing history: {str(e)}")
        return {"error": "Internal server error"}, 500


@logger.inject_lambda_context(correlation_id_path=API_GATEWAY_REST)
def handler(event: dict, context: LambdaContext) -> dict:
    """Lambda handler for the chat history API."""
    return app.resolve(event, context)
