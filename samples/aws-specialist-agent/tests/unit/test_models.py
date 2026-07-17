# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the selectable-model resolver.

Covers agent/strands-single-agent/models.py: logical-key resolution against
the MODEL_MAP allowlist, the default-key fallback, fail-loudly rejection of
unknown/empty keys, and the provider dispatch in build_model. The module is
strands-free at import time (Strands is imported lazily inside build_model), so
these tests run without the agent runtime dependencies installed.
"""

import importlib
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

# models.py lives in the agent pattern directory, not on the package path, so
# add that directory to sys.path before importing it (mirrors how the Dockerfile
# lays the module out next to basic_agent.py at /app).
_AGENT_DIR = Path(__file__).resolve().parents[2] / "agent" / "strands-single-agent"
sys.path.insert(0, str(_AGENT_DIR))


def _load_models() -> ModuleType:
    """Import (or reimport) the models module fresh.

    Returns:
        The imported models module.
    """
    if "models" in sys.modules:
        return importlib.reload(sys.modules["models"])
    return importlib.import_module("models")


# The MODEL_MAP shape the CDK injects: logical key -> {id, provider}.
# Every registry model appears here (there is no availability gate); a key not
# present is simply unknown and cannot resolve.
_MODEL_MAP = {
    "fable-5": {
        "id": "global.anthropic.claude-fable-5",
        "provider": "anthropic",
    },
    "opus-4.8": {
        "id": "global.anthropic.claude-opus-4-8",
        "provider": "anthropic",
    },
    "sonnet-4.6": {
        "id": "global.anthropic.claude-sonnet-4-6",
        "provider": "anthropic",
    },
    "haiku-4.5": {
        "id": "global.anthropic.claude-haiku-4-5-20251001-v1:0",
        "provider": "anthropic",
    },
    "gpt-5.4": {
        "id": "openai.gpt-5.4",
        "provider": "openai",
    },
    "gpt-5.5": {
        "id": "openai.gpt-5.5",
        "provider": "openai",
    },
}


@pytest.fixture
def models(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Provide the models module with a representative environment configured.

    Args:
        monkeypatch: Pytest fixture used to set MODEL_MAP / DEFAULT_MODEL_KEY.

    Returns:
        The freshly imported models module.
    """
    monkeypatch.setenv("MODEL_MAP", json.dumps(_MODEL_MAP))
    monkeypatch.setenv("DEFAULT_MODEL_KEY", "sonnet-4.6")
    return _load_models()


def test_resolve_none_returns_default(models: ModuleType) -> None:
    """None (no key sent) resolves to DEFAULT_MODEL_KEY's model."""
    assert models.resolve_model(None) == _MODEL_MAP["sonnet-4.6"]


def test_resolve_valid_key_passthrough(models: ModuleType) -> None:
    """A valid logical key resolves to its physical id and provider."""
    assert models.resolve_model("fable-5") == _MODEL_MAP["fable-5"]
    assert models.resolve_model("opus-4.8") == _MODEL_MAP["opus-4.8"]
    assert models.resolve_model("haiku-4.5") == _MODEL_MAP["haiku-4.5"]
    assert models.resolve_model("gpt-5.5") == _MODEL_MAP["gpt-5.5"]


def test_resolve_unknown_key_raises(models: ModuleType) -> None:
    """An unknown key is rejected (never resolved to a model)."""
    with pytest.raises(ValueError, match="Unknown or unavailable model key"):
        models.resolve_model("no-such-model")


def test_resolve_empty_string_raises_not_defaults(models: ModuleType) -> None:
    """An empty string is rejected loudly, not silently treated as default.

    Guards the explicit `is None` check (vs a truthy `or default`): an empty
    modelKey is a client error and must fail, not quietly fall back.
    """
    with pytest.raises(ValueError, match="Unknown or unavailable model key"):
        models.resolve_model("")


def test_forged_key_never_resolves(models: ModuleType) -> None:
    """A forged key matching no allowlist entry cannot reach a physical model.

    Regression for the security property: modelKey is untrusted input and the
    allowlist is the gate before any model is built.
    """
    for forged in ["../etc/passwd", "anthropic.claude-opus-4-8", "OPUS-4.8", "x"]:
        with pytest.raises(ValueError):
            models.resolve_model(forged)


def test_resolve_missing_model_map_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing MODEL_MAP env var fails loudly rather than defaulting."""
    monkeypatch.delenv("MODEL_MAP", raising=False)
    monkeypatch.setenv("DEFAULT_MODEL_KEY", "sonnet-4.6")
    mod = _load_models()
    with pytest.raises(ValueError, match="MODEL_MAP environment variable is required"):
        mod.resolve_model("opus-4.8")


def test_resolve_missing_default_key_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing DEFAULT_MODEL_KEY fails loudly when a request omits the key."""
    monkeypatch.setenv("MODEL_MAP", json.dumps(_MODEL_MAP))
    monkeypatch.delenv("DEFAULT_MODEL_KEY", raising=False)
    mod = _load_models()
    with pytest.raises(
        ValueError, match="DEFAULT_MODEL_KEY environment variable is required"
    ):
        mod.resolve_model(None)


