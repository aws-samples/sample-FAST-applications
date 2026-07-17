# Cedar policies for AgentCore Gateway

This directory holds the Cedar policy statements that AgentCore Policy enforces
on incoming Gateway tool calls. The CDK stack scans every `*.cedar` file in
this directory (sorted lexicographically) and creates **one AgentCore Policy
per file** inside a single Policy Engine attached to the Gateway.

## Why one file per policy?

The `bedrock-agentcore:CreatePolicy` API accepts exactly **one Cedar statement
per call** — putting multiple `permit(...)` blocks into one document fails with
`unexpected token \`permit\``. The Custom Resource Lambda
(`infra-cdk/lambdas/cedar-policy/index.py`) loops through the files in this
directory and calls `CreatePolicy` once per file, so each file must contain
exactly one Cedar statement.

## How it works

1. The CDK stack reads each `*.cedar` file at synth time.
2. Lines starting with `//` are stripped (the AgentCore API only accepts raw
   Cedar statements; comments are documentation only).
3. The placeholder `{{GATEWAY_ARN}}` is replaced with the actual Gateway ARN at
   deploy time.
4. The Custom Resource Lambda creates one policy per file inside the Policy
   Engine, then attaches the engine to the Gateway with `mode: "ENFORCE"`.

## How JWT claims map to Cedar principal tags

The Gateway's JWT Authorizer maps M2M JWT claims to Cedar principal tags:

| JWT claim    | Cedar access                     |
| ------------ | -------------------------------- |
| `department` | `principal.getTag("department")` |
| `role`       | `principal.getTag("role")`       |
| `user_id`    | `principal.getTag("user_id")`    |

These are **CUSTOM** claims injected by the Cognito V3 Pre-Token Lambda
(`infra-cdk/lambdas/pretoken-v3/index.py`) via `claimsToAddOrOverride`. The
claim names are arbitrary — keep the Pre-Token Lambda output and these Cedar
files in sync.

Standard claims (`sub`, `iss`, `client_id`, `scope`, `exp`, …) are also
exposed as principal tags and are populated automatically by Cognito.

## Cedar action name format

`<TargetName>___<tool_name>` (triple underscore). The target name comes from
the `name` field of the `CfnGatewayTarget` resource in CDK. Examples:

- `sample-tool-target___text_analysis_tool`
- `aws-mcp___aws___list_regions`

## Adding a new policy

1. Create a new file `NN-short-purpose.cedar` (use a 2-digit prefix for
   ordering; ordering is cosmetic — policies are independent).
2. Write **exactly one** `permit` (or `forbid`) statement.
3. Reference `{{GATEWAY_ARN}}` for the resource — CDK replaces it at deploy.
4. Run `cdk deploy` — the Custom Resource Lambda detects the change and
   recreates all managed policies (delete-then-create within the Policy
   Engine; the Engine itself is preserved).

## Cedar deny-by-default

Cedar denies any request that is not explicitly permitted by a matching
`permit` statement. Therefore you do **not** need an explicit `forbid`
statement to block a department — simply omit it from the `when` block.

## Current files

| File                           | Purpose                                                   |
| ------------------------------ | --------------------------------------------------------- |
| `01-sample-tool.cedar`         | Allow finance/engineering to call `sample-tool-target`    |
| `02-aws-mcp-read.cedar`        | Allow finance/engineering to call AWS MCP read-only tools |
| `03-aws-mcp-destructive.cedar` | Allow finance only to call destructive AWS MCP tools      |
| `04-ltm-mcp.cedar`             | Allow per-user access to the LTM listing MCP tool         |
| `05-strands-mcp.cedar`         | Allow finance/engineering to call Strands docs MCP tools  |
| `06-web-search.cedar`          | Allow finance/engineering to call the Web Search tool     |
