# aws-specialist-agent skills

This directory holds deployment-environment-specific skills based on this project itself. Currently it contains only one: `fast-project-guide` (the self-documentation skill for this demo application). For design decisions, refer to the repository's docs/.

The general-purpose AWS skills vendored from upstream live in a separate directory, `skills/agent-toolkit-for-aws/`, and are unrelated to this one.

## Directory structure

```
skills/aws-specialist-agent/
├── fast-project-guide/      ← [source] git-tracked (hand-written)
│   ├── SKILL.md             routing table (question type → reference to read)
│   └── references/          hand-written explanation layer
│       ├── overview.md
│       ├── architecture.md
│       ├── demo-features.md
│       └── configuration.md
│
└── build/                   ← [generated] git-ignored (.gitignore)
    └── fast-project-guide/  the finished form that scripts/build-project-guide.py rebuilds each time
        ├── SKILL.md             (copied from the source)
        ├── references/
        │   ├── overview.md      (copied)
        │   ├── architecture.md  (copied)
        │   ├── demo-features.md (copied)
        │   ├── configuration.md (copied)
        │   ├── repo/            ★generated: a mirror of git-tracked sources
        │   └── code-map.md      ★generated: an annotated file tree of repo/
```

- Commit `fast-project-guide/` (the source). It contains only the hand-written SKILL.md and the four explanation files.
- Do not commit `build/` (the generated output); it is excluded via `.gitignore`. The two `★` items are assembled by `scripts/build-project-guide.py`.
- What gets distributed to S3 / `/mnt/skills` is **the contents of `build/fast-project-guide/`**. Because CDK's `Source.asset(skills/aws-specialist-agent/build)` zips this directory, whether files are git-tracked is irrelevant to distribution (the actual files generated at synth time are uploaded as-is).

## Generation script

`scripts/build-project-guide.py` assembles `build/fast-project-guide/`.

- The mirror source is taken from `git ls-files` (**git-tracked files**). Excluded are `skills/`, `docs-jp/`, tests, CI configuration, binaries, lock/generated files, non-deployed patterns, and so on (the actual exclusion rules are the `EXCLUDED_*` constants at the top of the script).
- Manual run: `python3 scripts/build-project-guide.py` (the default output destination is `skills/aws-specialist-agent/build/`).

## Applying changes

### Normal: apply via `cdk deploy` (recommended)

`cdk deploy` runs `build-project-guide.py` at synth time (the `execFileSync` in `infra-cdk/lib/skills-storage-stack.ts`). A generation failure fails the entire synth, so a missed generation is never silently distributed.

| Where you changed something                                                           | Applied by `cdk deploy` | Notes                                                                                             |
| ------------------------------------------------------------------------------------- | ----------------------- | ------------------------------------------------------------------------------------------------- |
| `skills/aws-specialist-agent/fast-project-guide/` (SKILL.md, hand-written references) | Yes                     | The build copies it even before commit (working-tree edits)                                       |
| Edits to tracked sources (`backend-stack.ts`, `config.yaml`, etc.)                    | Yes                     | Goes into the `repo/` mirror. The Runtime image is updated at the same time                       |
| **New files (not yet `git add`-ed)**                                                  | **No**                  | Not shown by `git ls-files`, so not in the mirror. **`git add` is required** (see the note below) |

```bash
cd infra-cdk
CONFIG_FILE=config.dev.yaml npx cdk diff FAST-dev     # check the diff
CONFIG_FILE=config.dev.yaml npx cdk deploy FAST-dev --require-approval never
```

Changes take effect from a **new session** (S3 Files applies to the mount of a new runtimeSessionId).

### Note: new files are not in the mirror until you `git add` them

The mirror source is based on `git ls-files`. **A newly created source does not appear in `repo/` until it is `git add`-ed (or committed).** If "I added a new file but the guide doesn't know about it," the cause is almost always a missed add.
(Edits to existing tracked files are reflected even before commit.)

### On-site instant reflection: sync directly to S3 without `cdk deploy`

For when you only want to fix wording in SKILL.md or the docs and do not want to wait for a Runtime redeploy (a few minutes). Because S3 Files is bidirectional sync, updating S3 directly takes effect from a new session (without touching the Runtime or the image).

```bash
python3 scripts/build-project-guide.py

# The destination bucket name differs per environment, so get it dynamically (dev environment example)
SKILLS_BUCKET=$(aws s3api list-buckets --profile default \
  --query 'Buckets[?contains(Name, `skillsbucket`) && contains(Name, `dev`)].Name | [0]' \
  --output text)

aws s3 sync skills/aws-specialist-agent/build/fast-project-guide/ \
  "s3://${SKILLS_BUCKET}/skills/fast-project-guide/" --delete --profile default
```

Afterward, opening a **new chat (a new runtimeSessionId)** in the browser reflects the change. Note that it is not reflected immediately in the mount of an existing session.
