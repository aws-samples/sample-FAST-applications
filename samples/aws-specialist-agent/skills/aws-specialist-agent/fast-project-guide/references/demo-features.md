# List of Demo Features

Explanations organized by the axes of the features shown in the demo. Example questions to try are included for each.

## 1. Available tools (AWS expertise, web search, etc.)

The tools the Agent can use are split into MCP tools accessed via the AgentCore Gateway and tools built in locally to the Runtime. Tools accessed via the Gateway are authorized per department by Cedar ABAC (section 3).

Note: The table below is "the full catalog of tools this demo can potentially offer," and does not match the tools available to the individual user in the current conversation. The Gateway tools (the two tables below) are authorized per department by Cedar, and a guest user sees none of them at the tools/list step (deny-by-default, section 3). Local tools (the lower tables) are outside Cedar's scope and are available to all users. **When asked "what tools can you use right now?", do not read out this catalog; answer only with the tools that can actually be invoked** (presenting this catalog verbatim to a guest would mislead them into thinking they can use tools they cannot).

### MCP Servers (via Gateway / subject to Cedar authorization)

| Server             | Overview                                                       |
| ------------------ | -------------------------------------------------------------- |
| aws-mcp            | General AWS operations (CLI, documentation, region info, etc.) |
| sample-tool-target | Sample text-analysis tool                                      |
| ltm-mcp            | Long-term memory management (a user's past-session info)       |
| strands-mcp        | Strands Agents SDK documentation search/retrieval              |
| web-search-tool    | Real-time web search                                           |

Tools exposed by each server:

| Tool                      | Server             | Overview                                                             |
| ------------------------- | ------------------ | -------------------------------------------------------------------- |
| call_aws                  | aws-mcp            | Execute AWS CLI commands (mutating ones confirm with the user first) |
| run_script                | aws-mcp            | Execute Python (boto3) scripts                                       |
| search_documentation      | aws-mcp            | Search AWS documentation                                             |
| read_documentation        | aws-mcp            | Retrieve and read an AWS documentation page                          |
| get_regional_availability | aws-mcp            | Check per-region service availability                                |
| list_regions              | aws-mcp            | Retrieve the list of AWS regions                                     |
| retrieve_skill            | aws-mcp            | Retrieve a skill via the Gateway                                     |
| text_analysis_tool        | sample-tool-target | Word-count and frequent-character analysis of text                   |
| list_long_term_memories   | ltm-mcp            | Retrieve the list of long-term memories (past-session info)          |
| search_docs               | strands-mcp        | Search the Strands Agents documentation                              |
| fetch_doc                 | strands-mcp        | Retrieve a Strands Agents documentation page                         |
| WebSearch                 | web-search-tool    | Real-time web search                                                 |

### Local tools (outside MCP, built into the Runtime / outside Cedar's scope, available to all users)

| Tool             | Overview                                                 |
| ---------------- | -------------------------------------------------------- |
| skills           | Loading and activating local skills (/mnt/skills)        |
| file_read        | Reading and searching files                              |
| code_interpreter | Code execution in a sandboxed environment (Python/JS/TS) |

Notes:

- **AWS Skills**: 43 skills in /mnt/skills, such as aws-cdk, aws-iam, and amazon-bedrock. The Agent activates the skill that matches the question with the `skills` tool and follows the steps in SKILL.md. Detailed materials under references/ are read with `file_read`
- **Web search** (web-search-tool / WebSearch): An Amazon-managed web search tool that reached GA in 2026. It retrieves information newer than the training-data cutoff (latest releases, recent events, today's facts) and corroborates answers. Fully managed with no need for your own Lambda / Runtime or keys for external search APIs (Tavily / Brave); it uses Amazon-operated web indexes plus a knowledge graph, and queries stay within AWS (zero data egress). Available only in us-east-1, with no additional charge beyond Gateway data transfer. Because displaying citations (title / URL) is required by the Acceptable Use policy for answers that use search results, the SYSTEM_PROMPT instructs the Agent to cite sources
- Example questions: "Is Lambda available in us-east-2?", "Teach me how to create a VPC with CDK, following the skill", "Search for the latest information and tell me, with citations, about recently announced Bedrock AgentCore features"

## 2. Memory (AgentCore Memory)

- **Short-term memory**: Conversation history within a session. Managed by the AgentCoreMemorySessionManager
- **Long-term memory (LTM)**: The SemanticMemoryStrategy asynchronously extracts facts from the conversation and stores them under /facts/{actorId}. From the next session onward, relevant facts are injected automatically (per Cognito user). Note: extraction is asynchronous (about 1.5 minutes), so it does not take effect in the immediately following turn
- **Meta-recall**: For "list everything" questions such as "what do you remember about our past conversations?", semantic search comes up empty, so the dedicated list_long_term_memories tool (ltm-mcp) answers them
- Example question: (after telling it your preferences in a previous session) "Do you remember my preferences?"

## 3. Access control (Cedar ABAC)

- Cognito groups (finance / engineering) are converted into a department claim by a Pre-Token Lambda, and the Cedar policies of the AgentCore Policy attached to the Gateway evaluate them per tool
- Users not in any group are treated as guest, and all Gateway tools are denied (deny-by-default)
- Policy source: repo/gateway/policies/\*.cedar
- Example question: as a guest user, "Call sample_tool" → the Agent explains the 403 as insufficient permissions
- **Bulk creation of demo users**: repo/scripts/create-demo-users.py, an operational script that bulk-creates finance / engineer / guest role-specific users in Cognito, one per showcase PC. It has create / verify / cleanup subcommands, externalizes secrets (shared password, email) to scripts/.env (not tracked in git), and resolves the User Pool ID dynamically from the CloudFormation outputs. The role name engineer maps to the group engineering, and guest is created without a group to demo the denial experience

## 4. Model selection

- Switch per turn with the model selector in the UI. The registry (repo/infra-cdk/lib/utils/model-registry.ts) is the single source of truth
- Claude: Fable 5 / Opus 4.8 / Sonnet 5 / Sonnet 4.6 / Haiku 4.5 (Bedrock Global Cross-Region inference profiles)
- OpenAI GPT family: provided via Bedrock (a us-east-1 direct endpoint)
- Constraints of reasoning models (such as no temperature support) are absorbed by the registry's capability flags

## 5. Code execution (Code Interpreter)

- Executes Python in the AWS-managed isolated sandbox of AgentCore Code Interpreter (Python/JavaScript/TypeScript supported). The sandbox is fully isolated from other workloads and AWS infrastructure, but it is not inside the Runtime's private VPC; it is an AWS-managed environment (the API calls up to execution reach it privately via the bedrock-agentcore VPC endpoint)
- Uses the official strands_tools.code_interpreter.AgentCoreCodeInterpreter (migrated from the older in-house wrapper). Because session_name is tied to the conversation's session_id, the second and later invocations within the same conversation reconnect to the sandbox and preserve state (warm reconnect)
- Example question: "Compute the Fibonacci sequence in Python"

## 6. UI / UX

- **Conversation history sidebar**: ChatGPT-style. The list comes from localStorage, and the body is restored via a Lambda + a DynamoDB index. History is isolated when the Cognito user is switched. The auxiliary Lambdas (Feedback / History / Sessions) are placed in the same private VPC as the Runtime and reach DynamoDB / Bedrock (Haiku title generation) / AgentCore Memory via VPC endpoints
- **Streaming**: SSE. Progress display of tool calls, sticky auto-scroll, and Cmd+Enter to send (IME-safe)
- **Mermaid diagram rendering**: Renders ```mermaid fences in the response as SVG diagrams. Incomplete source during streaming is validated with mermaid.parse and held back from rendering; because mermaid itself is heavy it is lazy-loaded via dynamic import; and securityLevel: strict mitigates XSS. Full-screen enlargement is also possible
- **pre-warm**: When the session ID is fixed, a warmup payload is sent to pre-provision the microVM, the VPC ENI, and the skills mount

## 7. Self-documentation (this skill)

- Through the fast-project-guide skill, the Agent can explain this demo's own structure, configuration, and design decisions by reading primary sources (docs / a source-code mirror)
- Example questions: "What kind of architecture does this demo run on?", "Why are Skills distributed via S3 Files?"
