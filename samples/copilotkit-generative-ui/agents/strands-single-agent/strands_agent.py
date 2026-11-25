# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import logging
import os

from ag_ui.core import RunAgentInput, RunErrorEvent
from ag_ui_strands import (
    PredictStateMapping,
    StrandsAgent,
    StrandsAgentConfig,
    ToolBehavior,
    ToolCallContext,
)
from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig
from bedrock_agentcore.memory.integrations.strands.session_manager import (
    AgentCoreMemorySessionManager,
)
from bedrock_agentcore.runtime import BedrockAgentCoreApp, RequestContext
from mcp.client.streamable_http import streamablehttp_client
from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient
from tools.query_data import query_data
from tools.todos import manage_todos
from utils.auth import extract_user_id_from_context, get_gateway_access_token
from utils.ssm import get_ssm_parameter

app = BedrockAgentCoreApp()
logger = logging.getLogger(__name__)

ACTOR_ID_KEYS = ("actor_id", "actorId", "user_id", "userId", "sub")

SYSTEM_PROMPT = """You are a helpful assistant with access to tools via the Gateway and built-in data tools.

When asked for a chart, table, dashboard, or any other visual of data, always call the query_data tool first to fetch
the data from the database, then render it with the render_ui tool using the components of the generative UI catalog.
Every figure you show must come from query_data. Never invent data.
When managing todos, use manage_todos to update the list.
When asked about your tools, list them and explain what they do."""

BEDROCK_MODEL = BedrockModel(
    model_id="us.anthropic.claude-sonnet-5-5",
)


def create_gateway_mcp_client() -> MCPClient:
    """
    Create MCP client for AgentCore Gateway with OAuth2 authentication.

    Calls get_gateway_access_token() inside the lambda factory to ensure a fresh
    token is fetched on every MCP reconnection (avoids the closure trap).
    """
    stack_name = os.environ.get("STACK_NAME")
    if not stack_name:
        raise ValueError("STACK_NAME environment variable is required")

    if not stack_name.replace("-", "").replace("_", "").isalnum():
        raise ValueError("Invalid STACK_NAME format")

    gateway_url = get_ssm_parameter(f"/{stack_name}/gateway_url")

    return MCPClient(
        lambda: streamablehttp_client(
            url=gateway_url,
            headers={"Authorization": f"Bearer {get_gateway_access_token()}"},
        ),
        prefix="gateway",
    )


def _make_session_manager_provider(actor_id: str):
    """
    Per-thread AgentCore Memory session-manager factory for the AG-UI adapter.

    ag-ui-strands builds one Strands agent per thread from the template agent and
    attaches the manager returned here to it. A session_manager set on the template
    agent itself is ignored, so this provider is the supported way to get
    cloud-persistent conversation history keyed by actor_id + thread_id.
    """
    memory_id = os.environ.get("MEMORY_ID")
    if not memory_id:
        raise ValueError("MEMORY_ID environment variable is required")

    def provider(input_data: RunAgentInput) -> AgentCoreMemorySessionManager:
        return AgentCoreMemorySessionManager(
            agentcore_memory_config=AgentCoreMemoryConfig(
                memory_id=memory_id,
                session_id=input_data.thread_id or actor_id,
                actor_id=actor_id,
            ),
            region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        )

    return provider


def state_context_builder(input_data: RunAgentInput, user_message: str) -> str:
    """
    Append the current todos (AG-UI shared state) to the outgoing user message so
    the agent always knows the latest todo state without a separate get_todos tool.
    """
    state = input_data.state if isinstance(input_data.state, dict) else {}
    todos = state.get("todos") or []
    if not todos:
        return user_message
    return f"{user_message}\n\nCurrent todos:\n{json.dumps(todos, indent=2)}"


async def todos_state_from_args(ctx: ToolCallContext) -> dict:
    """
    When manage_todos is called, emit a StateSnapshotEvent with the new todos so
    the frontend updates immediately (before the tool result arrives).
    """
    tool_input = ctx.tool_input if isinstance(ctx.tool_input, dict) else {}
    return {"todos": tool_input.get("todos", [])}


