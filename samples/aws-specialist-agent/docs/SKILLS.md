# Skills

This deployment mounts a curated set of "skills" (self-describing capability bundles) into the runtime via S3 Files. Skills are how the agent gains domain knowledge without bloating the model's system prompt.

## How skills are mounted

- Skills live under `skills/agent-toolkit-for-aws/` (pinned third-party content, kept verbatim with their LICENSE/NOTICE) and `skills/aws-specialist-agent/` (skills authored for this project).
- A build step (`scripts/build-project-guide.py`) assembles the project-specific guide and uploads everything to an S3 bucket.
- The AgentCore Runtime container mounts the bucket via S3 Files at a known path. The agent loads skills lazily by reading `SKILL.md` for each capability it needs.

## Design notes

- **S3 Files over a packed image**: skills change more often than the runtime image. Mounting via S3 Files lets a skill update propagate without redeploying the runtime.
- **Vendored, not submoduled**: third-party skills were originally consumed via a git submodule, then switched to a vendored copy with a pinned commit hash recorded next to the LICENSE. Vendoring avoids supply-chain surprises and lets the build run without submodule init.
- **Mount path vs. VPC trade-off**: S3 Files requires the runtime to reach S3. In closed-network deployments this means provisioning an S3 (Gateway) endpoint. The trade-off (extra endpoint vs. ability to update skills out of band) was resolved in favour of S3 Files.
- **Strict IAM on the S3 Files execution role**: the role that the runtime uses to fetch skills is scoped to the specific bucket/prefix used for skills and validated against AgentCore's IAM requirements at deploy time.
- **Self-describing project guide**: `skills/aws-specialist-agent/fast-project-guide/` is generated from the live source tree (sanitized) so the deployed agent can answer questions like "how is this app put together?" against the actual code, not a stale doc.
- **Official Code Interpreter**: the AgentCore-provided Code Interpreter tool is used directly rather than reimplementing a sandbox; framework-specific wrappers (`tools/code_interpreter/`) bridge it to the chosen agent SDK.
- **Auxiliary Lambdas attached to the closed VPC**: helper Lambdas (feedback, history, session, oauth2-provider, zip-packager, pre-token) all run inside the same VPC as the runtime to keep traffic on the private path; their security groups allow only the explicit endpoints they need.
