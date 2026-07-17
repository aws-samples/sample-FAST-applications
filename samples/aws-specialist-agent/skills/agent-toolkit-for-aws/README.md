# Vendored AWS Skills

This directory is a **vendored (verbatim copy)** of the `skills/` from [aws/agent-toolkit-for-aws](https://github.com/aws/agent-toolkit-for-aws). It is mounted onto AgentCore Runtime via S3 Files and used from the Strands `AgentSkills` plugin (Phase 2).

## Provenance

- Repository: `aws/agent-toolkit-for-aws`
- Pinned commit: `ba1cc8ca4f063d88ca40c6acf3f670e6321b7a7f`
- License: Apache License 2.0 (`LICENSE` / `NOTICE` bundled)
- Vendoring date: 2026-05-29

## Why vendoring instead of a submodule

- This repository's CI (`.github/workflows/*`) uses `actions/checkout@v4` without specifying `submodules:`, so with a submodule the contents would not be fetched in CI, and the CDK `BucketDeployment` would read an empty tree, causing a **silent failure** of 0 skills in S3
- With vendoring, everything is committed as ordinary files and is reliably present on clone / in CI / on another machine

## Why a flat structure

The `AgentSkills` plugin only searches for SKILL.md "directly under the path you pass, or one level down in a subdirectory" (verified on real hardware). Leaving the upstream multi-level `core-skills/` / `specialized-skills/<category>/<skill>/` layout as-is would cause misses, so all skills are expanded into a flat `<skill-name>/` structure. Skill names (directory names) have been confirmed to have no duplicates upstream.

This means:

- All skills (43) can be recognized with a **single path**, `AgentSkills(skills=["/mnt/skills"])`
- It is also consistent with the S3 Files constraint that the mountPath be a single level (`/mnt/[a-zA-Z0-9._-]+/?`)

## Contents

- core-skills: 13 (`amazon-bedrock`, `aws-cdk`, `aws-iam`, `aws-serverless` ...)
- specialized-skills: 30 (`securing-s3-buckets`, `debugging-lambda-timeouts`, `creating-production-vpc-multi-az` ...)
- 43 skills in total. Each skill is a whole directory containing `SKILL.md` + (if present) `references/`, etc.

## How to update

```bash
# Re-import the latest from upstream (when you want to bump the pinned commit)
python scripts/vendor-skills.py
```

For details, see `scripts/vendor-skills.py`. If there is an upstream structural change (such as a skill-name duplication occurring), the script warns about it.
