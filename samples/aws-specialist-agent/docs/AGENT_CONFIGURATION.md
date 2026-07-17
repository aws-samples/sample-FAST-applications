# Agent Configuration Guide

FAST supports any agent framework that can run in a container. This guide covers how to use existing patterns, create your own, and configure agent behavior.

---

## Existing Patterns

### Strands Single Agent Pattern

**Location**: `agent/strands-single-agent/`

A basic conversational agent using the Strands framework with AgentCore Memory integration.

**What This Agent Does**:

- Multi-turn conversational chat
- Maintains conversation history with short-term memory
- **Optional long-term memory**: When `use_long_term_memory: true` is set in `config.yaml`, the agent uses a `SemanticMemoryStrategy` to extract and recall facts across sessions (keyed by Cognito user ID). See [Memory Integration Guide](MEMORY_INTEGRATION.md#enabling-long-term-memory) for details.
- Streams responses for better UX
- Authenticated via Cognito (user identity tracked in memory)

**Key Configuration Files**:

- **Agent Logic**: `agent/strands-single-agent/basic_agent.py` - Main agent implementation with memory integration, model configuration, and streaming logic
- **Python Dependencies**: `agent/strands-single-agent/requirements.txt` - Required Python packages (Strands, bedrock-agentcore, etc.)
- **Container Config**: `agent/strands-single-agent/Dockerfile` - Docker container definition (only used for `deployment_type: docker`)
- **Infrastructure**: `infra-cdk/lib/backend-stack.ts` - CDK configuration for memory resource and runtime deployment

**Model Configuration** (registry-driven):

The chat model is no longer hardcoded in `basic_agent.py`. Users pick a model in
the UI, and the selectable list is defined once in the registry:

```typescript
// infra-cdk/lib/utils/model-registry.ts
export const SELECTABLE_MODELS: readonly SelectableModel[] = [
  {
    key: "opus-4.8",
    label: "Claude Opus 4.8",
    id: "global.anthropic.claude-opus-4-8",
    provider: "anthropic",
  },
  {
    key: "sonnet-5",
    label: "Claude Sonnet 5",
    id: "global.anthropic.claude-sonnet-5",
    provider: "anthropic",
  },
  {
    key: "sonnet-4.6",
    label: "Claude Sonnet 4.6",
    id: "global.anthropic.claude-sonnet-4-6",
    provider: "anthropic",
    default: true,
  },
  // ...add a model here (one line) and redeploy
]
```

The CDK derives the backend allowlist (`MODEL_MAP` / `DEFAULT_MODEL_KEY` env vars)
and the frontend picker options (`aws-exports.json`) from this single source, so
they cannot drift. To change or add a model, edit this array and redeploy; no
change to `basic_agent.py` is needed. `agent/strands-single-agent/models.py`
resolves the selected logical key to the physical model. Both providers are
live: Claude models run on bedrock-runtime (Converse) and OpenAI GPT models on
the bedrock-mantle OpenAI Responses API. The registry has no `available` flag —
every entry is selectable.

**System Prompt** (`agent/strands-single-agent/basic_agent.py`):

```python
system_prompt = """You are a helpful assistant. Answer questions clearly and concisely."""
```

**After making changes**: See [Deployment Guide](DEPLOYMENT.md) for redeployment instructions.

---

## Creating Your Own Agent Pattern

### Step 1: Create Pattern Directory

```bash
mkdir -p agent/my-custom-agent
cd agent/my-custom-agent
```

### Step 2: Implement Your Agent

Create your agent code that:

- Accepts HTTP requests from AgentCore Runtime
- Processes user queries
- Returns responses (streaming or non-streaming)
- Integrates with AgentCore Memory (optional)

**Example Structure**:

```python
from bedrock_agentcore.runtime import BedrockAgentCoreApp, RequestContext
from utils.auth import extract_user_id_from_context

app = BedrockAgentCoreApp()

@app.entrypoint
async def agent_handler(payload, context: RequestContext):
    """Main entrypoint for the agent"""
    user_query = payload.get("prompt")
    session_id = payload.get("runtimeSessionId")

    # Extract user ID securely from the validated JWT token
    # instead of trusting the payload body (which could be manipulated)
    user_id = extract_user_id_from_context(context)

    # Your agent logic here
    # ...

    yield response

if __name__ == "__main__":
    app.run()
```

### Step 3: Create Dockerfile (for Docker deployment only)

If using `deployment_type: docker` in your config, create a Dockerfile:

```dockerfile
FROM public.ecr.aws/docker/library/python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

CMD ["python", "your_agent.py"]
```

**For ZIP deployment**: No Dockerfile is needed. The ZIP packager automatically bundles your `agent/<pattern>/` directory along with `agent/utils/`, `gateway/`, and `tools/` directories, plus dependencies from `requirements.txt`.

### Step 4: Update CDK Configuration

In `infra-cdk/config.yaml`:

```yaml
backend:
  pattern: "my-custom-agent" # Your pattern directory name
```

**If your agent needs additional AWS services** (Knowledge Bases, DynamoDB, S3, etc.), modify the CDK stacks in `infra-cdk/lib/`:

**Example**: Adding a Knowledge Base

```typescript
// Create your knowledge base construct
const knowledgeBase = new bedrock.CfnKnowledgeBase(this, "KB", {
  name: "MyKnowledgeBase",
  // ... configuration
});

// Add to agent environment variables in backend-stack.ts
EnvironmentVariables: {
  KNOWLEDGE_BASE_ID: knowledgeBase.attrKnowledgeBaseId,
  // ... other vars
}
```

### Step 5: Deploy

See the [Deployment Guide](DEPLOYMENT.md) for complete deployment instructions.

## Design notes

Decisions specific to this derivative:

- **Selectable models, single source of truth**: the model picker is driven by a registry rather than inferring availability from arbitrary IDs. The registry declares display name, provider, inference profile or endpoint, and capability flags; the frontend and backend both read from it so the picker and the runtime stay in sync.
- **OpenAI models served via Bedrock**: GPT models are added as registry entries and called through the OpenAI Responses API endpoint that Bedrock exposes. This keeps a single authentication and observability path; there is no separate OpenAI key wiring.
- **Direct call simplification**: the OpenAI integration calls the in-region endpoint directly. The earlier cross-region peering setup was removed once the in-region endpoint became available, simplifying the network model and lowering latency.
- **No temperature flag at construction**: some reasoning-tier endpoints reject `temperature`, so the model construction path never sets it; per-model registry flags govern any parameters that vary by endpoint.
- **System prompt is sectioned**: the system prompt is split into well-named sections (Language, Skills first, AWS guidance, Tool routing) so additions land in the right place and the prompt stays maintainable across model providers.
