# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the LTM MCP server.

Covers gateway/tools/ltm_mcp_server/server.py: the actor-scoped namespace
derivation, the JSON shape of the tool result, the empty-memory note, and the
missing-header error. The fakes for mcp and bedrock-agentcore are installed in
sys.modules BEFORE the module under test is loaded (it imports both at module
level), so these tests run without the server dependencies installed (same
approach as the test for the in-process tool this server replaced).
"""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_SERVER_PY = (
    Path(__file__).resolve().parents[2]
    / "gateway"
    / "tools"
    / "ltm_mcp_server"
    / "server.py"
)


class _FakeRecord:
    """Minimal stand-in for bedrock_agentcore's MemoryRecord (DictWrapper)."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)


class _FakeRequest:
    """Stand-in for the HTTP request exposed via the MCP request context."""

    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers


class _FakeRequestContext:
    def __init__(self, request: _FakeRequest | None) -> None:
        self.request = request


class _FakeContext:
    """Stand-in for mcp.server.fastmcp.Context."""

    def __init__(self, request: _FakeRequest | None) -> None:
        self.request_context = _FakeRequestContext(request)


def _install_fakes(
    monkeypatch: pytest.MonkeyPatch,
    captured: dict[str, Any],
    records: list[_FakeRecord],
) -> None:
    """Stub mcp's FastMCP/Context and bedrock_agentcore's MemorySessionManager.

    Args:
        monkeypatch: Pytest fixture used to inject the fake modules.
        captured: Dict the fakes write constructor/call kwargs into.
        records: The records the fake list call returns.
    """

    class _FakeManager:
        def __init__(self, *, memory_id: str, region_name: str) -> None:
            captured["memory_id"] = memory_id
            captured["region_name"] = region_name

        def list_long_term_memory_records(
            self, *, namespace: str, max_results: int
        ) -> list[_FakeRecord]:
            captured["namespace"] = namespace
            captured["max_results"] = max_results
            return records

    fake_session_mod = ModuleType("bedrock_agentcore.memory.session")
    fake_session_mod.MemorySessionManager = _FakeManager  # type: ignore[attr-defined]
    fake_memory_mod = ModuleType("bedrock_agentcore.memory")
    fake_root_mod = ModuleType("bedrock_agentcore")
    monkeypatch.setitem(sys.modules, "bedrock_agentcore", fake_root_mod)
    monkeypatch.setitem(sys.modules, "bedrock_agentcore.memory", fake_memory_mod)
    monkeypatch.setitem(
        sys.modules, "bedrock_agentcore.memory.session", fake_session_mod
    )

    class _FakeFastMCP:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            captured["fastmcp_kwargs"] = kwargs

        def tool(self) -> Any:
            return lambda f: f

        def run(self, **kwargs: Any) -> None:  # pragma: no cover
            raise AssertionError("run() must not be called from tests")

    fake_fastmcp_mod = ModuleType("mcp.server.fastmcp")
    fake_fastmcp_mod.FastMCP = _FakeFastMCP  # type: ignore[attr-defined]
    fake_fastmcp_mod.Context = _FakeContext  # type: ignore[attr-defined]
    fake_server_mod = ModuleType("mcp.server")
    fake_mcp_mod = ModuleType("mcp")
    monkeypatch.setitem(sys.modules, "mcp", fake_mcp_mod)
    monkeypatch.setitem(sys.modules, "mcp.server", fake_server_mod)
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", fake_fastmcp_mod)


def _load_server_module() -> ModuleType:
    """Load gateway/tools/ltm_mcp_server/server.py fresh, directly by path.

    Returns:
        The loaded module.
    """
    spec = importlib.util.spec_from_file_location("ltm_mcp_server", _SERVER_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ctx(actor_id: str | None) -> _FakeContext:
    if actor_id is None:
        return _FakeContext(_FakeRequest({}))
    return _FakeContext(
        _FakeRequest({"x-amzn-bedrock-agentcore-runtime-custom-actor-id": actor_id})
    )


def test_stateless_streamable_http_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    """The server binds 0.0.0.0 stateless, per the AgentCore MCP contract."""
    captured: dict[str, Any] = {}
    _install_fakes(monkeypatch, captured, [])
    _load_server_module()
    assert captured["fastmcp_kwargs"] == {"host": "0.0.0.0", "stateless_http": True}


def test_missing_memory_id_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing MEMORY_ID env var fails loudly at first tool call."""
    _install_fakes(monkeypatch, {}, [])
    monkeypatch.delenv("MEMORY_ID", raising=False)
    mod = _load_server_module()
    with pytest.raises(ValueError, match="MEMORY_ID environment variable is required"):
        mod.list_long_term_memories(_ctx("user-1"))


def test_namespace_is_bound_to_header_actor(monkeypatch: pytest.MonkeyPatch) -> None:
    """The facts namespace comes from the propagated actor-id header.

    Guards the identity property: the actor is taken from the header the agent
    runtime attaches (never from a tool parameter), so the model has no input
    through which to reach another user's namespace.
    """
    captured: dict[str, Any] = {}
    _install_fakes(monkeypatch, captured, [])
    monkeypatch.setenv("MEMORY_ID", "mem-123")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    mod = _load_server_module()

    mod.list_long_term_memories(_ctx("user-abc"))

    assert captured["memory_id"] == "mem-123"
    assert captured["region_name"] == "us-east-1"
    assert captured["namespace"] == "/facts/user-abc"


def test_returns_facts_as_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stored facts come back as a JSON object, blank texts dropped."""
    captured: dict[str, Any] = {}
    records = [
        _FakeRecord(
            {"content": {"text": "the user prefers Python"}, "createdAt": "t1"}
        ),
        _FakeRecord({"content": {"text": "  "}, "createdAt": "t2"}),
        _FakeRecord({"content": {"text": "lives in Tokyo"}, "createdAt": "t3"}),
    ]
    _install_fakes(monkeypatch, captured, records)
    monkeypatch.setenv("MEMORY_ID", "mem-123")
    mod = _load_server_module()

    result = json.loads(mod.list_long_term_memories(_ctx("user-abc")))

    assert [f["fact"] for f in result["facts"]] == [
        "the user prefers Python",
        "lives in Tokyo",
    ]
    assert result["facts"][0]["created_at"] == "t1"


def test_empty_memory_returns_note(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no stored facts the tool explains the async-extraction lag."""
    _install_fakes(monkeypatch, {}, [])
    monkeypatch.setenv("MEMORY_ID", "mem-123")
    mod = _load_server_module()

    result = json.loads(mod.list_long_term_memories(_ctx("user-abc")))

    assert result["facts"] == []
    assert "asynchronously" in result["note"]


def test_missing_actor_header_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without the propagated header there is no actor to scope to."""
    captured: dict[str, Any] = {}
    _install_fakes(monkeypatch, captured, [])
    monkeypatch.setenv("MEMORY_ID", "mem-123")
    mod = _load_server_module()

    result = json.loads(mod.list_long_term_memories(_ctx(None)))

    assert "error" in result
    # The memory API must not be touched when no actor is established.
    assert "namespace" not in captured
