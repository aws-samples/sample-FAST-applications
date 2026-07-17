"""Single-tool MCP server: list the current user's long-term memory facts.

Hosted on AgentCore Runtime (protocol: MCP, inbound auth: IAM/SigV4) and
registered as an MCP server target of the AgentCore Gateway. The
tool logic mirrors the in-process Strands tool this server replaces
(agent/strands-single-agent/tools/long_term_memory.py): a
no-query ListMemoryRecords over the actor's /facts namespace, because the
per-turn semantic retrieval misses meta questions like "what do you remember
about me?".

Actor identity: the Gateway does not forward inbound JWT claims to MCP server
targets, so the agent runtime sends the authenticated user's ID in the
X-Amzn-Bedrock-AgentCore-Runtime-Custom-Actor-Id header (the only header
prefix AgentCore allows through to runtime containers). It passes the
Gateway target's metadataConfiguration.allowedRequestHeaders and this
runtime's requestHeaderConfiguration.allowlistedHeaders. The value's trust
root is the same as the existing aws_client_metadata flow: a self-declaration
by the runtime that authenticated with the machine client secret.

AgentCore Runtime MCP contract: host 0.0.0.0, port 8000, path /mcp,
stateless streamable HTTP. The platform injects an Mcp-Session-Id header for
microVM stickiness; FastMCP in stateless mode accepts it.
"""

import json
import logging
import os

# The ignore covers the missing stubs when the server dependencies are not on
# the type-checker's path; unit tests stub both modules before import.
from bedrock_agentcore.memory.session import (  # type: ignore[import-not-found]
    MemorySessionManager,
)
from mcp.server.fastmcp import Context, FastMCP  # type: ignore[import-not-found]

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_MAX_RECORDS = 100
# Starlette normalizes header names to lowercase.
_ACTOR_ID_HEADER = "x-amzn-bedrock-agentcore-runtime-custom-actor-id"

mcp = FastMCP("ltm", host="0.0.0.0", stateless_http=True)

_manager: MemorySessionManager | None = None


def _get_manager() -> MemorySessionManager:
    """Return the shared MemorySessionManager, creating it on first use.

    Lazy so that tools/list (issued by the Gateway at target-creation time)
    succeeds even before MEMORY_ID is exercised, and so a misconfiguration
    surfaces as a tool error rather than a container crash loop.

    Returns:
        The shared MemorySessionManager bound to MEMORY_ID.

    Raises:
        ValueError: If the MEMORY_ID environment variable is not set.
    """
    global _manager
    if _manager is None:
        memory_id = os.environ.get("MEMORY_ID")
        if not memory_id:
            raise ValueError("MEMORY_ID environment variable is required")
        _manager = MemorySessionManager(
            memory_id=memory_id,
            region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        )
    return _manager


def _extract_actor_id(ctx: Context) -> str:
    """Extract the actor (user) ID from the propagated custom header.

    Args:
        ctx: The FastMCP request context; its request_context.request is the
            underlying HTTP request when served over streamable HTTP.

    Returns:
        The actor ID, or an empty string when the header is absent.
    """
    request = ctx.request_context.request
    if request is None:
        return ""
    return str(request.headers.get(_ACTOR_ID_HEADER, ""))


def list_memories_for_actor(actor_id: str) -> str:
    """List all stored long-term memory facts for one actor.

    Args:
        actor_id: The user whose /facts namespace is listed. Callers must
            pass only an identity derived from the propagated header, never
            a model-controlled value.

    Returns:
        JSON string with the stored facts.
    """
    records = _get_manager().list_long_term_memory_records(
        namespace=f"/facts/{actor_id}",
        max_results=_MAX_RECORDS,
    )
    facts = []
    for record in records:
        content = record.get("content", {})
        text = content.get("text", "").strip() if isinstance(content, dict) else ""
        if text:
            facts.append(
                {
                    "fact": text,
                    "created_at": str(record.get("createdAt", "")),
                }
            )
    logger.info("list_long_term_memories returned %d facts", len(facts))
    if not facts:
        return json.dumps(
            {
                "facts": [],
                "note": (
                    "No long-term memories stored yet. Facts are extracted "
                    "asynchronously, so very recent conversations may not "
                    "appear for a few minutes."
                ),
            }
        )
    return json.dumps({"facts": facts}, ensure_ascii=False)


@mcp.tool()  # type: ignore[untyped-decorator]
def list_long_term_memories(ctx: Context) -> str:
    """List all LONG-TERM memory facts stored about the current user.

    Long-term memory holds facts extracted across past sessions (not the
    current conversation's history, which is short-term memory and already
    in your context). Use this when the user asks what you remember about
    them, asks you to summarize or explain past conversations, or otherwise
    refers to their cross-session memory as a whole. Returns ALL stored
    facts (no relevance filtering), unlike the automatic per-turn
    memory retrieval.

    Returns:
        JSON string with the stored facts.
    """
    actor_id = _extract_actor_id(ctx)
    if not actor_id:
        # The header is attached by the agent runtime's gateway MCP client;
        # its absence means a caller outside that path (or a propagation
        # misconfiguration), so there is no actor to scope the listing to.
        return json.dumps(
            {
                "error": (
                    "Missing actor identity header; cannot determine whose "
                    "memories to list."
                )
            }
        )
    return list_memories_for_actor(actor_id)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
