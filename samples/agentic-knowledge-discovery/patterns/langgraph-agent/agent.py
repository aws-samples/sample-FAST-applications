"""AG-UI LangGraph agent with Gateway tools, Memory, model selection, and citations."""

from __future__ import annotations

import logging
import os

from ag_ui.core import RunAgentInput, RunErrorEvent
from bedrock_agentcore.runtime import BedrockAgentCoreApp, RequestContext
from copilotkit import CopilotKitMiddleware, LangGraphAGUIAgent
from langchain.agents import create_agent
from langchain_aws import ChatBedrock
from langgraph_checkpoint_aws import AgentCoreMemorySaver
from tools.gateway import create_gateway_mcp_client
from tools.output_tools import cite_sources, suggest_questions
from utils.auth import extract_user_id_from_context
from utils.models import DEFAULT_MODEL, is_openai, resolve_model_id
from utils.prompts import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

app = BedrockAgentCoreApp()

REGION = os.environ.get("AWS_REGION", "us-east-1")
MEMORY_ID = os.environ.get("MEMORY_ID")


def build_model(model_id: str) -> ChatBedrock:
    """Build a LangChain chat model. Bedrock ids run via Converse.

    GPT-via-Mantle requires SigV4-signed requests, which the Strands OpenAI
    client handles but LangChain's ChatOpenAI does not, so on this pattern an
    openai.* selection falls back to the default Bedrock model. Use the Strands
    pattern for GPT.
    """
    if is_openai(model_id):
        logger.warning("GPT via Mantle is Strands-only; falling back to %s", DEFAULT_MODEL)
        model_id = DEFAULT_MODEL
    # No temperature: Sonnet 5 (and newer models) reject it.
    return ChatBedrock(
        model_id=model_id,
        streaming=True,
        beta_use_converse_api=True,
    )


def get_memory_saver() -> AgentCoreMemorySaver | None:
    """Return an AgentCore Memory checkpointer, or None when MEMORY_ID is unset."""
    if not MEMORY_ID:
        return None
    return AgentCoreMemorySaver(memory_id=MEMORY_ID, region_name=REGION)


async def build_graph(actor_id: str, model_id: str):
    """Build a LangGraph compiled graph with Gateway tools, output tools, and Memory."""
    mcp_client = await create_gateway_mcp_client(actor_id)
    tools = await mcp_client.get_tools()
    tools.extend([suggest_questions, cite_sources])

    return create_agent(
        model=build_model(model_id),
        tools=tools,
        checkpointer=get_memory_saver(),
        middleware=[CopilotKitMiddleware()],
        system_prompt=SYSTEM_PROMPT,
    )


@app.entrypoint
async def invocations(payload: dict, context: RequestContext):
    input_data = RunAgentInput.model_validate(payload)
    actor_id = extract_user_id_from_context(context)

    forwarded = input_data.forwarded_props or {}
    model_id = resolve_model_id(forwarded.get("modelId") if isinstance(forwarded, dict) else None)

    graph = await build_graph(actor_id, model_id)
    agui_agent = LangGraphAGUIAgent(
        name="agui_langgraph_agent",
        description="AG-UI LangGraph agent with Gateway tools, citations, and suggestions",
        graph=graph,
        config={"configurable": {"actor_id": actor_id}},
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
