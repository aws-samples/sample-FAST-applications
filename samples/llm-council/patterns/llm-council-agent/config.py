"""
Configuration for the LLM Council agent.

This module loads council configuration from environment variables set by CDK.
The configuration includes the list of council member models and the chairman model.
"""

import os
import json
from typing import List


def _load_council_models() -> List[str]:
    """
    Load council models from environment variable.
    
    Returns:
        List[str]: List of Bedrock model IDs for council members
    
    Raises:
        ValueError: If COUNCIL_MODELS environment variable is not set or invalid
    """
    models_json = os.environ.get("COUNCIL_MODELS")
    if not models_json:
        raise ValueError(
            "COUNCIL_MODELS environment variable not set. "
            "This should be configured in infra-cdk/config.yaml"
        )
    
    try:
        models = json.loads(models_json)
        if not isinstance(models, list) or not models:
            raise ValueError("COUNCIL_MODELS must be a non-empty list")
        return models
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse COUNCIL_MODELS JSON: {e}")


def _load_chairman_model() -> str:
    """
    Load chairman model from environment variable.
    
    Returns:
        str: Bedrock model ID for the chairman
    
    Raises:
        ValueError: If CHAIRMAN_MODEL environment variable is not set
    """
    chairman = os.environ.get("CHAIRMAN_MODEL")
    if not chairman:
        raise ValueError(
            "CHAIRMAN_MODEL environment variable not set. "
            "This should be configured in infra-cdk/config.yaml"
        )
    return chairman


# Load configuration from environment variables
COUNCIL_MODELS = _load_council_models()
CHAIRMAN_MODEL = _load_chairman_model()

# Memory configuration
MEMORY_ID: str = os.environ.get("MEMORY_ID", "")

# AWS region
AWS_REGION: str = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")

# Timeout for individual model invocations (seconds)
MODEL_TIMEOUT: int = 120

# Maximum tokens for model responses
MAX_TOKENS: int = 4096
