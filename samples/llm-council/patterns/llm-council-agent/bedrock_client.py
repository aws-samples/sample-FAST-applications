"""
Bedrock API client for invoking multiple models in parallel.

This module provides functions to invoke Bedrock models asynchronously,
enabling parallel execution of multiple model calls for the council system.
Uses the Bedrock Converse API for unified cross-provider compatibility.
"""

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Any

import boto3
from botocore.exceptions import ClientError

from .config import AWS_REGION, MODEL_TIMEOUT, MAX_TOKENS

# Thread-local storage for boto3 clients (one per thread, reused across calls)
_thread_local = threading.local()

# Shared thread pool for parallel model invocations
_executor = ThreadPoolExecutor(max_workers=10)


def get_bedrock_client():
    """
    Get or create a thread-local Bedrock Runtime client.

    Each thread in the pool gets its own client instance which is reused
    across calls, avoiding the overhead of creating a new client per invocation
    while remaining thread-safe.

    Returns:
        boto3.client: Bedrock Runtime client configured for the specified region
    """
    if not hasattr(_thread_local, "client"):
        _thread_local.client = boto3.client("bedrock-runtime", region_name=AWS_REGION)
    return _thread_local.client


async def invoke_bedrock_model(
    model_id: str,
    messages: List[Dict[str, str]],
    system_prompt: Optional[str] = None,
    temperature: float = 0.1,
) -> Optional[str]:
    """
    Invoke a single Bedrock model asynchronously using the Converse API.

    The Converse API provides a unified interface across all Bedrock model
    providers (Anthropic, Meta, Amazon, Cohere, etc.), eliminating the need
    for provider-specific request/response formatting.

    Args:
        model_id: The Bedrock model identifier (e.g., "us.anthropic.claude-sonnet-4-20250514-v1:0")
        messages: List of message dicts with 'role' and 'content' keys
        system_prompt: Optional system prompt to guide the model's behavior
        temperature: Sampling temperature (0.0 to 1.0), lower is more deterministic

    Returns:
        str: The model's response text, or None if invocation failed
    """
    client = get_bedrock_client()

    # Build Converse API messages format
    converse_messages = []
    for msg in messages:
        converse_messages.append({
            "role": msg["role"],
            "content": [{"text": msg["content"]}],
        })

    # Build the Converse API kwargs
    kwargs: Dict[str, Any] = {
        "modelId": model_id,
        "messages": converse_messages,
        "inferenceConfig": {
            "maxTokens": MAX_TOKENS,
            "temperature": temperature,
        },
    }

    # Add system prompt if provided
    if system_prompt:
        kwargs["system"] = [{"text": system_prompt}]

    try:
        print(f"[BEDROCK] Invoking model: {model_id}")

        # Run the synchronous boto3 call in the shared thread pool
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            _executor,
            lambda: client.converse(**kwargs),
        )

        # Extract text from Converse API response
        output = response.get("output", {})
        message = output.get("message", {})
        content = message.get("content", [])

        if content and "text" in content[0]:
            print(f"[BEDROCK] Successfully invoked {model_id}")
            return content[0]["text"]

        print(f"[BEDROCK] No text content in response from {model_id}")
        return None

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        error_message = e.response.get("Error", {}).get("Message", str(e))
        print(f"[BEDROCK ERROR] ClientError invoking {model_id}: {error_code} - {error_message}")
        return None

    except Exception as e:
        print(f"[BEDROCK ERROR] Unexpected error invoking {model_id}: {type(e).__name__} - {str(e)}")
        return None


async def invoke_models_parallel(
    model_ids: List[str],
    messages: List[Dict[str, str]],
    system_prompt: Optional[str] = None,
    temperature: float = 0.1,
) -> Dict[str, Optional[str]]:
    """
    Invoke multiple Bedrock models in parallel.

    This function creates async tasks for each model invocation and waits for all
    to complete using asyncio.gather(). Failed invocations return None and don't
    block successful ones.

    Args:
        model_ids: List of Bedrock model identifiers to invoke
        messages: List of message dicts with 'role' and 'content' keys
        system_prompt: Optional system prompt for all models
        temperature: Sampling temperature for all models

    Returns:
        Dict mapping model_id to response text (or None if failed)
    """
    print(f"[BEDROCK] Starting parallel invocation of {len(model_ids)} models")

    # Create tasks for all model invocations
    tasks = [
        invoke_bedrock_model(
            model_id=model_id,
            messages=messages,
            system_prompt=system_prompt,
            temperature=temperature,
        )
        for model_id in model_ids
    ]

    # Wait for all tasks to complete (return_exceptions=True means exceptions don't stop other tasks)
    responses = await asyncio.gather(*tasks, return_exceptions=True)

    # Map model IDs to their responses
    result = {}
    for model_id, response in zip(model_ids, responses):
        if isinstance(response, Exception):
            print(f"[BEDROCK] Model {model_id} raised exception: {response}")
            result[model_id] = None
        else:
            result[model_id] = response

    successful_count = sum(1 for r in result.values() if r is not None)
    print(f"[BEDROCK] Parallel invocation complete: {successful_count}/{len(model_ids)} successful")

    return result
