# AgentCore Gateway Implementation

This document describes how FAST implements AgentCore Gateway with Lambda targets to provide a scalable, production-ready tool execution architecture.

## Overview

FAST uses **AgentCore Gateway with Lambda Targets** to enable agents to access external tools and services. This architecture provides a clean separation between agent logic and tool implementation, allowing for independent scaling and deployment of individual tools.

### What the agent can reach

The deployed agent has three sources of capability:

1. **Lambda target (`sample-tool-target`)** — the demo `text_analysis_tool`, described in detail below.
2. **AWS MCP Server target (`aws-mcp`)** — the managed Agent Toolkit for AWS MCP Server at `https://aws-mcp.<region>.api.aws/mcp`, registered as an MCP gateway target. It exposes `aws___*` tools (AWS knowledge plus `aws___call_aws` / `aws___run_script`). The Gateway signs requests with SigV4 scoped to the service name `aws-mcp`.
3. **Skills via S3 Files** — vendored AWS skills are synced to S3, mounted into the Runtime at `/mnt/skills` through S3 Files, and surfaced to the agent by the Strands `AgentSkills` plugin (not a Gateway target). See `infra-cdk/lib/skills-storage-stack.ts`.

**Per-user tool access:** the Gateway's Cedar policy gates _who_ can call each tool by the user's `department` claim (resolved from `cognito:groups`; see [Identity Propagation](IDENTITY_POLICY.md)). Read tools are allowed for finance + engineering; the destructive `aws___call_aws` / `aws___run_script` tools are restricted to finance; guest is denied. Denied tools are hidden at `tools/list`.

## Architecture Comparison

### Standalone MCP Gateway vs Lambda Targets

There are two primary approaches to implementing AgentCore Gateway:

#### Standalone MCP Gateway

- Gateway directly implements MCP (Model Context Protocol) server
- Tools are built into the gateway infrastructure
- Simpler setup for basic scenarios
- Direct client → gateway communication

#### Lambda Targets (FAST's Choice)

- Gateway acts as a proxy/router to external Lambda functions
- Each tool is implemented as a separate Lambda function
- Client → Gateway → Lambda → Gateway → Client flow
- Production-ready architecture with enterprise benefits

### Why FAST Uses Lambda Targets

We chose Lambda targets for the following production advantages:

1. **Separation of Concerns**: Business logic lives in Lambda functions, not gateway infrastructure
2. **Independent Scaling**: Each tool can scale independently based on usage patterns
3. **Maintainability**: Update tool logic without touching gateway infrastructure
4. **Reusability**: Same Lambda can be used by multiple gateways or other services
5. **Language Flexibility**: Each Lambda can use different programming languages
6. **Independent Deployment**: Deploy tool updates without gateway downtime
7. **Cost Optimization**: Pay only for actual tool execution time
8. **Security**: Each Lambda can have specific IAM permissions for its requirements

## Implementation Details

### Gateway Configuration

The gateway is created using AWS CDK L1 constructs with the following configuration:

- **Protocol Type**: MCP (Model Context Protocol)
- **Authorization**: Custom JWT with Cognito integration
- **Authentication**: Machine-to-machine client credentials flow
- **Target Type**: AWS Lambda functions
- **Optional Features**: Semantic search (can be enabled for tool discovery)

### Lambda Target Structure

Each Lambda target in FAST follows this pattern:

```python
def handler(event, context):
    # Get tool name from context (strip target prefix)
    delimiter = "___"
    original_tool_name = context.client_context.custom['bedrockAgentCoreToolName']
    tool_name = original_tool_name[original_tool_name.index(delimiter) + len(delimiter):]

    # Event contains tool arguments directly
    arguments = event

    # Return response in expected format
    return {
        'content': [
            {
                'type': 'text',
                'text': 'Tool response here'
            }
        ]
    }
```

#### Tool Invocation Protocol Details

**Critical Implementation Notes:**

The tool name is **NOT** passed in the event body. Gateway passes it via the Lambda context object:

