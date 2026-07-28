# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Model selection shared by both agent patterns.

The UI can pick a model per request; anything outside the allowlist falls back
to the default. Bedrock model ids run natively (Converse); OpenAI GPT ids run
through the Amazon Bedrock Mantle endpoint (requires the
AmazonBedrockMantleInferenceAccess managed policy on the runtime role).
"""

import os

DEFAULT_MODEL = os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-5")

# Models the UI may select. Claude Sonnet 5 (Bedrock, default) and GPT-5.5 (via
# the Bedrock Mantle endpoint; requires AmazonBedrockMantleInferenceAccess).
ALLOWED_MODELS = {
    "us.anthropic.claude-sonnet-5",
    "openai.gpt-5.5",
}


def resolve_model_id(requested: str | None) -> str:
    """Return the requested model id if allowed, otherwise the default."""
    return requested if requested in ALLOWED_MODELS else DEFAULT_MODEL


def is_openai(model_id: str) -> bool:
    return model_id.startswith("openai.")


def mantle_base_url(region: str) -> str:
    return f"https://bedrock-mantle.{region}.api.aws/openai/v1"
