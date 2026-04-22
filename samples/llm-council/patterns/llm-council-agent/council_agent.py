"""
LLM Council Agent - Main entrypoint for AgentCore Runtime.

This agent implements a 3-stage deliberation process where multiple LLMs
collaborate to answer questions:
1. Stage 1: Multiple models provide independent responses
2. Stage 2: Models anonymously rank each other's responses
3. Stage 3: A chairman model synthesizes the final answer

Based on Andrej Karpathy's LLM Council architecture, adapted for AWS Bedrock.
"""

import json
import traceback
from typing import AsyncGenerator, Dict, Any

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from .council import run_full_council
from .config import COUNCIL_MODELS, CHAIRMAN_MODEL

app = BedrockAgentCoreApp()


async def stream_council_response(user_query: str) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Stream the council deliberation process with progressive updates.
    
    This function runs the 3-stage council process and yields events as each
    stage completes, allowing the frontend to show progressive updates.
    
    Args:
        user_query: The user's question to answer
    
    Yields:
        Dict events with type and data for each stage:
            - stage1_start: Stage 1 is beginning
            - stage1_complete: Stage 1 results available
            - stage2_start: Stage 2 is beginning
            - stage2_complete: Stage 2 results available
            - stage3_start: Stage 3 is beginning
            - stage3_complete: Stage 3 results available
            - complete: All stages finished
            - error: An error occurred
    """
    try:
        # Notify that Stage 1 is starting
        yield {
            "type": "stage1_start",
            "message": f"Collecting responses from {len(COUNCIL_MODELS)} council members..."
        }
        
        # Run the full council process
        stage1_results, stage2_results, stage3_result, metadata = await run_full_council(user_query)
        
        # Yield Stage 1 results
        yield {
            "type": "stage1_complete",
            "data": {
                "stage1": stage1_results
            }
        }
        
        # Notify that Stage 2 is starting
        yield {
            "type": "stage2_start",
            "message": "Council members are reviewing and ranking responses..."
        }
        
        # Yield Stage 2 results with metadata
        yield {
            "type": "stage2_complete",
            "data": {
                "stage2": stage2_results,
                "metadata": metadata
            }
        }
        
        # Notify that Stage 3 is starting
        yield {
            "type": "stage3_start",
            "message": "Chairman is synthesizing final response..."
        }
        
        # Yield Stage 3 results
        yield {
            "type": "stage3_complete",
            "data": {
                "stage3": stage3_result
            }
        }
        
        # Notify completion
        yield {
            "type": "complete",
            "message": "Council deliberation complete"
        }
        
    except Exception as e:
        print(f"[AGENT ERROR] Error in stream_council_response: {e}")
        traceback.print_exc()
        yield {
            "type": "error",
            "error": str(e)
        }


@app.entrypoint
async def agent_stream(payload):
    """
    Main entrypoint for the LLM Council agent.
    
    This is the function that AgentCore Runtime calls when the agent receives
    a request. It extracts the user's query and streams the council deliberation
    process back to the frontend.
    
    The response format is designed to be compatible with the existing FAST
    frontend while providing the additional structure needed for council display.
    
    Args:
        payload: Dict containing:
            - prompt: The user's question
            - userId: User identifier
            - runtimeSessionId: Session identifier
    
    Yields:
        Events for each stage of the council process
    """
    user_query = payload.get("prompt")
    user_id = payload.get("userId")
    session_id = payload.get("runtimeSessionId")
    
    # Validate required fields
    if not all([user_query, user_id, session_id]):
        yield {
            "status": "error",
            "error": "Missing required fields: prompt, userId, or runtimeSessionId"
        }
        return
    
    try:
        print(f"[AGENT] Starting LLM Council for user: {user_id}, session: {session_id}")
        print(f"[AGENT] Query: {user_query}")
        print(f"[AGENT] Council members: {len(COUNCIL_MODELS)}")
        print(f"[AGENT] Chairman: {CHAIRMAN_MODEL}")
        
        # Stream the council deliberation process
        async for event in stream_council_response(user_query):
            yield event
            
    except Exception as e:
        print(f"[AGENT ERROR] Error in agent_stream: {e}")
        traceback.print_exc()
        yield {
            "status": "error",
            "error": str(e)
        }


if __name__ == "__main__":
    app.run()
