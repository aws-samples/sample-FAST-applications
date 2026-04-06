# Local Docker Testing

This guide covers how to test the agent and CopilotKit Runtime locally without the full frontend.

## Test the agent directly

The agent container exposes a standard AG-UI endpoint at `POST /invocations`. You can test it with `curl` before connecting the frontend:

```bash
# Start just the agent
cd docker && docker compose up agent

# Send a test message
curl -N -X POST http://localhost:8080/invocations \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer test-token" \
  -d '{
    "threadId": "test-thread-1",
    "runId": "run-1",
    "state": {},
    "messages": [{"id": "msg-1", "role": "user", "content": "Hello, what tools do you have?"}],
    "tools": [],
    "context": [],
    "forwardedProps": {"actor_id": "test-user"}
  }'
```

You should see AG-UI SSE events streamed back: `RUN_STARTED`, `TEXT_MESSAGE_CONTENT`, `RUN_FINISHED`.

## Test the CopilotKit bridge

The bridge (CopilotKit Runtime) runs on port 3001. It accepts CopilotKit protocol requests and forwards them to the agent:

```bash
cd docker && docker compose up agent bridge
```

The bridge endpoint is `POST http://localhost:3001/copilotkit`. This is what the frontend's `runtimeUrl` points to.

## Using the test script

The `scripts/test-agent.py` utility can test the deployed (AWS) agent directly:

```bash
cd scripts
pip install -r requirements.txt

# Test against deployed stack
python test-agent.py "Show me a bar chart of sales data"

# Test locally
python test-agent.py --local "What can you do?"
```

## Health checks

```bash
# Agent health
curl http://localhost:8080/ping
# Expected: {"status": "Healthy"}

# Bridge (CopilotKit Runtime) - returns 404 on GET /copilotkit which is expected
curl -I http://localhost:3001/copilotkit
```

## Rebuilding after code changes

```bash
# Rebuild agent only
docker compose up --build agent

# Rebuild everything
docker compose up --build
```