def test_resolve_empty_model_map_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty MODEL_MAP object is treated as misconfiguration, not valid."""
    monkeypatch.setenv("MODEL_MAP", "{}")
    monkeypatch.setenv("DEFAULT_MODEL_KEY", "sonnet-4.6")
    mod = _load_models()
    with pytest.raises(ValueError, match="non-empty JSON object"):
        mod.resolve_model(None)


def _stub_openai_responses_model(
    monkeypatch: pytest.MonkeyPatch, captured: dict[str, Any]
) -> type:
    """Install fakes for the OpenAIResponsesModel + token generator imports.

    The fake's signature deliberately has no `params` / `temperature`: every
    model runs on its provider-default inference parameters, so a
    change that reintroduces them fails these tests with a TypeError.

    Args:
        monkeypatch: Pytest fixture used to inject the fake modules.
        captured: Dict the fake constructor writes its kwargs into.

    Returns:
        The fake OpenAIResponsesModel class (for isinstance assertions).
    """

    class _FakeOpenAIResponsesModel:
        def __init__(
            self,
            *,
            model_id: str,
            stateful: bool,
            client_args: dict[str, Any],
        ) -> None:
            captured["model_id"] = model_id
            captured["stateful"] = stateful
            captured["client_args"] = client_args

    fake_or_mod = ModuleType("strands.models.openai_responses")
    fake_or_mod.OpenAIResponsesModel = _FakeOpenAIResponsesModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "strands.models.openai_responses", fake_or_mod)

    fake_tokgen = ModuleType("aws_bedrock_token_generator")
    fake_tokgen.provide_token = lambda *, region: f"token-for-{region}"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "aws_bedrock_token_generator", fake_tokgen)
    return _FakeOpenAIResponsesModel


def test_build_model_openai_builds_responses_model(
    models: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The OpenAI branch builds an OpenAIResponsesModel for the mantle endpoint.

    Asserts the load-bearing constraints: stateful=False (so AgentCore Memory's
    conversation manager is not rejected), the in-region mantle base_url with
    the required /openai/v1 path (derived from the runtime's own AWS_REGION),
    and a bearer token generated for that same region as api_key.
    The fake constructor rejects any inference params, so this also covers the
    no-temperature rule for the OpenAI branch.
    """
    captured: dict[str, Any] = {}
    fake_cls = _stub_openai_responses_model(monkeypatch, captured)
    monkeypatch.setenv("AWS_REGION", "us-east-1")

    result = models.build_model(
        {
            "id": "openai.gpt-5.5",
            "provider": "openai",
        }
    )

    assert isinstance(result, fake_cls)
    assert captured["model_id"] == "openai.gpt-5.5"
    assert captured["stateful"] is False
    assert (
        captured["client_args"]["base_url"]
        == "https://bedrock-mantle.us-east-1.api.aws/openai/v1"
    )
    assert captured["client_args"]["api_key"] == "token-for-us-east-1"


def test_build_model_openai_missing_region_raises(
    models: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing AWS_REGION fails loudly instead of targeting a wrong region.

    The mantle endpoint is resolved from the runtime's own region;
    there is deliberately no fallback default.
    """
    captured: dict[str, Any] = {}
    _stub_openai_responses_model(monkeypatch, captured)
    monkeypatch.delenv("AWS_REGION", raising=False)

    with pytest.raises(KeyError, match="AWS_REGION"):
        models.build_model(
            {
                "id": "openai.gpt-5.4",
                "provider": "openai",
            }
        )


def test_build_model_unknown_provider_raises(models: ModuleType) -> None:
    """An unrecognized provider value is rejected."""
    with pytest.raises(ValueError, match="Unrecognized model provider"):
        models.build_model({"id": "x", "provider": "meta"})


def _stub_bedrock_model(
    monkeypatch: pytest.MonkeyPatch, captured: dict[str, Any]
) -> type:
    """Install a fake strands.models.BedrockModel that records its kwargs.

    The fake accepts only model_id: no model is built with `temperature` (or
    any other inference param) since current-generation models reject it, so
    reintroducing one fails these tests with a TypeError.

    Args:
        monkeypatch: Pytest fixture used to inject the fake module.
        captured: Dict the fake constructor writes its kwargs into.

    Returns:
        The fake BedrockModel class (for isinstance assertions).
    """

    class _FakeBedrockModel:
        def __init__(self, *, model_id: str) -> None:
            captured["model_id"] = model_id

    fake_strands = ModuleType("strands")
    fake_models_mod = ModuleType("strands.models")
    fake_models_mod.BedrockModel = _FakeBedrockModel  # type: ignore[attr-defined]
    fake_strands.models = fake_models_mod  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "strands", fake_strands)
    monkeypatch.setitem(sys.modules, "strands.models", fake_models_mod)
    return _FakeBedrockModel


def test_build_model_anthropic_builds_with_model_id_only(
    models: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The anthropic branch builds a BedrockModel from the physical id alone.

    Regression for the no-temperature rule: models like Opus 4.8 and
    Fable 5 reject `temperature` with a ValidationException ("`temperature` is
    deprecated for this model"), so build_model must never pass it. The fake
    constructor accepts only model_id and turns any extra kwarg into a
    TypeError.
    """
    captured: dict[str, Any] = {}
    fake_cls = _stub_bedrock_model(monkeypatch, captured)

    result = models.build_model(
        {
            "id": "global.anthropic.claude-opus-4-8",
            "provider": "anthropic",
        }
    )

    assert isinstance(result, fake_cls)
    assert captured["model_id"] == "global.anthropic.claude-opus-4-8"