```python
# Tool name location
original_tool_name = context.client_context.custom['bedrockAgentCoreToolName']

# Arguments are in event body
name = event.get('name', 'World')
```

**Tool Name Format:**

Gateway includes the target name as a prefix with three underscores as delimiter:

```
{target_name}___{tool_name}
```

Example: `sample_tool_target___sample_tool`

**Complete Working Implementation:**

```python
import json
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

def lambda_handler(event, context):
    try:
        # Get tool name from context
        original_tool_name = context.client_context.custom['bedrockAgentCoreToolName']
        logger.info(f"Received tool invocation: {original_tool_name}")
        logger.info(f"Event: {json.dumps(event)}")

        # Strip target prefix
        delimiter = "___"
        if delimiter in original_tool_name:
            tool_name = original_tool_name[original_tool_name.index(delimiter) + len(delimiter):]
        else:
            tool_name = original_tool_name

        # Route to appropriate tool handler
        if tool_name == "sample_tool":
            name = event.get('name', 'World')
            result = f"Hello, {name}! This is a sample tool from FAST."
            return {"result": result}
        else:
            raise ValueError(f"Unknown tool: {tool_name}")

    except Exception as e:
        logger.error(f"Error in lambda_handler: {str(e)}", exc_info=True)
        raise
```

**Multiple Tools Per Lambda:**

A single Lambda can handle multiple tools by routing on the extracted tool name:

```python
if tool_name == "tool_one":
    # Handle tool one
    pass
elif tool_name == "tool_two":
    # Handle tool two
    pass
```

This is a valid production pattern used in AWS samples.

### Tool Schema Definition

Tools are defined using JSON schema in the CDK stack:

```json
{
  "name": "sample_tool",
  "description": "A sample tool that returns a greeting",
  "inputSchema": {
    "type": "object",
    "properties": {
      "name": {
        "type": "string",
        "description": "Name to greet"
      }
    },
    "required": ["name"]
  }
}
```

**Supported JSON Schema Types:**

When defining tool specs for Gateway, use these types:

- `"integer"` - for integers (not "int")
- `"number"` - for floats
- `"string"` - for strings
- `"boolean"` - for booleans
- `"array"` - for arrays
- `"object"` - for objects

### Authentication Flow

1. **Machine Client**: CDK creates a Cognito machine client with client credentials flow
2. **Resource Server**: Defines scopes for gateway access (read/write)
3. **JWT Authorization**: Gateway validates tokens using Cognito's OIDC discovery
4. **SSM Parameters**: Client credentials stored securely in SSM Parameter Store

## Key Components

### 1. Gateway L1 Construct

The gateway is created using native CloudFormation L1 constructs in `infra-cdk/lib/backend-stack.ts`:

- `CfnGateway`: Creates AgentCore Gateway with MCP protocol
- `CfnGatewayTarget`: Configures Lambda targets with tool schemas
- JWT authorization configured via Cognito
- Automatic lifecycle management by CloudFormation

### 2. Sample Tool Lambda

Located in `gateway/tools/sample_tool/sample_tool_lambda.py`:

- Demonstrates proper Lambda target implementation
- Shows how to parse AgentCore Gateway event format
- Includes error handling and logging

### 3. IAM Roles and Permissions

**Gateway Role**: Allows gateway to invoke Lambda functions and access required AWS services

**Custom Resource Role**: Manages gateway lifecycle operations

### 4. SSM Parameter Storage

Gateway configuration is stored in SSM for easy access:

- `/stack-name/gateway_url`: Gateway endpoint URL
- `/stack-name/machine_client_id`: Cognito client ID
- `/stack-name/machine_client_secret`: Cognito client secret
- `/stack-name/cognito_provider`: Cognito domain URL

## Testing the Gateway

### Direct Gateway Testing

Use the provided test script to verify gateway functionality:

```bash
python3 scripts/test-gateway.py
```

This script:

1. Authenticates using machine client credentials from SSM
2. Lists available tools via MCP protocol
3. Calls the sample tool with test parameters
4. Displays responses for verification

