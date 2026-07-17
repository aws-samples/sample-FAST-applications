# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the Strands docs MCP server launcher.

Covers gateway/tools/strands_mcp_server/server.py: the thin wrapper that
retargets the upstream strands-agents-mcp-server FastMCP instance to the
AgentCore Runtime MCP contract and starts it over streamable HTTP. A fake
strands_mcp_server.server module (exposing a settings-bearing mcp object and a
cache) is installed in sys.modules BEFORE the module under test is loaded (it
imports both at module level), so these tests run without the upstream package
installed (same approach as test_ltm_mcp_server.py).
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

_SERVER_PY = (
    Path(__file__).resolve().parents[2]
    / "gateway"
    / "tools"
    / "strands_mcp_server"
    / "server.py"
)


def _install_fakes(monkeypatch: pytest.MonkeyPatch, captured: dict[str, Any]) -> None:
    """Stub the upstream strands_mcp_server.server module.

    Args:
        monkeypatch: Pytest fixture used to inject the fake modules.
        captured: Dict the fake mcp.run writes its kwargs into.
    """

    class _FakeMcp:
        def __init__(self) -> None:
            # Mirror FastMCP.settings: a mutable object the wrapper overrides.
            self.settings = SimpleNamespace(
                host="127.0.0.1",
                port=8000,
                stateless_http=False,
                transport_security=None,
            )

        def run(self, **kwargs: Any) -> None:
            captured["run_kwargs"] = kwargs

    class _FakeCache:
        def ensure_ready(self) -> None:
            captured["cache_ready"] = True

    fake_upstream_server = ModuleType("strands_mcp_server.server")
    fake_upstream_server.mcp = _FakeMcp()  # type: ignore[attr-defined]
    fake_upstream_server.cache = _FakeCache()  # type: ignore[attr-defined]
    fake_upstream_pkg = ModuleType("strands_mcp_server")
    monkeypatch.setitem(sys.modules, "strands_mcp_server", fake_upstream_pkg)
    monkeypatch.setitem(sys.modules, "strands_mcp_server.server", fake_upstream_server)

    # The wrapper builds a TransportSecuritySettings; capture its kwargs so the
    # DNS-rebinding-protection toggle is asserted without the real mcp package.
    class _FakeTransportSecuritySettings:
        def __init__(self, **kwargs: Any) -> None:
            captured["transport_security_kwargs"] = kwargs

    fake_transport_mod = ModuleType("mcp.server.transport_security")
    fake_transport_mod.TransportSecuritySettings = (  # type: ignore[attr-defined]
        _FakeTransportSecuritySettings
    )
    fake_server_mod = ModuleType("mcp.server")
    fake_mcp_mod = ModuleType("mcp")
    monkeypatch.setitem(sys.modules, "mcp", fake_mcp_mod)
    monkeypatch.setitem(sys.modules, "mcp.server", fake_server_mod)
    monkeypatch.setitem(
        sys.modules, "mcp.server.transport_security", fake_transport_mod
    )


def _load_server_module() -> ModuleType:
    """Load gateway/tools/strands_mcp_server/server.py fresh, directly by path.

    Returns:
        The loaded module.
    """
    spec = importlib.util.spec_from_file_location("strands_mcp_launcher", _SERVER_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_retargets_to_agentcore_mcp_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Import-time the wrapper rebinds host/port/stateless to the contract."""
    captured: dict[str, Any] = {}
    _install_fakes(monkeypatch, captured)
    mod = _load_server_module()
    assert mod.mcp.settings.host == "0.0.0.0"
    assert mod.mcp.settings.port == 8000
    assert mod.mcp.settings.stateless_http is True
    # The upstream package constructs FastMCP for localhost, which bakes in a
    # localhost-only Host allowlist on mcp>=1.27; the wrapper must opt out or
    # the gateway's sync requests are rejected with 421.
    assert captured["transport_security_kwargs"] == {
        "enable_dns_rebinding_protection": False
    }
    assert mod.mcp.settings.transport_security is not None


def test_main_warms_cache_then_serves_streamable_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """main() warms the doc index and starts the streamable-http transport."""
    captured: dict[str, Any] = {}
    _install_fakes(monkeypatch, captured)
    mod = _load_server_module()

    mod.main()

    assert captured.get("cache_ready") is True
    assert captured["run_kwargs"] == {"transport": "streamable-http"}
