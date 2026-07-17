#!/usr/bin/env python3
"""Vendor the aws/agent-toolkit-for-aws skills into skills/agent-toolkit-for-aws/ (flat).

This re-creates skills/agent-toolkit-for-aws/ from a pinned commit of the
upstream agent-toolkit-for-aws repository. Each skill directory (a directory
that contains a SKILL.md) is copied flat as
skills/agent-toolkit-for-aws/<skill-name>/, so the Strands AgentSkills plugin
can discover every skill from the single mount path /mnt/skills (the plugin
only scans one level below each given path).

See skills/agent-toolkit-for-aws/README.md for the rationale.

Usage:
    python scripts/vendor-skills.py [--ref <git-ref-or-sha>]

The default ref is the currently pinned commit. Bump PINNED_REF to take a newer
upstream snapshot.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

UPSTREAM = "https://github.com/aws/agent-toolkit-for-aws.git"
PINNED_REF = "ba1cc8ca4f063d88ca40c6acf3f670e6321b7a7f"

REPO_ROOT = Path(__file__).resolve().parent.parent
DEST = REPO_ROOT / "skills" / "agent-toolkit-for-aws"


def _clone_skills(ref: str, workdir: Path) -> Path:
    """Sparse-clone only the skills/ tree at the given ref. Returns the clone root."""
    clone_dir = workdir / "agent-toolkit-for-aws"
    subprocess.run(
        ["git", "clone", "--filter=blob:none", "--sparse", UPSTREAM, str(clone_dir)],
        check=True,
    )
    subprocess.run(
        ["git", "sparse-checkout", "set", "skills"], cwd=clone_dir, check=True
    )
    subprocess.run(["git", "checkout", ref], cwd=clone_dir, check=True)
    return clone_dir


def _vendor(clone_dir: Path) -> int:
    """Copy each skill directory flat into DEST. Returns the number of skills copied."""
    skills_src = clone_dir / "skills"
    skill_md_files = sorted(skills_src.rglob("SKILL.md"))
    if not skill_md_files:
        print("ERROR: no SKILL.md found in upstream skills/", file=sys.stderr)
        sys.exit(1)

    # Detect skill-name collisions before touching the destination.
    names: dict[str, Path] = {}
    collisions: list[str] = []
    for md in skill_md_files:
        name = md.parent.name
        if name in names:
            collisions.append(name)
        names[name] = md.parent
    if collisions:
        print(
            f"ERROR: skill name collisions would break flat vendoring: {collisions}",
            file=sys.stderr,
        )
        sys.exit(1)

    if DEST.exists():
        shutil.rmtree(DEST)
    DEST.mkdir(parents=True)

    for name, skill_dir in names.items():
        shutil.copytree(skill_dir, DEST / name)

    # Carry the upstream license and notice for Apache-2.0 compliance.
    for license_file in ("LICENSE", "NOTICE"):
        src = clone_dir / license_file
        if src.exists():
            shutil.copy2(src, DEST / license_file)

    return len(names)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default=PINNED_REF, help="git ref or SHA to vendor")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        clone_dir = _clone_skills(args.ref, Path(tmp))
        resolved = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=clone_dir,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        count = _vendor(clone_dir)

    print(f"Vendored {count} skills from {UPSTREAM} @ {resolved}")
    print(f"Destination: {DEST}")
    if resolved != PINNED_REF:
        print(
            f"NOTE: vendored ref {resolved} differs from PINNED_REF {PINNED_REF}; "
            "update PINNED_REF in this script and "
            "skills/agent-toolkit-for-aws/README.md.",
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
