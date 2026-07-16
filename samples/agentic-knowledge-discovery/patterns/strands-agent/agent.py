"""AG-UI Strands agent with Gateway tools, Memory, model selection, and citations."""

from __future__ import annotations

import logging
import os

from ag_ui.core import RunAgentInput, RunErrorEvent
from ag_ui_strands import StrandsAgent, StrandsAgentConfig
from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig
from bedrock_agentcore.memory.integrations.strands.session_manager import (
    AgentCoreMemorySessionManager,
)
from bedrock_agentcore.runtime import BedrockAgentCoreApp, RequestContext
from strands import Agent
from strands.models import BedrockModel
from tools.gateway import create_gateway_mcp_client
from tools.output_tools import cite_sources, suggest_questions
from utils.auth import extract_user_id_from_context
from utils.models import is_openai, mantle_base_url, resolve_model_id
from utils.prompts import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

app = BedrockAgentCoreApp()

REGION = os.environ.get("AWS_REGION", "us-east-1")
MEMORY_ID = os.environ.get("MEMORY_ID")


def build_model(model_id: str):
    """Build a Strands model from a model id.

    Bedrock ids run via Converse; openai.* ids run on the Bedrock Mantle
    endpoint (requires AmazonBedrockMantleInferenceAccess on the runtime role).
    """
    if is_openai(model_id):
        from strands.models._openai_bedrock import resolve_bedrock_client_args
        from strands.models.openai_responses import OpenAIResponsesModel

        args = resolve_bedrock_client_args({"region": REGION})
        args["base_url"] = mantle_base_url(REGION)
        return OpenAIResponsesModel(client_args=args, model_id=model_id)

    # No temperature: Sonnet 5 (and newer models) reject it.
    return BedrockModel(model_id=model_id)


def _make_session_manager_provider(actor_id: str):
    """Per-thread AgentCore Memory session-manager factory for the AG-UI adapter."""

    def provider(run_input: RunAgentInput) -> AgentCoreMemorySessionManager | None:
        if not MEMORY_ID:
            return None
        session_id = run_input.thread_id or actor_id
        return AgentCoreMemorySessionManager(
            AgentCoreMemoryConfig(
                memory_id=MEMORY_ID, session_id=session_id, actor_id=actor_id
            ),
            region_name=REGION,
        )

    return provider


@app.entrypoint
async def invocations(payload: dict, context: RequestContext):
    input_data = RunAgentInput.model_validate(payload)
    actor_id = extract_user_id_from_context(context)

    # UI-selected model (validated against the allowlist), from AG-UI forwardedProps.
    forwarded = input_data.forwarded_props or {}
    model_id = resolve_model_id(forwarded.get("modelId") if isinstance(forwarded, dict) else None)

    agent = Agent(
        model=build_model(model_id),
        system_prompt=SYSTEM_PROMPT,
        tools=[create_gateway_mcp_client(actor_id), suggest_questions, cite_sources],
        # Automatic context management: summarizes older turns and offloads large
        # tool results so long conversations don't overflow the context window.
        context_manager="auto",
    )
    agui_agent = StrandsAgent(
        agent=agent,
        name="agui_strands_agent",
        description="AG-UI Strands agent with Gateway tools, citations, and suggestions",
        config=StrandsAgentConfig(
            session_manager_provider=_make_session_manager_provider(actor_id),
            replay_history_into_strands=False,
        ),
    )

    try:
        async for event in agui_agent.run(input_data):
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
