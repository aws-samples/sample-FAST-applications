# AgentCore Code Interpreter Integration

This document explains the architectural decisions for integrating Amazon Bedrock AgentCore Code Interpreter into FAST.

## What is AgentCore Code Interpreter?

Amazon Bedrock AgentCore Code Interpreter is a fully managed capability that enables AI agents to execute code securely in isolated sandbox environments. Key features:

- Secure code execution in containerized environments
- Multiple language support (Python, JavaScript, TypeScript)
- Pre-built runtimes with common libraries
- Session management with state persistence
- Long execution duration (default 15 minutes, up to 8 hours)

**Documentation**: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/code-interpreter-tool.html

## Why Direct Integration (Not Gateway)?

FAST integrates Code Interpreter **directly into agents** rather than through the Gateway. Here's why:

### Approach 1: Direct Integration ✅ (Chosen)

**Architecture**: `Agent → Code Interpreter SDK → Code Interpreter Service`

**Pros**:

- **Simpler implementation** - Minimal code, no additional infrastructure
- **Lower latency** - No Gateway/Lambda hops
- **Lower cost** - No Lambda invocations
- **Session management** - Code Interpreter maintains state across calls
- **Follows AWS patterns** - Matches official documentation examples
- **Better error handling** - Direct access to Code Interpreter errors

**Cons**:

- Not discoverable through Gateway
- Requires agent redeployment for updates
- Tool logic lives in agent code

### Approach 2: Gateway Integration ❌ (Not Chosen)

**Architecture**: `Agent → Gateway → Lambda → Code Interpreter SDK → Code Interpreter Service`

**Pros**:

- Consistent with Gateway pattern
- Discoverable through MCP
- Independent deployment

**Cons**:

- **More complex** - Lambda wrapper + Gateway target + IAM roles
- **Higher latency** - Additional hops in request path
- **Higher cost** - Lambda invocations + Code Interpreter usage
- **Session complexity** - Lambda must manage sessions across cold starts
- **No AWS references** - No official examples of this pattern
- **Not intended use case** - Code Interpreter is a built-in service, not a custom tool

### Decision Rationale

Code Interpreter is a **built-in AgentCore service**, similar to Bedrock models or AgentCore Memory. AWS designed it for direct integration, not to be proxied through Gateway. Gateway is meant for **custom Lambda-based tools**, not built-in services.

**Comparison**:

| Aspect      | Direct           | Gateway        |
| ----------- | ---------------- | -------------- |
| Complexity  | Low              | High           |
| Latency     | ~100ms           | ~300-500ms     |
| Cost        | CI only          | Lambda + CI    |
| AWS Pattern | ✅ Documented    | ❌ No examples |
| Use Case    | Built-in service | Custom tools   |

## Implementation Architecture

The agent uses the official Strands Code Interpreter tool
(`strands_tools.code_interpreter.AgentCoreCodeInterpreter`) directly, rather
than a hand-written wrapper. The integration lives entirely in the agent
entrypoint:

```
agent/strands-single-agent/
└── basic_agent.py    # Imports AgentCoreCodeInterpreter and registers its tool
```

### Key Components

**Agent Integration** (`agent/strands-single-agent/basic_agent.py`):

```python
from strands_tools.code_interpreter import AgentCoreCodeInterpreter

# Bind the sandbox to the conversation session so repeat calls in the same
# conversation reconnect to the same AgentCore sandbox (warm reconnect)
# instead of cold-creating a new one.
code_interpreter_tool = AgentCoreCodeInterpreter(
    region=region, session_name=session_id
)

# Register the tool alongside the Gateway MCP client and file_read.
tools = [gateway_client, code_interpreter_tool.code_interpreter, file_read]
```

### Design Principles

1. **Use the maintained tool** - the official `strands_tools` Code Interpreter
   tracks the AgentCore Code Interpreter API, so there is no in-repo wrapper to
   keep in sync.
2. **Session-bound sandbox** - `session_name` is set to the conversation
   `session_id`, so the tool's module-level cache reconnects to the same
   sandbox across invocations (warm reconnect vs cold create). This compounds
   with the VPC cold-start mitigation.
3. **Direct integration** - the tool talks to the Code Interpreter service
   directly (no Gateway/Lambda hop), as argued in the decision above.

## Benefits of This Architecture

1. **Less code to maintain**: no hand-written wrapper; the official tool owns
   the API surface.
2. **Performance**: direct integration = lower latency; warm reconnect avoids
   repeated cold starts within a conversation.
3. **Cost**: no Lambda overhead.
4. **Simplicity**: follows AWS documented patterns.

## Usage

The agent automatically uses Code Interpreter when users request code execution:

**Example prompts**:

- "Calculate the factorial of 20"
- "Create a list of the first 50 Fibonacci numbers"
- "Generate 100 random numbers and calculate statistics"

The tool is registered as `execute_python_securely` to emphasize security vs built-in Python execution.

## Session Management

- **Automatic**: Code Interpreter creates sessions on first use
- **Persistence**: Sessions maintain state across multiple calls (`clearContext=False`)
- **Cleanup**: AgentCore automatically cleans up inactive sessions after timeout
- **Manual cleanup**: Optional via `cleanup()` method for immediate resource release

## Testing

**Local Docker Build**:

```bash
docker build -f agent/strands-single-agent/Dockerfile -t test-agent .
docker run --rm test-agent python -c "from strands_tools.code_interpreter import AgentCoreCodeInterpreter; print('✓ Import successful')"
```

**Deployment**:

```bash
cd infra-cdk
cdk deploy
```

**Frontend Testing**: Use prompts that require code execution to verify functionality.

## Future Enhancements

Potential improvements:

- Add `write_files` tool for file operations
- Add `list_files` tool to see sandbox contents
- Support JavaScript/TypeScript execution
- Add file upload from S3
- Implement custom timeout configuration

## References

- [AgentCore Code Interpreter Documentation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/code-interpreter-tool.html)
- [AWS IDP Reference Implementation](https://github.com/aws-solutions-library-samples/accelerated-intelligent-document-processing-on-aws)
- [FAST Gateway Documentation](./GATEWAY.md)