### Integration with AgentCore Runtime

The gateway integrates with AgentCore Runtime through:

1. **Runtime Configuration**: Runtime is configured with gateway URL via SSM
2. **Authentication**: Runtime uses same Cognito user pool for JWT tokens
3. **Tool Discovery**: Runtime discovers tools via gateway's `tools/list` endpoint
4. **Tool Execution**: Runtime calls tools via gateway's `tools/call` endpoint

### Integration with Agents via MCP

Agents connect to the Gateway using the Model Context Protocol (MCP). Two common integration approaches are shown below:

#### LangGraph with MultiServerMCPClient

The `MultiServerMCPClient` from `langchain-mcp-adapters` provides automatic session management:

```python
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.prebuilt import create_react_agent

# Create MCP client with Gateway configuration
mcp_client = MultiServerMCPClient({
    "gateway": {
        "transport": "streamable_http",
        "url": gateway_url,
        "headers": {
            "Authorization": f"Bearer {access_token}"
        }
    }
})

# Load tools from Gateway
tools = await mcp_client.get_tools()

# Create agent with tools
graph = create_react_agent(
    model=bedrock_model,
    tools=tools,
    checkpointer=checkpointer
)
```

#### Strands with Direct MCP Session

Strands agents can use direct MCP session management for more control:

```python
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from langchain_mcp_adapters.tools import load_mcp_tools

async with streamablehttp_client(
    gateway_url,
    headers={"Authorization": f"Bearer {access_token}"}
) as (read, write, _):
    async with ClientSession(read, write) as session:
        await session.initialize()
        tools = await load_mcp_tools(session)
        # Use tools with agent
```

**Example:** See `agent/strands-single-agent/basic_agent.py` for complete implementation.

## Adding New Tools

To add a new tool to the gateway:

1. **Create Lambda Function**: Implement tool logic following the Lambda target pattern
2. **Define Tool Schema**: Add JSON schema definition to CDK stack
3. **Update Gateway Configuration**: Add new target to gateway custom resource
4. **Deploy**: Run CDK deploy to update infrastructure

### Example: Adding a Weather Tool

```typescript
// In backend-stack.ts
const weatherLambda = new lambda.Function(this, "WeatherToolLambda", {
  runtime: lambda.Runtime.PYTHON_3_13,
  handler: "weather_tool.handler",
  code: lambda.Code.fromAsset(path.join(__dirname, "../../gateway/tools/sample_tool")),
})

const weatherToolSchema = {
  name: "get_weather",
  description: "Get current weather for a location",
  inputSchema: {
    type: "object",
    properties: {
      location: {
        type: "string",
        description: "City and state, e.g. 'Seattle, WA'",
      },
    },
    required: ["location"],
  },
}
```

## Security Considerations

### Authentication

- Machine-to-machine authentication using Cognito client credentials
- JWT tokens with configurable expiration
- Scoped access using Cognito resource server

### Authorization

- Gateway validates JWT tokens on every request
- Lambda functions inherit gateway's IAM role permissions
- Principle of least privilege for all components

### Network Security

- Gateway endpoints use HTTPS only
- Lambda functions run in AWS managed VPC
- No direct internet access required for Lambda functions

## Monitoring and Logging

### CloudWatch Logs

- Gateway operations logged to `/aws/bedrock-agentcore/gateway/*`
- Lambda function logs in `/aws/lambda/function-name`
- Custom resource operations in `/aws/lambda/gateway-custom-resource`

### Metrics

- Gateway invocation metrics via CloudWatch
- Lambda function duration and error metrics
- Custom metrics can be added to Lambda functions

## Troubleshooting

### Common Issues

**"Unknown tool: None" Error**

- Indicates Lambda function isn't parsing context correctly
- Verify Lambda follows AgentCore Gateway input format
- Check CloudWatch logs for detailed error information

**Authentication Failures**

- Verify Cognito client credentials in SSM
- Check JWT token expiration
- Ensure gateway authorization configuration is correct

