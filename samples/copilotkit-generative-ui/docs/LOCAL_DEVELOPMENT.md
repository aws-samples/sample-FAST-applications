# Local Development

This guide explains how to run the full stack locally using Docker Compose.

## How it works

Local dev runs three services:

| Service | Port | Description |
|---|---|---|
| `agent` | 8080 | LangGraph or Strands agent, hot-reloads on `.py` changes |
| `bridge` | 3001 | CopilotKit Runtime (Node.js server, same code as the Lambda) |
| `frontend` | 3000 | Vite dev server with HMR |

The browser connects to the bridge on port 3001. The bridge connects to the agent on port 8080. This mirrors the production chain (`Browser → CopilotKit Lambda → AgentCore → Agent`) but without AWS auth overhead.

> **Note:** A deployed AWS stack is still required for AgentCore Memory and Gateway. The `up.sh` script reads STACK_NAME and MEMORY_ID from your `.env` and fetches the necessary config from CloudFormation.

## Prerequisites

- Docker running locally
- A deployed stack (run `./deploy-langgraph.sh` or `./deploy-strands.sh` first)
- AWS credentials exported as environment variables (Docker containers cannot read `~/.aws/credentials`)

## Setup

```bash
cd docker
cp .env.example .env
```

Edit `.env`:

```bash
# Which agent to run
AGENT=langgraph   # or: strands

# From your deployed stack
STACK_NAME=my-copilotkit-agentcore-lg
AWS_DEFAULT_REGION=us-east-1

# AWS credentials (must be env vars, not ~/.aws/credentials)
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_SESSION_TOKEN=...   # if using temporary credentials
```

`MEMORY_ID` and other stack outputs are resolved automatically by `up.sh` from CloudFormation.

## Start

```bash
./up.sh --build   # first run or after dependency changes
./up.sh           # subsequent runs (faster)
```

Open `http://localhost:3000` and sign in with your Cognito credentials.

## Hot reload

**Agent code** — any `.py` file change under `agents/` triggers an automatic container restart via Docker Compose `watch`. No manual rebuild needed.

**Frontend** — Vite's HMR updates the browser instantly on save.

**Bridge (CopilotKit Runtime)** — changes to `infra-cdk/lambdas/copilotkit-runtime/src/` require a rebuild:
```bash
docker compose up --build bridge
```

## Gateway tools

AgentCore Gateway (MCP tools) requires a deployed stack and are unavailable locally. The agent detects missing Gateway credentials and continues without them — you'll see a warning in the agent logs. All other features (generative UI, shared state, memory) work locally.

## Troubleshooting

**Agent won't start** — check that AWS credentials are set and the deployed stack is healthy:
```bash
aws sts get-caller-identity
aws cloudformation describe-stacks --stack-name $STACK_NAME
```

**Frontend can't connect** — verify the bridge is healthy:
```bash
curl http://localhost:3001/copilotkit
```

**View logs:**
```bash
docker compose logs -f agent
docker compose logs -f bridge
docker compose logs -f frontend
```