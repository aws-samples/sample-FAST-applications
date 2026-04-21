"""
Core orchestration logic for the LLM Council 3-stage deliberation process.

This module implements the three stages:
1. Stage 1: Collect individual responses from all council models
2. Stage 2: Collect anonymized peer rankings from all models
3. Stage 3: Chairman synthesizes final response
"""

from typing import Dict, List, Tuple, Any

from .bedrock_client import invoke_models_parallel, invoke_bedrock_model
from .config import COUNCIL_MODELS, CHAIRMAN_MODEL
from .prompts import (
    STAGE1_SYSTEM_PROMPT,
    build_ranking_prompt,
    build_chairman_prompt,
)
from .ranking_parser import parse_ranking_from_text, calculate_aggregate_rankings


async def stage1_collect_responses(user_query: str) -> List[Dict[str, Any]]:
    """
    Stage 1: Collect individual responses from all council models.
    
    All models are queried in parallel with the same user query. Each model
    provides its own independent response without seeing other models' answers.
    Only successful responses are included in the results.
    
    Args:
        user_query: The user's question to answer
    
    Returns:
        List of dicts, each containing:
            - model: The model ID that provided the response
            - response: The model's response text
    """
    print(f"[COUNCIL] Stage 1: Collecting responses from {len(COUNCIL_MODELS)} models")
    
    # Create messages in the format expected by Bedrock
    messages = [{"role": "user", "content": user_query}]
    
    # Query all models in parallel
    responses = await invoke_models_parallel(
        model_ids=COUNCIL_MODELS,
        messages=messages,
        system_prompt=STAGE1_SYSTEM_PROMPT,
        temperature=0.1,
    )
    
    # Format results, filtering out failed invocations
    stage1_results = []
    for model_id, response_text in responses.items():
        if response_text is not None:
            stage1_results.append({
                "model": model_id,
                "response": response_text
            })
    
    print(f"[COUNCIL] Stage 1 complete: {len(stage1_results)} successful responses")
    return stage1_results


