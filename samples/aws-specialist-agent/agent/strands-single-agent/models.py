"""User-selectable Bedrock model resolution and construction.

This module isolates everything about "which LLM the agent uses" so that
basic_agent.py stays lean. The frontend sends a stable logical key (e.g.
"opus-4.8") in the invoke payload; this module validates that key against an
allowlist sourced from the environment and resolves it to the physical Bedrock
model id, then builds the matching Strands model.

Two environment variables, both injected by the CDK from the single source of
truth in infra-cdk/lib/utils/model-registry.ts:
  - MODEL_MAP: JSON object mapping each logical key to
    {"id": <physical id>, "provider": "anthropic" | "openai"}.
  - DEFAULT_MODEL_KEY: the logical key used when a request omits one.

Strands is imported lazily inside the factory so this module (and its unit
tests) load without the agent runtime dependencies installed.
"""

import json
import os
from typing import Any, Literal, TypedDict

Provider = Literal["anthropic", "openai"]


def _mantle_base_url(region: str) -> str:
    """Build the bedrock-mantle OpenAI Responses API base URL for a region.

    OpenAI (GPT-5.x) is served via the bedrock-mantle / OpenAI Responses API,
    not bedrock-runtime. Since 2026-06 the models are available in us-east-1,
    so the mantle calls target the runtime's own region through the in-region
    bedrock-mantle VPC endpoint. The base URL needs the `/openai/v1`
    path (the bare `/v1` path is rejected with "does not support the
    '/v1/responses' API").

    Args:
        region: The AWS region to target (the runtime's own region).

    Returns:
        The mantle base URL including the required /openai/v1 path.
    """
    return f"https://bedrock-mantle.{region}.api.aws/openai/v1"


class ResolvedModel(TypedDict):
    """A logical key resolved to its physical Bedrock model and provider.

    Attributes:
        id: The physical Bedrock model id / inference-profile id to invoke.
        provider: Which Strands model class / auth path to use.
    """

    id: str
    provider: Provider


def _load_model_map() -> dict[str, ResolvedModel]:
    """Parse the MODEL_MAP env var into a logical-key -> ResolvedModel dict.

    Reads the variable on each call (rather than at import) so tests can set it
    via monkeypatch without import-order constraints.

    Returns:
        A dict mapping each available logical key to its ResolvedModel.

    Raises:
        ValueError: If MODEL_MAP is missing, empty, or not a JSON object.
    """
    raw = os.environ.get("MODEL_MAP")
    if not raw:
        raise ValueError("MODEL_MAP environment variable is required")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict) or not parsed:
        raise ValueError("MODEL_MAP must be a non-empty JSON object")
    return parsed


def resolve_model(requested_key: str | None) -> ResolvedModel:
    """Resolve and validate a requested logical model key.

    The requested key is untrusted client input (it rides in the request body,
    unlike the user identity which comes from the validated JWT). It is checked
    against the allowlist (MODEL_MAP keys) before any model is built, so a forged
    or unknown key can never reach Bedrock.

    Args:
        requested_key: The logical key from the request, or None when the request
            did not specify one. None (and only None) falls back to
            DEFAULT_MODEL_KEY. An empty string is rejected, not defaulted, so the
            failure is loud rather than silent.

    Returns:
        The ResolvedModel (physical id + provider) for the key.

    Raises:
        ValueError: If DEFAULT_MODEL_KEY is unset while defaulting, or the
            (defaulted or requested) key is not in the allowlist.
    """
    model_map = _load_model_map()

    if requested_key is None:
        key = os.environ.get("DEFAULT_MODEL_KEY")
        if not key:
            raise ValueError("DEFAULT_MODEL_KEY environment variable is required")
    else:
        key = requested_key

    if key not in model_map:
        raise ValueError(f"Unknown or unavailable model key: {key!r}")
    return model_map[key]


def build_model(resolved: ResolvedModel) -> Any:
    """Build the Strands model for a resolved model, dispatching on provider.

    Strands classes are imported lazily here so this module loads without the
    agent runtime installed (e.g. during unit tests).

    No model is given a `temperature`: current-generation models reject the
    parameter outright (Claude Opus 4.8 / Fable 5 fail with a
    ValidationException, GPT-5.5 with a 400), so every model runs on its
    provider default.

    Args:
        resolved: The physical id + provider returned by resolve_model.

    Returns:
        A Strands model instance (BedrockModel for Anthropic,
        OpenAIResponsesModel for OpenAI). Typed as Any to avoid importing Strands
        at module load.

    Raises:
        ValueError: For an unrecognized provider value.
    """
    provider = resolved["provider"]
    if provider == "anthropic":
        # Imported lazily so this module loads without Strands installed (e.g.
        # in unit tests). The ignore covers the missing stub when Strands is not
        # present on the type-checker's path.
        from strands.models import BedrockModel  # type: ignore[import-not-found]

        return BedrockModel(model_id=resolved["id"])

    if provider == "openai":
        # GPT-5.x is served via the bedrock-mantle / OpenAI Responses API, not
        # bedrock-runtime. Build an OpenAIResponsesModel pointed at the mantle
        # endpoint, authenticated with a short-lived Bedrock bearer token derived
        # from the runtime's own credentials (no stored key to rotate).
        #
        # Imported lazily so the openai extra is not required for Claude-only
        # use and so this module loads without Strands installed in tests.
        from aws_bedrock_token_generator import (  # type: ignore[import-not-found]
            provide_token,
        )
        from strands.models.openai_responses import (  # type: ignore[import-not-found]
            OpenAIResponsesModel,
        )

        # The mantle endpoint lives in the runtime's own region.
        # AWS_REGION is always set in the AgentCore Runtime environment; a
        # KeyError here is a misconfiguration that should fail loudly rather
        # than silently target a wrong region.
        region = os.environ["AWS_REGION"]

        # stateful MUST be False: a stateful Responses model manages history
        # server-side and forbids a conversation manager (throws if one is
        # supplied), but the agent always supplies AgentCoreMemorySessionManager,
        # which would silently break AgentCore Memory history restore.
        return OpenAIResponsesModel(
            model_id=resolved["id"],
            stateful=False,
            client_args={
                "api_key": provide_token(region=region),
                "base_url": _mantle_base_url(region),
            },
        )

    raise ValueError(f"Unrecognized model provider: {provider!r}")
