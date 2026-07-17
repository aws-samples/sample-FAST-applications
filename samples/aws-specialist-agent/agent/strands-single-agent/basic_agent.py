"""Strands agent with Gateway MCP tools, Memory, and Code Interpreter."""

import json
import logging
import os

from bedrock_agentcore.memory.integrations.strands.config import (
    AgentCoreMemoryConfig,
    RetrievalConfig,
)
from bedrock_agentcore.memory.integrations.strands.session_manager import (
    AgentCoreMemorySessionManager,
)
from bedrock_agentcore.runtime import BedrockAgentCoreApp, RequestContext
from models import ResolvedModel, build_model, resolve_model
from strands import Agent
from strands_tools import file_read
from strands_tools.code_interpreter import AgentCoreCodeInterpreter
from tools.gateway import create_gateway_mcp_client
from utils.auth import (
    extract_user_groups_from_context,
    extract_user_id_from_context,
)

logger = logging.getLogger(__name__)

app = BedrockAgentCoreApp()

# Path where the S3 Files skills access point is mounted (Phase 2b). Empty/missing
# when skills are disabled or the mount is not present, in which case the
# AgentSkills plugin is simply not attached.
SKILLS_MOUNT_PATH = os.environ.get("SKILLS_MOUNT_PATH", "")

# Concise principles over behavior enumeration (the
# prompt serves every selectable model, from Fable 5 down to Haiku 4.5), the
# AWS guidance section adapts the upstream Agent Toolkit for AWS rules
# (rules/aws-agent-rules.md) to this project's tools by capability, not exact
# name: naming the Gateway tools (which Cedar gates per department) would let a
# guest, whose tools/list excludes them, still read the names off the prompt and
# report them as available.
SYSTEM_PROMPT = """
You are an AWS specialist assistant. You have tools via the Gateway, a Code
Interpreter, a file_read tool, and a library of AWS skills.

# Skills first

When a question concerns a specific AWS service or task, first review
<available_skills> in your context. When a skill matches, call the `skills`
tool with its name to load the full SKILL.md instructions, then prefer its
guidance over your general knowledge. The skill response includes a Location
(the absolute path to the skill's SKILL.md, e.g. /mnt/skills/<name>/SKILL.md)
and a list of resource files under references/, scripts/, and assets/. The
`skills` tool does NOT read those files — when the SKILL.md tells you to
consult a reference file, use the `file_read` tool on its absolute path (the
skill directory is the Location's parent, so references/foo.md is
/mnt/skills/<name>/references/foo.md). If no local skill matches, check
whether a Gateway AWS skill-retrieval tool, if available to you, offers a
relevant skill.

# AWS guidance

- Prefer the Gateway's AWS MCP Server tools for AWS interactions (AWS
documentation search and retrieval, region and service availability lookups,
and sandboxed AWS CLI / boto3 execution) — they provide sandboxed execution,
observability, and audit logging.
- When uncertain about specific AWS details (API parameters, permissions,
limits, error codes), verify against documentation with the Gateway's AWS
documentation search and retrieval tools rather than guessing. State
uncertainty explicitly if you cannot confirm.
- Before running an operation that creates, modifies, or deletes AWS
resources (via a Gateway AWS CLI or script-execution tool), tell the user what
you are about to run and ask for confirmation. Read-only calls need no
confirmation.
- When creating infrastructure, prefer infrastructure as code (AWS CDK or
CloudFormation) over direct CLI commands, and follow AWS Well-Architected
Framework principles.
- Do not use em dashes in AWS resource names or descriptions; use hyphens
instead.

# Code Interpreter

Use it only to run code or compute a result. Do NOT use it to emit static
text, code, or diagrams, to reformat text, or to print something you could
just write in your reply.

# Tool routing

- When the user asks about this demo application itself (its architecture,
configuration, AWS services, or design decisions), use the
`fast-project-guide` skill and answer from its references rather than from
memory.
- For questions about building agents with the Strands Agents SDK, consult
the official Strands Agents documentation with the Gateway's Strands
documentation search and retrieval tools.
- For current or post-training-cutoff information (recent events, latest
releases, today's facts), use the Gateway's web search tool to ground your
answer in live web results. When you use a search result in your reply, you
MUST cite its source: show the title and URL of each result you draw on. This
citation is mandatory whenever the answer relies on web search.
- When the user asks what you remember about them or about past conversations
(across sessions), use the Gateway's long-term memory listing tool if
available and answer from its facts rather than claiming you have no memory.
- When asked about your tools or skills, describe only the tools actually
available to you in this session (the tools you can call right now, plus the
`skills` library) and explain what they do. Do NOT recite tool names from this
prompt or from any document: a tool you cannot call is not available to you,
and naming it would misrepresent what you can do.
"""


