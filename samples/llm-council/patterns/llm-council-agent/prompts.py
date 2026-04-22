"""
Prompt templates for the LLM Council 3-stage deliberation process.

This module contains the carefully crafted prompts that guide models through
each stage of the council process.
"""

from typing import List, Dict, Any


def build_ranking_prompt(
    user_query: str,
    stage1_results: List[Dict[str, Any]],
    labels: List[str]
) -> str:
    """
    Build the prompt for Stage 2 peer ranking.
    
    This prompt is critical to the council's effectiveness. It:
    1. Presents anonymized responses to prevent bias
    2. Asks for detailed evaluation of each response
    3. Requires a structured ranking format for parsing
    
    Args:
        user_query: The original user question
        stage1_results: List of responses from Stage 1
        labels: List of anonymized labels (e.g., ["A", "B", "C"])
    
    Returns:
        Formatted prompt string for ranking
    """
    # Build the anonymized responses section
    responses_text = "\n\n".join([
        f"Response {label}:\n{result['response']}"
        for label, result in zip(labels, stage1_results)
    ])
    
    prompt = f"""You are evaluating different responses to the following question:

Question: {user_query}

Here are the responses from different models (anonymized):

{responses_text}

Your task:
1. First, evaluate each response individually. For each response, explain what it does well and what it does poorly.
2. Then, at the very end of your response, provide a final ranking.

IMPORTANT: Your final ranking MUST be formatted EXACTLY as follows:
- Start with the line "FINAL RANKING:" (all caps, with colon)
- Then list the responses from best to worst as a numbered list
- Each line should be: number, period, space, then ONLY the response label (e.g., "1. Response A")
- Do not add any other text or explanations in the ranking section

Example of the correct format for your ENTIRE response:

Response A provides good detail on X but misses Y...
Response B is accurate but lacks depth on Z...
Response C offers the most comprehensive answer...

FINAL RANKING:
1. Response C
2. Response A
3. Response B

Now provide your evaluation and ranking:"""
    
    return prompt


def build_chairman_prompt(
    user_query: str,
    stage1_results: List[Dict[str, Any]],
    stage2_results: List[Dict[str, Any]]
) -> str:
    """
    Build the prompt for Stage 3 chairman synthesis.
    
    The chairman receives all information from previous stages and synthesizes
    a comprehensive final answer that represents the council's collective wisdom.
    
    Args:
        user_query: The original user question
        stage1_results: Individual responses from Stage 1
        stage2_results: Peer rankings from Stage 2
    
    Returns:
        Formatted prompt string for chairman synthesis
    """
    # Build Stage 1 summary
    stage1_text = "\n\n".join([
        f"Model: {result['model']}\nResponse: {result['response']}"
        for result in stage1_results
    ])
    
    # Build Stage 2 summary
    stage2_text = "\n\n".join([
        f"Model: {result['model']}\nRanking: {result['ranking']}"
        for result in stage2_results
    ])
    
    prompt = f"""You are the Chairman of an LLM Council. Multiple AI models have provided responses to a user's question, and then ranked each other's responses.

Original Question: {user_query}

STAGE 1 - Individual Responses:
{stage1_text}

STAGE 2 - Peer Rankings:
{stage2_text}

Your task as Chairman is to synthesize all of this information into a single, comprehensive, accurate answer to the user's original question. Consider:
- The individual responses and their insights
- The peer rankings and what they reveal about response quality
- Any patterns of agreement or disagreement
- The strengths and weaknesses identified in the peer reviews

Provide a clear, well-reasoned final answer that represents the council's collective wisdom. Do not simply repeat one response - synthesize the best elements from all responses into a cohesive answer."""
    
    return prompt


# System prompt for Stage 1 (individual responses)
STAGE1_SYSTEM_PROMPT = """You are a helpful AI assistant participating in a council of AI models. 
You will be asked a question, and your response will be evaluated alongside responses from other models.
Provide a clear, accurate, and well-reasoned answer to the best of your ability."""