def build_agui_config(actor_id: str) -> StrandsAgentConfig:
    """
    AG-UI adapter configuration: shared state for the todo canvas and durable
    AgentCore Memory.

    Frontend tools (toggleTheme, scheduleTime, enableAppMode, ...) and the
    generative UI tool (render_ui, added by the CopilotKit runtime when the
    frontend registers a generative UI catalog) arrive on RunAgentInput.tools and
    are registered by the adapter on every run. They need no configuration here.
    """
    return StrandsAgentConfig(
        tool_behaviors={
            "manage_todos": ToolBehavior(
                state_from_args=todos_state_from_args,
                predict_state=[
                    PredictStateMapping(
                        state_key="todos",
                        tool="manage_todos",
                        tool_argument="todos",
                    )
                ],
            ),
        },
        state_context_builder=state_context_builder,
        session_manager_provider=_make_session_manager_provider(actor_id),
        # AgentCore Memory owns the conversation history, so the adapter must not
        # replay the client's copy of it into the Strands agent as well.
        replay_history_into_strands=False,
    )


def create_strands_agent(actor_id: str, session_id: str) -> StrandsAgent:
    """
    Create a StrandsAgent wrapping a Strands SDK agent with AgentCore Memory,
    Gateway MCP tools, and CopilotKit-compatible AG-UI configuration.
    """
    # Template agent: the adapter reads its model, system prompt and tools and
    # builds the per-thread agent (with the AgentCore Memory session manager) from it.
    core_agent = Agent(
        name="FASTAgent",
        system_prompt=SYSTEM_PROMPT,
        tools=[create_gateway_mcp_client(), query_data, manage_todos],
        model=BEDROCK_MODEL,
        record_direct_tool_call=True,
        trace_attributes={
            "user.id": actor_id,
            "session.id": session_id,
        },
    )

    return StrandsAgent(
        agent=core_agent,
        name="FASTAgent",
        description="FAST Strands agent with CopilotKit generative UI support",
        config=build_agui_config(actor_id),
    )


@app.entrypoint
async def invocations(payload: dict, context: RequestContext):
    """
    Main entrypoint for the Strands agent using AG-UI protocol.

    Accepts RunAgentInput payloads from the CopilotKit Lambda Runtime,
    streams AG-UI events back, and supports generative UI, shared state
    (todos), and human-in-the-loop interactions via CopilotKit.
    """
    input_data = RunAgentInput.model_validate(payload)

    # Extract actor identity securely from the validated JWT token.
    try:
        actor_id = extract_user_id_from_context(context)
    except ValueError:
        # Fall back to forwarded props if JWT extraction fails (e.g. local dev).
        forwarded = input_data.forwarded_props if isinstance(input_data.forwarded_props, dict) else {}
        actor_id = next(
            (forwarded[k] for k in ACTOR_ID_KEYS if k in forwarded and forwarded[k]),
            None,
        )

    if not actor_id:
        raise ValueError(
            "Missing actor identity. Provide forwardedProps.actor_id/user_id or include sub claim in the bearer token."
        )

    # Use thread_id from the request (set by CopilotKit runtime) or fall back
    # to actor_id so each user gets their own persistent conversation thread.
    session_id = input_data.thread_id or actor_id

    # Ensure thread_id in the payload matches the session the memory provider resolves.
    input_data = input_data.model_copy(update={"thread_id": session_id})

    try:
        strands_agent = create_strands_agent(actor_id, session_id)

        async for event in strands_agent.run(input_data):
            if event is not None:
                yield event.model_dump(mode="json", by_alias=True, exclude_none=True)

    except Exception as exc:
        logger.exception("Agent run failed")
        yield RunErrorEvent(
            message=str(exc) or type(exc).__name__,
            code=type(exc).__name__,
        ).model_dump(mode="json", by_alias=True, exclude_none=True)


if __name__ == "__main__":
    app.run()
