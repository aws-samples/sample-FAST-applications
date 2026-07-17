# Configuration Reference

For questions about configuration values, first use this file to understand the structure, then **always read the actual files under repo/ with file_read to confirm the current values** before answering.

## Configuration entry point: repo/infra-cdk/config.yaml

The overall configuration for CDK deployment. Main keys:

| Key                                     | Meaning                                                     | Current demo value (confirm against the actual file) |
| --------------------------------------- | ----------------------------------------------------------- | ---------------------------------------------------- |
| stack_name_base                         | Base for the stack name                                     | fast-aws-specialist                                  |
| admin_user_email                        | Admin user email (automatically added to the finance group) | Placeholder                                          |
| backend.pattern                         | Agent pattern to deploy                                     | strands-single-agent                                 |
| backend.deployment_type                 | docker (ECR image) or zip                                   | docker                                               |
| backend.network_mode                    | PUBLIC or VPC                                               | VPC (prerequisite for S3 Files)                      |
| backend.vpc_management                  | CDK (build a VpcStack) or EXISTING                          | CDK                                                  |
| backend.vpc_cidr / availability_zones   | Overrides for multi-environment / cross-account deployment  | Defaults (10.20.0.0/16, us-east-1b+1d)               |
| backend.use_long_term_memory            | Enable LTM                                                  | true                                                 |
| backend.ltm_top_k / ltm_relevance_score | LTM search parameters                                       | 10 / 0.3                                             |
| backend.skills.enabled / mount_path     | Skills mount                                                | true / /mnt/skills                                   |
| backend.runtime_lifecycle               | idle/max lifetime                                           | Defaults (idle 3600s)                                |

The configuration schema and the default-value resolution logic are in repo/infra-cdk/lib/utils/config-manager.ts.

## Model registry: repo/infra-cdk/lib/utils/model-registry.ts

The single source of truth for selectable models. For each model it defines the modelKey, the physical model id, the provider, capability flags (such as supportsTemperature), and available, and CDK distributes them to both the Runtime environment variables and the front-end configuration. Resolution and allowlist validation on the Agent side are in repo/agent/strands-single-agent/models.py.

## Main Runtime environment variables (injected by backend-stack.ts)

| Variable                                               | Purpose                                          |
| ------------------------------------------------------ | ------------------------------------------------ |
| MEMORY_ID                                              | The AgentCore Memory ID                          |
| USE_LONG_TERM_MEMORY / LTM_TOP_K / LTM_RELEVANCE_SCORE | LTM settings                                     |
| SKILLS_MOUNT_PATH                                      | Skills mount path (/mnt/skills)                  |
| GATEWAY_URL and other Gateway/M2M variables            | Gateway connection and OAuth2 Client Credentials |
| MODEL_REGISTRY variables                               | Model resolution (referenced by models.py)       |

For the exact list, read the environment-variable assembly section of repo/infra-cdk/lib/backend-stack.ts.

## Cedar policies: repo/gateway/policies/

Six files: 01-sample-tool / 02-aws-mcp-read / 03-aws-mcp-destructive / 04-ltm-mcp / 05-strands-mcp / 06-web-search. Each defines a permit per department (finance / engineering) plus deny-by-default. Deployment is performed by the Custom Resource Lambda in infra-cdk/lambdas/cedar-policy/.

## The Agent itself: repo/agent/strands-single-agent/

- basic_agent.py — The entry point. Contains the SYSTEM_PROMPT, tool configuration (Gateway MCP / Code Interpreter / file_read), the AgentSkills plugin, the Memory session manager, event slimming, and the warmup short-circuit. The Code Interpreter uses the official strands_tools.code_interpreter.AgentCoreCodeInterpreter
- models.py — Resolution of modelKey → physical model, and allowlist validation
- Dockerfile — An ARM64 image. What goes into the container is agent/strands-single-agent + agent/utils + gateway/ (the in-house Code Interpreter wrapper under the root tools/ has been retired)

## Skill distribution (including this skill itself)

- repo/infra-cdk/lib/skills-storage-stack.ts — An S3 bucket + S3 Files FileSystem/MountTarget/AccessPoint. A BucketDeployment syncs the build output of skills/agent-toolkit-for-aws/ and skills/aws-specialist-agent/ (this skill) to the skills/ prefix
- scripts/build-project-guide.py — Generates this skill's references (the repo mirror and code-map)
- Update flow: Once uploaded to S3, changes take effect from a new session (S3 Files bidirectional sync; no Runtime redeploy required)

## Operational scripts (repo/scripts/)

- deploy-frontend.py — Builds the front end and deploys it to Amplify (takes the stack name as an argument)
- create-demo-users.py — Bulk-creates Cognito users for the showcase. Supports create / verify / cleanup. Secrets (shared password, email prefix/domain) are read from scripts/.env (not tracked in git), and the User Pool ID is resolved dynamically from the CloudFormation outputs
- vendor-skills.py — Re-vendors the upstream AWS skills into skills/agent-toolkit-for-aws/
- build-project-guide.py — Generates this skill (see above)
- Handling of secrets: scripts/.env is in .gitignore, and only the template scripts/.env.example is committed. .env is not included in this guide's repo mirror either (because only git-tracked files are mirrored)