def _build_plugins() -> list:
    """Build the agent plugin list. Attaches AgentSkills when skills are mounted.

    The vendored skills are laid out flat under SKILLS_MOUNT_PATH
    (<skill-name>/SKILL.md), so a single path surfaces every skill. AgentSkills
    injects each skill's name/description into the system context and exposes a
    `skills` tool to activate (load) a skill's full instructions on demand.
    """
    plugins: list = []
    if SKILLS_MOUNT_PATH and os.path.isdir(SKILLS_MOUNT_PATH):
        # Imported lazily so the module still loads when running on an older
        # Strands that predates vended_plugins.skills.
        from strands import AgentSkills

        plugins.append(AgentSkills(skills=[SKILLS_MOUNT_PATH]))
        logger.info("AgentSkills enabled from %s", SKILLS_MOUNT_PATH)
    elif SKILLS_MOUNT_PATH:
        logger.warning(
            "SKILLS_MOUNT_PATH set but not a directory: %s", SKILLS_MOUNT_PATH
        )
    return plugins


def _create_session_manager(
    user_id: str, session_id: str
) -> AgentCoreMemorySessionManager:
    """Create an AgentCore memory session manager, optionally with long-term semantic retrieval.

    When the USE_LONG_TERM_MEMORY environment variable is "true", configures retrieval
    from the /facts/{actorId} namespace so the agent recalls facts across sessions.
    When false (default), only short-term memory (conversation history) is active,
    avoiding the additional storage and retrieval costs of long-term memory.

    Args:
        user_id: Unique identifier for the user (actor), extracted from the JWT sub claim.
        session_id: Unique identifier for the current conversation session.

    Returns:
        An AgentCoreMemorySessionManager bound to the user and session.
    """
    memory_id = os.environ.get("MEMORY_ID")
    if not memory_id:
        raise ValueError("MEMORY_ID environment variable is required")

    use_ltm = os.environ.get("USE_LONG_TERM_MEMORY", "false").lower() == "true"

    top_k = int(os.environ.get("LTM_TOP_K", "10"))
    relevance_score = float(os.environ.get("LTM_RELEVANCE_SCORE", "0.3"))

    # Only pass retrieval_config when LTM is explicitly enabled.
    # Omitting it means the session manager uses short-term memory only,
    # which avoids the $0.50/1,000 retrieval and $0.75/1,000 storage costs.
    retrieval_config = (
        {
            "/facts/{actorId}": RetrievalConfig(
                top_k=top_k,
                relevance_score=relevance_score,
            )
        }
        if use_ltm
        else None
    )

    config = AgentCoreMemoryConfig(
        memory_id=memory_id,
        session_id=session_id,
        actor_id=user_id,
        retrieval_config=retrieval_config,
    )
    return AgentCoreMemorySessionManager(
        agentcore_memory_config=config,
        region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
    )