async def stage2_collect_rankings(
    user_query: str,
    stage1_results: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    """
    Stage 2: Each model ranks the anonymized responses from Stage 1.
    
    Key innovation: Responses are anonymized as "Response A, B, C..." to prevent
    models from favoring their own work or showing brand bias. Each model evaluates
    all responses and provides a ranking.
    
    Args:
        user_query: The original user question
        stage1_results: List of responses from Stage 1
    
    Returns:
        Tuple of:
            - stage2_results: List of ranking results from each model
            - label_to_model: Mapping from anonymized labels to model IDs
    """
    print(f"[COUNCIL] Stage 2: Collecting rankings from {len(COUNCIL_MODELS)} models")
    
    # Create anonymized labels (A, B, C, ...)
    labels = [chr(65 + i) for i in range(len(stage1_results))]
    
    # Create mapping from label to model ID for later de-anonymization
    label_to_model = {
        f"Response {label}": result["model"]
        for label, result in zip(labels, stage1_results)
    }
    
    print(f"[COUNCIL] Anonymized {len(stage1_results)} responses as: {', '.join([f'Response {l}' for l in labels])}")
    
    # Build the ranking prompt with anonymized responses
    ranking_prompt = build_ranking_prompt(user_query, stage1_results, labels)
    
    # Create messages for ranking
    messages = [{"role": "user", "content": ranking_prompt}]
    
    # Get rankings from all council models in parallel
    responses = await invoke_models_parallel(
        model_ids=COUNCIL_MODELS,
        messages=messages,
        temperature=0.1,
    )
    
    # Format results with parsed rankings
    stage2_results = []
    for model_id, response_text in responses.items():
        if response_text is not None:
            # Parse the ranking from the response text
            parsed_ranking = parse_ranking_from_text(response_text)
            stage2_results.append({
                "model": model_id,
                "ranking": response_text,
                "parsed_ranking": parsed_ranking
            })
            print(f"[COUNCIL] {model_id} ranked: {parsed_ranking}")
    
    print(f"[COUNCIL] Stage 2 complete: {len(stage2_results)} rankings collected")
    return stage2_results, label_to_model


async def stage3_synthesize_final(
    user_query: str,
    stage1_results: List[Dict[str, Any]],
    stage2_results: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Stage 3: Chairman model synthesizes the final response.
    
    The chairman receives all information from previous stages:
    - All individual responses from Stage 1
    - All peer rankings and evaluations from Stage 2
    
    The chairman synthesizes this into a comprehensive final answer that
    represents the council's collective wisdom.
    
    Args:
        user_query: The original user question
        stage1_results: Individual responses from Stage 1
        stage2_results: Peer rankings from Stage 2
    
    Returns:
        Dict containing:
            - model: The chairman model ID
            - response: The synthesized final answer
    """
    print(f"[COUNCIL] Stage 3: Chairman synthesizing final response")
    
    # Build the chairman prompt with all context
    chairman_prompt = build_chairman_prompt(user_query, stage1_results, stage2_results)
    
    # Create messages for chairman
    messages = [{"role": "user", "content": chairman_prompt}]
    
    # Query the chairman model
    response_text = await invoke_bedrock_model(
        model_id=CHAIRMAN_MODEL,
        messages=messages,
        temperature=0.1,
    )
    
    if response_text is None:
        print("[COUNCIL ERROR] Chairman failed to generate response")
        return {
            "model": CHAIRMAN_MODEL,
            "response": "Error: Unable to generate final synthesis. The chairman model failed to respond."
        }
    
    print(f"[COUNCIL] Stage 3 complete: Chairman synthesized final response")
    return {
        "model": CHAIRMAN_MODEL,
        "response": response_text
    }


async def run_full_council(user_query: str) -> Tuple[List, List, Dict, Dict]:
    """
    Run the complete 3-stage council deliberation process.
    
    This is the main orchestration function that coordinates all three stages
    and returns the complete results including metadata for display.
    
    Args:
        user_query: The user's question to answer
    
    Returns:
        Tuple of:
            - stage1_results: Individual responses from all models
            - stage2_results: Peer rankings from all models
            - stage3_result: Final synthesized answer from chairman
            - metadata: Additional information (label mappings, aggregate rankings)
    """
    print(f"[COUNCIL] Starting full council deliberation")
    print(f"[COUNCIL] Query: {user_query}")
    
    # Stage 1: Collect individual responses
    stage1_results = await stage1_collect_responses(user_query)
    
    MIN_COUNCIL_SIZE = 3

    # Require at least 3 successful responses for a meaningful deliberation
    if len(stage1_results) < MIN_COUNCIL_SIZE:
        print(f"[COUNCIL ERROR] Only {len(stage1_results)} models responded, need at least {MIN_COUNCIL_SIZE}")
        return [], [], {
            "model": "error",
            "response": f"Only {len(stage1_results)} of {len(COUNCIL_MODELS)} models responded. "
                        f"At least {MIN_COUNCIL_SIZE} are required for a council deliberation. Please try again."
        }, {}
    
    # Stage 2: Collect peer rankings
    stage2_results, label_to_model = await stage2_collect_rankings(
        user_query, stage1_results
    )
    
    # Calculate aggregate rankings across all peer evaluations
    aggregate_rankings = calculate_aggregate_rankings(
        stage2_results, label_to_model
    )
    
    print(f"[COUNCIL] Aggregate rankings calculated:")
    for i, ranking in enumerate(aggregate_rankings, 1):
        print(f"[COUNCIL]   #{i}: {ranking['model']} (avg rank: {ranking['average_rank']})")
    
    # Stage 3: Chairman synthesizes final answer
    stage3_result = await stage3_synthesize_final(
        user_query,
        stage1_results,
        stage2_results
    )
    
    # Prepare metadata for frontend display
    metadata = {
        "label_to_model": label_to_model,
        "aggregate_rankings": aggregate_rankings
    }
    
    print(f"[COUNCIL] Full council deliberation complete")
    return stage1_results, stage2_results, stage3_result, metadata
