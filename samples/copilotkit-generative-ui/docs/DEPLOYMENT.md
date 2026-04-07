# Deployment Guide

This guide expands on the deployment steps in the [README](../README.md).

## Prerequisites

| Tool | Version | Notes |
|---|---|---|
| AWS CLI | any | `aws configure` must be set up — see [getting started](https://docs.aws.amazon.com/cli/latest/userguide/cli-chap-getting-started.html) |
| Node.js | 18+ | Required to build the CopilotKit Runtime Lambda |
| Python | 3.8+ | Required for deploy scripts |
| Docker | running | Required to build the agent container image |

## Configuration

Copy and edit the config file:

```bash
cp config.yaml.example config.yaml
```

| Field | Description |
|---|---|
| `stack_name_base` | Short prefix for all AWS resources. Max 35 chars. Used in resource names, SSM paths, and log groups. Must be unique per account/region. |
| `admin_user_email` | A Cognito user is auto-created and credentials emailed here. Leave blank to create a user manually in the Cognito console after deploy. |

## Deploy

```bash
./deploy-langgraph.sh       # LangGraph agent
./deploy-strands.sh         # Strands agent
```

Both scripts run in sequence: CDK bootstrap (if needed), CDK deploy, then frontend deploy to Amplify. The full deploy takes 10–20 minutes on first run, faster on subsequent deploys.

Pass `--skip-frontend` to update only the agent and infrastructure without rebuilding the frontend.

## What gets deployed

| Resource | Description |
|---|---|
| Cognito User Pool | Authentication for the browser frontend |
| ECR Repository | Stores the agent Docker image |
| AgentCore Runtime | Serverless container runtime for the agent |
| AgentCore Memory | Persistent conversation history |
| AgentCore Gateway | MCP endpoint for external tools (OAuth2 M2M) |
| CopilotKit Lambda | Node.js bridge between browser and AgentCore |
| API Gateway | Public HTTPS endpoint for the CopilotKit Lambda |
| Amplify Hosting | Serves the React frontend |
| SSM Parameters | Stores runtime config (Gateway URL, etc.) |

## Deployment types

The `deployment_type` field in `config.yaml` controls how the agent is packaged:

- `docker` (default) — builds an ARM64 container image and pushes to ECR. Requires Docker running locally.
- `zip` — packages the agent as a ZIP file. No Docker required, but cold starts are slower.

## Updating after changes

**Agent code changes:**
```bash
./deploy-langgraph.sh --skip-frontend
```

**Frontend changes only:**
```bash
python scripts/deploy-frontend.py
```

**Full redeploy:**
```bash
./deploy-langgraph.sh
```

## Tear down

Delete all AWS resources to stop charges:

```bash
cd infra-cdk && npx cdk destroy --all
```

This removes all resources including the Cognito user pool, ECR images, AgentCore resources, and Amplify app.