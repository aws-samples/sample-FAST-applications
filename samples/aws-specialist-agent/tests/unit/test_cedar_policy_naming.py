# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regression tests for Cedar policy name generation in the cedar-policy Lambda.

Covers infra-cdk/lambdas/cedar-policy/index.py `_create_policies` naming: when
`{engine_name}_cp_{timestamp}_{index}` exceeds the 48-char AgentCore policy
name cap, the old code truncated the tail (`policy_name[:48]`), which dropped
the index and produced the SAME name for every policy — CreatePolicy then
failed with ConflictException from the second policy onward. The fix shortens
the timestamp and always keeps the index. A fake boto3 is installed in
sys.modules BEFORE the module under test is loaded (it creates a
bedrock-agentcore-control client at module level), so these tests run without
boto3 or AWS credentials (same approach as the MCP server tests).
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_INDEX_PY = (
    Path(__file__).resolve().parents[2]
    / "infra-cdk"
    / "lambdas"
    / "cedar-policy"
    / "index.py"
)

# AgentCore CreatePolicy caps the policy name at 48 characters.
_NAME_CAP = 48


class _FakeWaiter:
    """No-op stand-in for a boto3 waiter (policy_active etc.)."""

    def wait(self, **kwargs: Any) -> None:
        """Return immediately instead of polling AWS.

        Args:
            kwargs: Waiter arguments (policyEngineId, policyId, ...); ignored.
        """


class _FakeClient:
    """Records CreatePolicy calls so tests can assert on generated names."""

    def __init__(self) -> None:
        self.created_names: list[str] = []

    def create_policy(self, **kwargs: Any) -> dict[str, Any]:
        """Record the requested policy name and return a fake policy id.

        Mirrors the real API contract: a ConflictException would be raised on
        a duplicate name, so the test asserts uniqueness separately.

        Args:
            kwargs: CreatePolicy request (policyEngineId, name, description,
                definition).

        Returns:
            Minimal CreatePolicy response containing a unique policyId.
        """
        name = kwargs["name"]
        self.created_names.append(name)
        return {"policyId": f"pid-{len(self.created_names)}"}

    def get_waiter(self, name: str) -> _FakeWaiter:
        """Return a no-op waiter for any waiter name.

        Args:
            name: boto3 waiter name (e.g. "policy_active").

        Returns:
            A `_FakeWaiter` instance.
        """
        return _FakeWaiter()


def _load_index_module() -> ModuleType:
    """Load the cedar-policy Lambda module with a fake boto3 installed.

    Returns:
        The loaded module object with `client` replaced by a `_FakeClient`.
    """
    fake_boto3 = ModuleType("boto3")

    def _fake_client_factory(service_name: str) -> _FakeClient:
        """Stand-in for boto3.client used at module import time.

        Args:
            service_name: AWS service name requested by the module.

        Returns:
            A fresh `_FakeClient`.
        """
        return _FakeClient()

    fake_boto3.client = _fake_client_factory  # type: ignore[attr-defined]
    sys.modules["boto3"] = fake_boto3

    spec = importlib.util.spec_from_file_location("cedar_policy_index", _INDEX_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def index_module() -> ModuleType:
    """Provide the cedar-policy Lambda module loaded against fake boto3."""
    module = _load_index_module()
    # Replace the module-level client with a fresh recorder per test.
    module.client = _FakeClient()
    return module


def _create_and_collect_names(
    index_module: ModuleType, engine_name: str, document_count: int
) -> list[str]:
    """Run _create_policies and return the policy names it requested.

    Args:
        index_module: The loaded cedar-policy Lambda module.
        engine_name: Policy Engine name used as the name prefix.
        document_count: Number of Cedar documents to create policies for.

    Returns:
        The list of names passed to CreatePolicy, in creation order.
    """
    index_module._create_policies(
        policy_engine_id="pe-123",
        engine_name=engine_name,
        description="test",
        policy_documents=["permit(principal, action, resource);"] * document_count,
    )
    return index_module.client.created_names


def test_long_engine_name_produces_unique_names_within_cap(
    index_module: ModuleType,
) -> None:
    """Names must stay unique and <= 48 chars when the base name overflows.

    Regression: `fast_specialist_agent_policy_engine` (36 chars) pushed the
    full `{engine}_cp_{ts}_{idx}` name to 51 chars; the old tail truncation
    made all six policies collide on one name and deploys failed with
    ConflictException.
    """
    engine_name = "fast_specialist_agent_policy_engine"
    names = _create_and_collect_names(
        index_module=index_module, engine_name=engine_name, document_count=6
    )

    assert len(names) == 6
    assert len(set(names)) == 6, f"policy names must be unique, got: {names}"
    for name in names:
        assert len(name) <= _NAME_CAP, f"name over {_NAME_CAP} chars: {name}"


def test_long_engine_name_keeps_managed_prefix(index_module: ModuleType) -> None:
    """Truncated names must keep the `{engine_name}_cp` managed prefix.

    _delete_managed_policies discovers policies to delete by this prefix; a
    name that loses it would be orphaned on Update/Delete.
    """
    engine_name = "fast_specialist_agent_policy_engine"
    names = _create_and_collect_names(
        index_module=index_module, engine_name=engine_name, document_count=3
    )

    for name in names:
        assert name.startswith(f"{engine_name}_cp"), name


def test_short_engine_name_is_untruncated(index_module: ModuleType) -> None:
    """Short engine names keep the full `{engine}_cp_{ts}_{idx}` format."""
    engine_name = "FAST_demo_policy_engine"
    names = _create_and_collect_names(
        index_module=index_module, engine_name=engine_name, document_count=2
    )

    for index, name in enumerate(names):
        assert len(name) <= _NAME_CAP
        assert name.startswith(f"{engine_name}_cp_")
        assert name.endswith(f"_{index}")
