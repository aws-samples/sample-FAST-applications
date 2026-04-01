"""Strands agent with Gateway MCP tools, Memory, and Knowledge Base retrieve."""

import json
import logging
import os

from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig
from bedrock_agentcore.memory.integrations.strands.session_manager import (
    AgentCoreMemorySessionManager,
)
from bedrock_agentcore.runtime import BedrockAgentCoreApp, RequestContext
from strands import Agent
from strands.models import BedrockModel
from strands_tools import retrieve
from tools.gateway import create_gateway_mcp_client
from utils.auth import extract_user_id_from_context

logger = logging.getLogger(__name__)

app = BedrockAgentCoreApp()


def _create_session_manager(
    user_id: str, session_id: str
) -> AgentCoreMemorySessionManager:
    """Create an AgentCore Memory session manager for conversation persistence.

    Args:
        user_id: The authenticated user's ID.
        session_id: The current conversation session ID.

    Returns:
        AgentCoreMemorySessionManager: Configured session manager instance.
    """
    memory_id = os.environ.get("MEMORY_ID")
    if not memory_id:
        raise ValueError("MEMORY_ID environment variable is required")
    config = AgentCoreMemoryConfig(
        memory_id=memory_id, session_id=session_id, actor_id=user_id
    )
    return AgentCoreMemorySessionManager(
        agentcore_memory_config=config,
        region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
    )


def create_basic_agent(user_id: str, session_id: str) -> Agent:
    """Create a restaurant assistant agent with Gateway MCP tools, memory, and KB retrieve.

    Args:
        user_id: The authenticated user's ID.
        session_id: The current conversation session ID.

    Returns:
        Agent: Configured Strands agent with restaurant helper persona.
    """
    system_prompt = """You are "Restaurant Helper", a restaurant assistant helping customers reserving tables in \
different restaurants. You can talk about the menus, create new bookings, get the details of an existing booking \
or delete an existing reservation. You reply always politely and mention your name in the reply (Restaurant Helper). \
NEVER skip your name in the start of a new conversation. If customers ask about anything that you cannot reply, \
please provide the following phone number for a more personalized experience: +1 999 999 99 9999.

Some information that will be useful to answer your customer's questions:
Restaurant Helper Address: 101W 87th Street, 100024, New York, New York
You should only contact restaurant helper for technical support.
Before making a reservation, make sure that the restaurant exists in our restaurant directory.

You have access to the following tools:
- retrieve: Search the restaurant knowledge base for information about restaurants, menus, and locations
- get_booking_details: Retrieve details of an existing reservation using booking ID and restaurant name
- create_booking: Create a new restaurant reservation with date, time, restaurant, guest name, and party size
- delete_booking: Cancel an existing reservation using booking ID and restaurant name

Always use the retrieve tool first to check if a restaurant exists before making reservations.
When creating bookings, generate a unique booking ID and provide it to the customer for future reference."""

    bedrock_model = BedrockModel(
        model_id="us.anthropic.claude-sonnet-4-5-20250929-v1:0", temperature=0.1
    )

    session_manager = _create_session_manager(user_id=user_id, session_id=session_id)

    try:
        logger.info("[AGENT] Starting agent creation with Gateway tools...")
        gateway_client = create_gateway_mcp_client()
        logger.info("[AGENT] Gateway MCP client created successfully")

        agent = Agent(
            name="RestaurantHelper",
            system_prompt=system_prompt,
            tools=[retrieve, gateway_client],
            model=bedrock_model,
            session_manager=session_manager,
            trace_attributes={"user.id": user_id, "session.id": session_id},
        )
        logger.info(
            "[AGENT] Agent created successfully with Gateway tools and KB retrieve"
        )
        return agent

    except Exception as e:
        logger.exception("[AGENT ERROR] Error creating Gateway client: %s", e)
        raise


@app.entrypoint
async def invocations(payload, context: RequestContext):
    """Main entrypoint — called by AgentCore Runtime on each request.

    Extracts user ID from the validated JWT token (not the payload body)
    to prevent impersonation via prompt injection.
    """
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
        agent = create_basic_agent(user_id=user_id, session_id=session_id)

        async for event in agent.stream_async(user_query):
            yield json.loads(json.dumps(dict(event), default=str))

    except Exception as e:
        logger.exception("Agent run failed")
        yield {"status": "error", "error": str(e)}


if __name__ == "__main__":
    app.run()
