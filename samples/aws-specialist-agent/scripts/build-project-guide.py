#!/usr/bin/env python3
"""Build the fast-project-guide skill tree for the S3 Files skills mount.

Assembles the self-describing skill that lets the deployed agent answer
questions about this demo application itself. The output combines:

- the committed skill source (SKILL.md + handwritten references) from
  skills/aws-specialist-agent/fast-project-guide/
- references/repo/       — a mirror of the git-tracked source files,
  with noise removed (vendored skills, tests, CI config, binaries, lockfiles;
  see the exclusion rules below)
- references/code-map.md — an auto-generated file tree of the mirror with
  per-directory descriptions, used by the agent to locate files

Usage:
    python3 scripts/build-project-guide.py [--output <dir>]

The default output directory is skills/aws-specialist-agent/build/ (git-ignored). CDK synth
invokes this script through the BucketDeployment local bundling in
infra-cdk/lib/skills-storage-stack.ts, so a generation failure fails the synth.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_NAME = "fast-project-guide"
SKILL_SRC = REPO_ROOT / "skills" / "aws-specialist-agent" / SKILL_NAME
DEFAULT_OUTPUT = REPO_ROOT / "skills" / "aws-specialist-agent" / "build"

# --- repo mirror selection -------------------------------------------------
# Everything git-tracked is mirrored EXCEPT the rules below.

EXCLUDED_PREFIXES: tuple[str, ...] = (
    # Already mounted at /mnt/skills (the vendored agent-toolkit-for-aws skills)
    # or self-recursive (this project's own skills, including this guide).
    "skills/",
    # Stale translation of docs/ — the English docs are the source of truth.
    "docs-jp/",
    # Coding-assistant / CI / build-tool configuration — not demo architecture.
    ".amazonq",
    ".claude/",
    ".clinerules",
    ".github/",
    ".kiro",
    ".mkdocs/",
    # Tests are not needed to explain the architecture or configuration.
    "tests/",
    "test-scripts/",
    "infra-cdk/test/",
    "frontend/src/test/",
    # Generated shadcn/ui boilerplate and static assets.
    "frontend/src/components/ui/",
    "frontend/public/",
    "frontend/readme-imgs/",
)

EXCLUDED_FILES: frozenset[str] = frozenset(
    {
        ".dockerignore",
        ".gitignore",
        ".kics.yml",
        ".prettierrc",
        "CLAUDE.md",  # Claude Code entrypoint; @imports vibe-context/, not agent content
        "README-jp.md",  # Japanese translation of README.md; English README already mirrored
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "LICENSE",
        "NOTICE",
        "VERSION",
        "requirements-dev.txt",
        "ruff.toml",
        "skills-lock.json",
        "frontend/components.json",
        "frontend/eslint.config.mjs",
        "frontend/postcss.config.mjs",
        "frontend/tsconfig.json",
        "frontend/tsconfig.node.json",
        "frontend/vitest.config.ts",
        "infra-cdk/.gitignore",
        "infra-cdk/.npmignore",
        "infra-cdk/jest.config.js",
        "infra-cdk/minimal-deploy-policy.json",
    }
)

EXCLUDED_SUFFIXES: tuple[str, ...] = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".svg",
    ".woff",
    ".woff2",
    ".drawio",
    ".d.ts",
    ".nav.yml",
    "package-lock.json",
)

# Directory descriptions surfaced in code-map.md (deepest match wins is not
# needed — each directory is annotated independently when present).
DIR_DESCRIPTIONS: dict[str, str] = {
    ".": "Repository root (README.md is the entry point for the whole project)",
    "docker": "docker compose for local development (runs only the frontend locally)",
    "docs": "Official documentation. Primary reference for architecture, deployment, and each feature",
    "frontend": "React + TypeScript + Vite + shadcn/ui frontend",
    "frontend/src/app": "Application-wide context",
    "frontend/src/components/auth": "Cognito authentication components",
    "frontend/src/components/chat": "Chat UI (message display, input, model selector, history sidebar, tool rendering)",
    "frontend/src/hooks": "Custom hooks (authentication, tool rendering, etc.)",
    "frontend/src/lib/agentcore-client": "SSE streaming client for AgentCore Runtime with per-pattern parsers",
    "frontend/src/routes": "React Router route definitions",
    "frontend/src/services": "API service layer (feedback, conversation history, sessions)",
    "gateway": "AgentCore Gateway tool implementations and Cedar policies",
    "gateway/policies": "Cedar ABAC policies (tool access controlled by the department claim derived from Cognito groups)",
    "gateway/tools/sample_tool": "Sample Lambda-target tool (text analysis)",
    "gateway/tools/ltm_mcp_server": "MCP server providing the long-term-memory meta-recall tool (hosted on Runtime)",
    "gateway/tools/strands_mcp_server": "MCP server for Strands Agents documentation search",
    "infra-cdk": "Infrastructure definitions in CDK (TypeScript). config.yaml is the starting point for deployment settings",
    "infra-cdk/bin": "CDK app entry point",
    "infra-cdk/lib": "Stack definitions (fast-main / amplify-hosting / cognito / backend / vpc / skills-storage)",
    "infra-cdk/lib/utils": "Shared utilities for config loading, the model registry, and AgentCore IAM roles",
    "infra-cdk/lambdas": "Custom Resource and application Lambdas (cedar-policy / oauth2-provider / pretoken-v3 / feedback / history / sessions / zip-packager)",
    "agent/strands-single-agent": "Deployed Strands Agent implementation (basic_agent.py is the core, models.py resolves models)",
    "agent/utils": "Shared helpers for authentication (JWT claim extraction) and SSM",
    "scripts": "Scripts for deployment, vendoring, and generating this skill",
    "tools/code_interpreter": "AgentCore Code Interpreter wrapper (framework-agnostic)",
    "vibe-context": "Development rules for AI coding assistants (supports the vibe-coding-first design)",
}


class BuildError(Exception):
    """Raised when generation fails."""


def list_repo_files() -> list[str]:
    """Return all git-tracked file paths relative to the repo root.

    Returns:
        Sorted relative paths as reported by ``git ls-files``.

    Raises:
        BuildError: If git is unavailable or the command fails.
    """
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise BuildError(f"git ls-files failed: {exc}") from exc
    return sorted(p for p in result.stdout.split("\0") if p)


def _is_dotenv_secret(rel_path: str) -> bool:
    """Return True for a .env secrets file, but not the committable template.

    .env holds passwords / PII and is git-ignored, so it never reaches the
    git ls-files-driven mirror in practice. This is a defense-in-depth guard:
    if someone force-adds a .env (git add -f), it must still never be mirrored.
    .env.example (the committed, secret-free template) is explicitly allowed.

    Args:
        rel_path: Path relative to the repo root, using forward slashes.

    Returns:
        True when the file is a .env secrets file that must be excluded.
    """
    name = rel_path.rsplit("/", 1)[-1]
    return (name == ".env" or name.startswith(".env.")) and name != ".env.example"


def is_mirrored(rel_path: str) -> bool:
    """Decide whether a tracked file belongs in the repo mirror.

    Args:
        rel_path: Path relative to the repo root, using forward slashes.

    Returns:
        True when the file passes every exclusion rule.
    """
    if _is_dotenv_secret(rel_path):
        return False
    if rel_path in EXCLUDED_FILES:
        return False
    if rel_path.startswith(EXCLUDED_PREFIXES):
        return False
    return not rel_path.endswith(EXCLUDED_SUFFIXES)


def copy_file(src: Path, dest: Path, rel_path: str) -> None:
    """Copy one text file verbatim into the mirror.

    Args:
        src: Source file.
        dest: Destination file (parents are created).
        rel_path: Path used in error messages.

    Raises:
        BuildError: If the file is not valid UTF-8 (binary files must be
            excluded by suffix instead).
    """
    try:
        text = src.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise BuildError(
            f"{rel_path}: not UTF-8 text; extend EXCLUDED_SUFFIXES"
        ) from exc
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")


def build_repo_mirror(references_dir: Path) -> list[str]:
    """Mirror the selected repo files under references/repo/.

    Args:
        references_dir: The skill's references/ output directory.

    Returns:
        The mirrored relative paths (for code-map generation).
    """
    mirrored = [p for p in list_repo_files() if is_mirrored(p)]
    if not mirrored:
        raise BuildError("repo mirror selected 0 files — exclusion rules broken?")
    repo_dir = references_dir / "repo"
    for rel_path in mirrored:
        copy_file(REPO_ROOT / rel_path, repo_dir / rel_path, rel_path)
    return mirrored


def build_code_map(references_dir: Path, mirrored: list[str]) -> None:
    """Generate code-map.md: an annotated file tree of the repo mirror.

    Args:
        references_dir: The skill's references/ output directory.
        mirrored: Relative paths included in the mirror.
    """
    by_dir: dict[str, list[str]] = {}
    for rel_path in mirrored:
        parent = str(Path(rel_path).parent)
        by_dir.setdefault(parent, []).append(Path(rel_path).name)

    lines = [
        "# Code Map",
        "",
        "Complete file listing of the source-code mirror under references/repo/.",
        "When answering implementation or configuration details, locate the",
        "relevant file here first, then read it with file_read (the path is",
        "references/repo/<path below>).",
        "",
    ]
    for directory in sorted(by_dir):
        description = DIR_DESCRIPTIONS.get(directory)
        header = f"## {directory}/" if directory != "." else "## (repository root)"
        lines.append(header)
        if description:
            lines.append(f"\n{description}\n")
        else:
            lines.append("")
        lines.extend(f"- {name}" for name in sorted(by_dir[directory]))
        lines.append("")
    (references_dir / "code-map.md").write_text("\n".join(lines), encoding="utf-8")


def build(output_dir: Path) -> Path:
    """Assemble the complete skill tree under output_dir.

    Args:
        output_dir: Directory that will contain fast-project-guide/.

    Returns:
        The generated skill directory.

    Raises:
        BuildError: If the committed skill source is missing or any
            generation step fails.
    """
    skill_md = SKILL_SRC / "SKILL.md"
    if not skill_md.is_file():
        raise BuildError(f"committed skill source missing: {skill_md}")

    skill_out = output_dir / SKILL_NAME
    if skill_out.exists():
        shutil.rmtree(skill_out)
    skill_out.mkdir(parents=True)

    # 1. Committed layer: SKILL.md + handwritten references.
    for src in sorted(SKILL_SRC.rglob("*")):
        if src.is_file():
            rel = src.relative_to(SKILL_SRC)
            copy_file(src, skill_out / rel, str(rel))

    references_dir = skill_out / "references"
    references_dir.mkdir(exist_ok=True)

    # 2. Generated layers.
    mirrored = build_repo_mirror(references_dir)
    build_code_map(references_dir, mirrored)

    print(f"Built {SKILL_NAME}: {len(mirrored)} mirrored files")
    print(f"Output: {skill_out}")
    return skill_out


def main() -> int:
    """CLI entrypoint.

    Returns:
        Process exit code (0 on success, 1 on build failure).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"output directory (default: {DEFAULT_OUTPUT})",
    )
    args = parser.parse_args()
    try:
        build(args.output.resolve())
    except BuildError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
