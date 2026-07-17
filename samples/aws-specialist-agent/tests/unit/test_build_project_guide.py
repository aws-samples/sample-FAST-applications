# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for scripts/build-project-guide.py.

Covers the repo-mirror exclusion rules and the end-to-end build. The script
filename is hyphenated, so it is loaded via importlib from its file path.
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "build-project-guide.py"


def _load() -> ModuleType:
    """Load the hyphenated script as a module."""
    spec = importlib.util.spec_from_file_location("build_project_guide", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_project_guide"] = module
    spec.loader.exec_module(module)
    return module


bpg = _load()


class TestIsMirrored:
    """is_mirrored applies the repo-mirror exclusion rules."""

    @pytest.mark.parametrize(
        "path",
        [
            "README.md",
            "infra-cdk/config.yaml",
            "infra-cdk/lib/backend-stack.ts",
            "agent/strands-single-agent/basic_agent.py",
            "agent/utils/auth.py",
            "gateway/policies/02-aws-mcp-read.cedar",
            "frontend/src/lib/agentcore-client/client.ts",
            "docs/GATEWAY.md",
            "scripts/build-project-guide.py",
            "scripts/create-demo-users.py",
            "scripts/.env.example",  # committed, secret-free template -> OK
        ],
    )
    def test_includes_primary_sources(self, path: str) -> None:
        assert bpg.is_mirrored(path)

    @pytest.mark.parametrize(
        "path",
        [
            "skills/agent-toolkit-for-aws/aws-cdk/SKILL.md",
            "skills/aws-specialist-agent/fast-project-guide/SKILL.md",
            "docs-jp/GATEWAY.md",
            ".github/workflows/python-lint.yml",
            "tests/unit/test_models.py",
            "test-scripts/test-agent.py",
            "frontend/src/test/build.test.ts",
            "frontend/src/components/ui/button.tsx",
            "frontend/public/favicon.ico",
            "frontend/package-lock.json",
            "frontend/src/types/jsx.d.ts",
            "docs/architecture-diagram/aws-specialist-agent-architecture.png",
            "docs/img/screenshot.png",
            "docs/.nav.yml",
            "CLAUDE.md",  # Claude Code entrypoint (@imports vibe-context/) -> not agent content
            "README-jp.md",  # JA translation; English README already mirrored
            "CHANGELOG.md",
            "skills-lock.json",
            "infra-cdk/jest.config.js",
            "scripts/.env",  # secrets/PII (password, email) -> must never mirror
            "scripts/.env.local",
            "frontend/.env",
        ],
    )
    def test_excludes_noise(self, path: str) -> None:
        assert not bpg.is_mirrored(path)


class TestBuild:
    """End-to-end build into a temp dir using the real repo as input."""

    def test_build_produces_tree(self, tmp_path: Path) -> None:
        skill_out = bpg.build(tmp_path)
        assert (skill_out / "SKILL.md").is_file()
        assert (skill_out / "references" / "overview.md").is_file()
        assert (skill_out / "references" / "code-map.md").is_file()
        assert (skill_out / "references" / "repo" / "README.md").is_file()
        assert not (skill_out / "references" / "repo" / "skills").exists()
        assert not (skill_out / "references" / "repo" / "docs-jp").exists()
        # A .env secrets file must never be mirrored (only its template).
        assert not list(skill_out.rglob(".env"))