**Tool Not Found**

- Verify tool schema matches Lambda implementation
- Check gateway target configuration
- Ensure Lambda function is deployed and accessible

**Gateway returns "An internal error occurred"**

- Enable debugging to see detailed error messages by updating the gateway to set `exceptionLevel: 'DEBUG'` in the CDK construct or via AWS CLI.

```bash
# Enable debugging on gateway
aws bedrock-agentcore-control update-gateway \
  --gateway-identifier <GATEWAY_ID> \
  --name <GATEWAY_NAME> \
  --role-arn <ROLE_ARN> \
  --protocol-type MCP \
  --authorizer-type CUSTOM_JWT \
  --authorizer-configuration <AUTH_CONFIG> \
  --exception-level DEBUG
```

Or update the gateway construct in CDK:

```typescript
const gateway = new bedrockagentcore.CfnGateway(this, "AgentCoreGateway", {
  name: `${config.stack_name_base}-gateway`,
  roleArn: gatewayRole.roleArn,
  protocolType: "MCP",
  exceptionLevel: "DEBUG", // Add this line for detailed error messages
  // ... rest of configuration
})
```

### Debug Steps

1. **Check SSM Parameters**: Verify all gateway configuration parameters exist
2. **Test Authentication**: Use test script to verify token generation
3. **Review CloudWatch Logs**: Check gateway and Lambda function logs
4. **Validate Tool Schema**: Ensure schema matches expected format
5. **Test Lambda Directly**: Invoke Lambda function independently to verify logic

## Best Practices

### Lambda Function Development

- Always log incoming events for debugging
- Implement proper error handling and return meaningful error messages
- Use environment variables for configuration
- Keep functions focused on single tool responsibility

### Schema Design

- Provide clear, descriptive tool and parameter descriptions
- Use appropriate JSON schema types and constraints
- Include examples in descriptions where helpful
- Keep input schemas simple and focused

### Deployment

- Test tools individually before gateway integration
- Use version tags for Lambda function deployments
- Monitor CloudWatch metrics after deployment
- Implement gradual rollout for production changes

## Related Documentation

- [Identity Propagation & Cedar Policy Guide](IDENTITY_POLICY.md) - User-level access control for Gateway tools
- [Cedar Policy Guide](CEDAR_POLICY_GUIDE.md) - Cedar policy syntax, capabilities, and reference
- [Replacing Cognito](REPLACING_COGNITO.md) - Identity provider swap and Gateway interceptors guide
- [Runtime-Gateway Authentication](RUNTIME_GATEWAY_AUTH.md) - M2M token flow between Runtime and Gateway
- [Deployment Guide](DEPLOYMENT.md) - How to deploy FAST infrastructure
- [AWS AgentCore Gateway Documentation](https://docs.aws.amazon.com/bedrock/latest/userguide/agentcore-gateway.html) - Official AWS documentation
- [AWS Gateway Lambda Target Documentation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-add-target-lambda.html) - Lambda target implementation details

## Design notes

Decisions specific to this derivative around Gateway targets:

- **MCP server targets, not just Lambda**: the AWS MCP server, a Strands docs MCP server, and a long-term-memory meta-recall MCP server are exposed as Gateway MCP server targets. Hosting them as targets (rather than embedding their tools in the agent) keeps tool surface, authentication, and Cedar policy unified across native and MCP tools.
- **AWS MCP target inherits a broader role**: the AWS MCP server's `call_aws` and `run_script` capabilities require the Gateway target role to enumerate and call AWS services. The role is scoped read-broad / write-narrow for the demo; production deployments should re-evaluate against their threat model.
- **SigV4 service name pinned**: when calling Gateway target endpoints from inside the runtime, the SigV4 signing service identifier matters and is pinned in code rather than inferred, to avoid silent auth failures on minor API changes.
- **Managed Web Search target**: the GA Web Search connector is wired in as a Gateway target so the agent can answer general questions with citations without bringing along a separate search SDK or browser tool.
