# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the warmup short-circuit in the agent entrypoint.

Covers agent/strands-single-agent/basic_agent.py: a payload carrying a
truthy "warmup" flag must yield a single {"status": "warm"} event and return
without resolving a model, extracting the caller identity, or building the
agent (no Memory writes, no Gateway client). The fakes for every runtime
dependency are installed in sys.modules BEFORE the module under test is loaded
(it imports them at module level), so these tests run without the agent
runtime dependencies installed — the same approach as test_ltm_mcp_server.py.
"""

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_BASIC_AGENT_PY = (
    Path(__file__).resolve().parents[2]
    / "agent"
    / "strands-single-agent"
    / "basic_agent.py"
)


class _FakeApp:
    """Stand-in for BedrockAgentCoreApp: a pass-through entrypoint decorator."""

    def entrypoint(self, fn: Any) -> Any:
        return fn

    def run(self) -> None:  # pragma: no cover - never called in tests
        raise AssertionError("app.run() must not be called by tests")


def _must_not_be_called(name: str) -> Any:
    def _raise(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"{name} must not be called on the warmup path")

    return _raise


def _install_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install fakes for every module basic_agent.py imports at module level.

    monkeypatch.setitem restores sys.modules afterwards, so the fakes never
    leak into other tests (notably test_models.py, which imports the real
    models module).
    """

    def module(name: str, **attrs: Any) -> ModuleType:
        mod = ModuleType(name)
        for key, value in attrs.items():
            setattr(mod, key, value)
        monkeypatch.setitem(sys.modules, name, mod)
        return mod

    # bedrock_agentcore.* package chain
    module("bedrock_agentcore")
    module("bedrock_agentcore.memory")
    module("bedrock_agentcore.memory.integrations")
    module("bedrock_agentcore.memory.integrations.strands")
    module(
        "bedrock_agentcore.memory.integrations.strands.config",
        AgentCoreMemoryConfig=_must_not_be_called("AgentCoreMemoryConfig"),
        RetrievalConfig=_must_not_be_called("RetrievalConfig"),
    )
    module(
        "bedrock_agentcore.memory.integrations.strands.session_manager",
        AgentCoreMemorySessionManager=_must_not_be_called(
            "AgentCoreMemorySessionManager"
        ),
    )
    module(
        "bedrock_agentcore.runtime",
        BedrockAgentCoreApp=_FakeApp,
        RequestContext=object,
    )

    # Pattern-local modules. Each callable raises when reached, proving the
    # warmup path returns before any of them is touched.
    module(
        "models",
        ResolvedModel=dict,
        build_model=_must_not_be_called("build_model"),
        resolve_model=_must_not_be_called("resolve_model"),
    )
    module("strands", Agent=_must_not_be_called("Agent"))
    module("strands_tools", file_read=object())
    module(
        "strands_tools.code_interpreter",
        AgentCoreCodeInterpreter=_must_not_be_called("AgentCoreCodeInterpreter"),
    )
    module("tools")
    module(
        "tools.gateway",
        create_gateway_mcp_client=_must_not_be_called("create_gateway_mcp_client"),
    )
    module("utils")
    module(
        "utils.auth",
        extract_user_id_from_context=_must_not_be_called(
            "extract_user_id_from_context"
        ),
        extract_user_groups_from_context=_must_not_be_called(
            "extract_user_groups_from_context"
        ),
    )


def _load_basic_agent(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Load basic_agent.py fresh against the installed fakes.

    Args:
        monkeypatch: Pytest fixture used to install the module fakes.

    Returns:
        The loaded basic_agent module.
    """
    _install_fakes(monkeypatch)
    monkeypatch.delitem(sys.modules, "basic_agent", raising=False)
    spec = importlib.util.spec_from_file_location("basic_agent", _BASIC_AGENT_PY)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "basic_agent", mod)
    spec.loader.exec_module(mod)
    return mod


def _collect_events(gen: Any) -> list[dict[str, Any]]:
    """Drain an async generator into a list on a fresh event loop."""

    async def _drain() -> list[dict[str, Any]]:
        return [event async for event in gen]

    return asyncio.run(_drain())


@pytest.mark.unit
def test_warmup_yields_warm_and_touches_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A warmup payload short-circuits before model/identity/Memory/Gateway."""
    basic_agent = _load_basic_agent(monkeypatch)
    # context=None proves the warmup path never reads the request context;
    # the _must_not_be_called fakes prove it never builds the agent stack.
    events = _collect_events(
        basic_agent.invocations({"warmup": True, "runtimeSessionId": "s-1"}, None)
    )
    assert events == [{"status": "warm"}]


@pytest.mark.unit
def test_warmup_works_without_session_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Warmup precedes field validation: no prompt/sessionId required."""
    basic_agent = _load_basic_agent(monkeypatch)
    events = _collect_events(basic_agent.invocations({"warmup": True}, None))
    assert events == [{"status": "warm"}]


@pytest.mark.unit
def test_falsy_warmup_still_validates_required_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A falsy warmup flag is not a warmup: the normal validation applies."""
    basic_agent = _load_basic_agent(monkeypatch)
    events = _collect_events(basic_agent.invocations({"warmup": False}, None))
    assert len(events) == 1
    assert events[0]["status"] == "error"
