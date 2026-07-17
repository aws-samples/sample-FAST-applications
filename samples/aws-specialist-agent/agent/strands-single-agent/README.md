# Strands Single Agent Pattern

This pattern uses the [Strands Agents](https://github.com/strands-agents/strands-agents) framework to build a single agent with Gateway tool access, Code Interpreter, and AgentCore Memory for conversation history.

## Features

- **Token-Level Streaming**: True token-by-token streaming via `agent.stream_async()`
- **AgentCore Memory**: Conversation history persisted across requests via `AgentCoreMemorySessionManager`, with optional long-term memory for cross-session fact recall
- **Code Interpreter**: Secure Python execution via the official `strands_tools.code_interpreter.AgentCoreCodeInterpreter`
- **Gateway Integration**: Access Lambda-based tools through AgentCore Gateway (MCP protocol with OAuth2 auth)
- **Skills**: AWS skills mounted from S3 Files at `/mnt/skills`, surfaced through the Strands `AgentSkills` plugin; `file_read` reads a skill's reference files
- **Secure Identity**: User identity extracted from validated JWT token (`RequestContext`), not from payload

## Architecture

```
User Request
    |
BedrockAgentCoreApp (basic_agent.py)
    |
Strands Agent (model resolved per request from the registry)
    |
    +-- AgentCore Memory (conversation history)
    |     AgentCoreMemorySessionManager
    |
    +-- Code Interpreter
    |     AgentCoreCodeInterpreter (strands_tools)
    |
    +-- Gateway MCP Client (streamable HTTP)
    |     Lambda-based tools via AgentCore Gateway
    |
    +-- Skills (/mnt/skills via AgentSkills) + file_read
```

## File Structure

```
agent/strands-single-agent/
├── basic_agent.py        # Main entrypoint (BedrockAgentCoreApp), tools, system prompt
├── models.py             # Model factory: resolve a logical key to a Bedrock/OpenAI model
├── tools/
│   └── gateway.py        # Gateway MCP client wiring (user identity propagation)
├── requirements.txt      # Pinned dependencies
└── Dockerfile            # Container build (Python 3.13)
```

## Available Tools

| Tool               | Source            | Description                                           |
| ------------------ | ----------------- | ----------------------------------------------------- |
| `code_interpreter` | Code Interpreter  | Execute Python in a secure sandbox (`strands_tools`)  |
| `file_read`        | `strands_tools`   | Read a skill's reference files from `/mnt/skills`     |
| `skills`           | `AgentSkills`     | List and activate AWS skills mounted at `/mnt/skills` |
| Gateway tools      | AgentCore Gateway | Lambda / MCP-server tools discovered via MCP          |

## Model

- The chat model is resolved per request from the model registry
  (`infra-cdk/lib/utils/model-registry.ts`); `models.py` maps the selected
  logical key to the physical Bedrock or OpenAI-on-Bedrock model. See the
  [Agent Configuration Guide](../../docs/AGENT_CONFIGURATION.md).

## Streaming Events

The agent yields SSE `data: {json}` lines via `agent.stream_async()`. The frontend parser at `frontend/src/lib/agentcore-client/parsers/strands.ts` handles these event types:

| Event          | Format                                                                | Description              |
| -------------- | --------------------------------------------------------------------- | ------------------------ |
| Text           | `{"data": "text"}`                                                    | Token-level text content |
| Tool use start | `{"current_tool_use": {...}, "delta": {"toolUse": {"input": ""}}}`    | Tool invocation begins   |
| Tool use delta | `{"current_tool_use": {...}, "delta": {"toolUse": {"input": "..."}}}` | Streaming tool input     |
| Tool result    | `{"message": {"role": "user", "content": [{"toolResult": {...}}]}}`   | Tool execution result    |
| Result         | `{"result": {"stop_reason": "end_turn"}}`                             | Agent finished           |
| Lifecycle      | `{"init_event_loop": true}` / `{"start_event_loop": true}`            | Agent lifecycle events   |

## Memory Integration

This pattern uses **AgentCore Memory** for conversation persistence and optional long-term recall:

**Short-term memory** (always active):

1. `MEMORY_ID` environment variable provides the memory resource ID
2. `AgentCoreMemoryConfig` is initialized with `memory_id`, `session_id`, and `actor_id` (user ID)
3. `AgentCoreMemorySessionManager` handles storing/retrieving conversation history
4. Memory is tied to the `runtimeSessionId` from the client

**Long-term memory** (opt-in via `use_long_term_memory: true` in `config.yaml`):

1. The CDK stack passes `USE_LONG_TERM_MEMORY=true` to the agent runtime
2. The agent configures a `RetrievalConfig` for the `/facts/{actorId}` namespace
3. AgentCore extracts facts from conversations asynchronously and stores them per user (keyed by Cognito `userId`)
4. On each turn, relevant facts are retrieved and injected into the agent context, enabling cross-session personalization
5. Additional costs apply: $0.75/1,000 records stored + $0.50/1,000 retrieval calls

See [Memory Integration Guide](../../docs/MEMORY_INTEGRATION.md) for full details.

## Security

- **User identity**: Extracted from the validated JWT token via `RequestContext`, not from the payload body
- **STACK_NAME validation**: Validated for alphanumeric format before use in SSM parameter paths
- **Payload validation**: Required fields (`prompt`, `runtimeSessionId`) validated before processing
- **Gateway auth**: OAuth2 client credentials flow via Cognito for machine-to-machine authentication with user identity propagation for Cedar policy evaluation

## Deployment

```bash
cd infra-cdk
# Set pattern in config.yaml:
#   backend:
#     pattern: strands-single-agent
#     deployment_type: docker  # or zip
cdk deploy
```

Both Docker and ZIP deployment types are supported.

## Dependencies

See [`requirements.txt`](requirements.txt) for the pinned dependency set
(`strands-agents[openai]`, `strands-agents-tools`, `bedrock-agentcore`, `mcp`,
`PyJWT[crypto]`).