def create_strands_agent(
    user_id: str, groups: list[str], session_id: str, resolved_model: ResolvedModel
) -> Agent:
    """Create a Strands agent with Gateway tools, memory, and Code Interpreter.

    Args:
        user_id: Unique identifier for the user (actor), from the JWT sub claim.
        groups: The user's Cognito groups, used for per-user Gateway access.
        session_id: Unique identifier for the current conversation session.
        resolved_model: The physical model id + provider the user selected,
            resolved and allowlist-validated by models.resolve_model.

    Returns:
        A configured Strands Agent bound to the user, session, and model.
    """

    bedrock_model = build_model(resolved_model)

    session_manager = _create_session_manager(user_id, session_id)

    region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    # Official Strands Code Interpreter tool, replacing the former
    # in-repo wrapper. Binding session_name to the conversation session id lets
    # its module-level cache reconnect to the same AgentCore sandbox across
    # invocations (warm reconnect vs cold create), which compounds with the
    # VPC cold-start mitigation.
    code_interpreter_tool = AgentCoreCodeInterpreter(
        region=region, session_name=session_id
    )

    gateway_client = create_gateway_mcp_client(user_id, groups)

    # file_read lets the agent read a skill's references/*.md from /mnt/skills
    # (the AgentSkills `skills` tool only lists them).
    # list_long_term_memories now arrives through the gateway: it
    # is hosted as the ltm-mcp MCP server target, scoped to the
    # caller via the actor-id header attached in tools/gateway.py.
    tools = [gateway_client, code_interpreter_tool.code_interpreter, file_read]

    return Agent(
        name="strands_agent",
        system_prompt=SYSTEM_PROMPT,
        tools=tools,
        plugins=_build_plugins(),
        model=bedrock_model,
        session_manager=session_manager,
        trace_attributes={"user.id": user_id, "session.id": session_id},
    )


# Keys the frontend Strands parser actually consumes (see
# frontend/src/lib/agentcore-client/parsers/strands.ts). Everything else that
# stream_async emits per event — the agent object, event_loop_cycle_trace /
# _span / _id, request_state, raw telemetry — is dead weight that gets
# stringified by default=str and balloons the response. Long outputs accumulate
# this until the 100 MB AgentCore response-payload limit is hit and the stream is
# cut mid-output (observed: ~103 MB in ~60s -> "Response ended prematurely" /
# the browser's "network error"). Forwarding only these keys keeps each chunk
# small (hundreds of bytes vs ~58 KB) and well under the limit.
_FORWARDED_EVENT_KEYS = frozenset(
    {
        "data",  # text delta
        "current_tool_use",  # tool use streaming
        "delta",  # tool use input delta (toolUse.input)
        "message",  # complete assistant/user message (incl. tool results)
        "result",  # final result / stop reason
        "init_event_loop",  # lifecycle
        "start_event_loop",  # lifecycle
        "start",  # lifecycle
    }
)


def _slim_event(event: dict) -> dict:
    """Keep only the fields the frontend parser reads, dropping heavy telemetry."""
    return {k: v for k, v in event.items() if k in _FORWARDED_EVENT_KEYS}


@app.entrypoint
async def invocations(payload, context: RequestContext):
    """Main entrypoint — called by AgentCore Runtime on each request.

    Extracts user ID from the validated JWT token (not the payload body)
    to prevent impersonation via prompt injection.
    """
    # Speculative pre-warm: the frontend fires this as soon as a
    # sessionId exists, before the user types anything. Reaching this line is
    # the whole point — the microVM (and its VPC ENI, image pull, and skills
    # mount) is already provisioned. Return before touching the model, JWT
    # extraction, Memory, or Gateway so the warmup never writes history.
    if payload.get("warmup"):
        yield {"status": "warm"}
        return

    user_query = payload.get("prompt")
    session_id = payload.get("runtimeSessionId")

    if not all([user_query, session_id]):
        yield {
            "status": "error",
            "error": "Missing required fields: prompt or runtimeSessionId",
        }
        return

    try:
        user_id = extract_user_id_from_context(context)
        groups = extract_user_groups_from_context(context)
        # modelKey is untrusted client input (it rides in the body, unlike the
        # JWT-derived user_id). resolve_model validates it against the allowlist
        # before any model is built; None falls back to the default model.
        resolved_model = resolve_model(payload.get("modelKey"))
        agent = create_strands_agent(user_id, groups, session_id, resolved_model)

        async for event in agent.stream_async(user_query):
            slim = _slim_event(dict(event))
            if slim:
                yield json.loads(json.dumps(slim, default=str))

    except Exception as e:
        logger.exception("Agent run failed")
        yield {"status": "error", "error": str(e)}


if __name__ == "__main__":
    app.run()
