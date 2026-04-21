"""
Ranking parser and aggregator for LLM Council peer reviews.

This module handles parsing rankings from model responses and calculating
aggregate rankings across all peer evaluations.
"""

import re
from collections import defaultdict
from typing import Dict, List, Any


def parse_ranking_from_text(ranking_text: str) -> List[str]:
    """
    Parse the FINAL RANKING section from a model's response.
    
    The ranking prompt instructs models to provide rankings in a specific format:
    
    FINAL RANKING:
    1. Response A
    2. Response B
    3. Response C
    
    This function extracts that structured ranking using regex patterns. It has
    multiple fallback strategies to handle variations in model output.
    
    Args:
        ranking_text: The full text response from the model containing rankings
    
    Returns:
        List of response labels in ranked order (e.g., ["Response A", "Response C", "Response B"])
    """
    # Strategy 1: Look for explicit "FINAL RANKING:" section
    if "FINAL RANKING:" in ranking_text:
        parts = ranking_text.split("FINAL RANKING:")
        if len(parts) >= 2:
            ranking_section = parts[1]
            
            # Try to extract numbered list format (e.g., "1. Response A")
            numbered_matches = re.findall(r'\d+\.\s*Response [A-Z]', ranking_section)
            if numbered_matches:
                # Extract just the "Response X" part from each match
                return [re.search(r'Response [A-Z]', m).group() for m in numbered_matches]
            
            # Fallback: Extract all "Response X" patterns in order of appearance
            matches = re.findall(r'Response [A-Z]', ranking_section)
            if matches:
                return matches
    
    # Strategy 2: Final fallback - find any "Response X" patterns in the entire text
    # This handles cases where the model didn't follow the exact format
    matches = re.findall(r'Response [A-Z]', ranking_text)
    return matches


def calculate_aggregate_rankings(
    stage2_results: List[Dict[str, Any]],
    label_to_model: Dict[str, str]
) -> List[Dict[str, Any]]:
    """
    Calculate aggregate rankings across all peer evaluations.
    
    This function computes the average rank position for each model based on
    all peer evaluations. Lower average rank is better (1st place is best).
    
    The algorithm:
    1. For each peer evaluation, extract the ranked list of responses
    2. Record the position (1, 2, 3, ...) where each model appears
    3. Calculate the average position for each model across all evaluations
    4. Sort models by average position (lower is better)
    
    Args:
        stage2_results: List of ranking results from Stage 2, each containing:
            - model: The model that provided the ranking
            - ranking: The full ranking text
            - parsed_ranking: List of response labels in ranked order
        label_to_model: Mapping from anonymized labels (e.g., "Response A") to model IDs
    
    Returns:
        List of dicts sorted by average rank (best to worst), each containing:
            - model: The model ID
            - average_rank: Average position across all rankings (lower is better)
            - rankings_count: Number of times this model was ranked
    """
    # Track all positions where each model was ranked
    model_positions = defaultdict(list)
    
    # Process each peer evaluation
    for ranking_result in stage2_results:
        # Get the parsed ranking (list of "Response X" labels in order)
        parsed_ranking = ranking_result.get("parsed_ranking", [])
        
        # Record the position (1-indexed) for each response
        for position, label in enumerate(parsed_ranking, start=1):
            # Map the anonymized label back to the actual model ID
            if label in label_to_model:
                model_id = label_to_model[label]
                model_positions[model_id].append(position)
    
    # Calculate average position for each model
    aggregate = []
    for model_id, positions in model_positions.items():
        if positions:  # Only include models that were ranked at least once
            avg_rank = sum(positions) / len(positions)
            aggregate.append({
                "model": model_id,
                "average_rank": round(avg_rank, 2),
                "rankings_count": len(positions)
            })
    
    # Sort by average rank (lower is better)
    aggregate.sort(key=lambda x: x["average_rank"])
    
    return aggregate


def get_model_short_name(model_id: str) -> str:
    """
    Extract a short, readable name from a Bedrock model ID.
    
    Bedrock model IDs are verbose (e.g., "us.anthropic.claude-sonnet-4-5-20250929-v1:0").
    This function extracts just the model name for display purposes.
    
    Args:
        model_id: Full Bedrock model identifier
    
    Returns:
        Short model name (e.g., "claude-sonnet-4-5")
    """
    # Extract the part after the last dot and before the version
    if "." in model_id:
        name_part = model_id.split(".")[-1]  # Get "claude-sonnet-4-5-20250929-v1:0"
        if ":" in name_part:
            name_part = name_part.split(":")[0]  # Remove ":0" suffix
        # Remove date and version suffixes
        parts = name_part.split("-")
        # Keep only the model name parts (claude-sonnet-4-5)
        filtered_parts = [p for p in parts if not p.isdigit() or len(p) <= 2]
        return "-".join(filtered_parts[:4])  # Limit to first 4 parts
    return model_id
