## Add LLM Council sample application

### Summary
- Adds a new FAST sample implementing [Andrej Karpathy's Council of LLMs](https://x.com/karpathy/status/1821458514940092673) pattern on AWS, built with Amazon Bedrock AgentCore
- Multiple diverse LLMs collaborate through a structured 3-stage deliberation process (independent responses → anonymized peer ranking → chairman synthesis) to produce higher-quality responses than any single model alone
- Includes full-stack implementation: React frontend with tabbed UI, CDK infrastructure, and a containerized council agent backend

### Council Models
| Role | Model | Provider |
|------|-------|----------|
| Council Member | `meta.llama4-maverick-17b-instruct-v1:0` | Meta |
| Council Member | `google.gemma-3-27b-it` | Google |
| Council Member | `openai.gpt-oss-120b-1:0` | OpenAI |
| Council Member | `deepseek.v3.2` | DeepSeek |
| Chairman | `us.anthropic.claude-sonnet-4-5-20250929-v1:0` | Anthropic |

### Key Features
- **3-stage deliberation**: Independent response collection → anonymized peer ranking → chairman synthesis
- **Multi-provider diversity**: Council members span 4 different model providers for varied perspectives
- **Real-time streaming**: SSE-based event streaming shows each stage as it completes
- **Parallel invocation**: All council members are queried concurrently for low latency
- **Feedback API**: Lambda + DynamoDB backend for collecting user feedback
- **Docker & ZIP deployment**: Supports both deployment modes via `config.yaml`

### Architecture
- **Frontend**: React + TypeScript, Amplify Hosting, Cognito authentication
- **Backend**: Bedrock AgentCore Runtime, containerized council agent
- **AI**: Amazon Bedrock Converse API with 4 council models + 1 chairman model
- **Infrastructure**: CDK-managed (Cognito, AgentCore, DynamoDB, S3, Amplify, CloudFront)

### Test Plan
- [ ] Run `cdk deploy --all` and verify all stacks deploy successfully
- [ ] Deploy frontend via `python scripts/deploy-frontend.py`
- [ ] Submit a query and verify all 3 stages complete (responses, rankings, synthesis)
- [ ] Confirm all 4 council models respond in Stage 1
- [ ] Verify peer rankings display correctly in Stage 2
- [ ] Verify chairman synthesis in Stage 3
- [ ] Test feedback submission
- [ ] Run `ruff check` and `ruff format --check` for Python linting
- [ ] Run `npx prettier --check "src/**/*.{ts,tsx,js,jsx,css,json}"` for frontend formatting
