# Dual Monitoring — Infrastructure

AWS CDK infrastructure for the Dual Monitoring system. Deploys the multi-agent swarm, evaluation dashboard, DevOps Agent integration, and supporting services.

## Stacks

The infrastructure is organized as nested stacks under a single parent:

| Stack | File | Purpose |
|-------|------|---------|
| Main | `lib/fast-main-stack.ts` | Parent stack, orchestrates all nested stacks |
| Cognito | `lib/cognito-stack.ts` | User Pool, OAuth client, managed login |
| Backend | `lib/backend-stack.ts` | AgentCore Runtime, Memory, Gateway, API Gateway, Feedback, DevOps Agent proxy |
| Evaluation | `lib/evaluation-stack.ts` | Evaluation Lambda, analysis jobs DynamoDB table |
| Amplify | `lib/amplify-hosting-stack.ts` | Frontend hosting on Amplify |

## API Routes

All routes are served through a single API Gateway with Cognito authorization.

### Feedback API
| Method | Path | Description |
|--------|------|-------------|
| POST | `/feedback` | Submit user feedback on agent responses |

### Evaluation API
| Method | Path | Description |
|--------|------|-------------|
| GET | `/evaluations/sessions` | List sessions with filtering |
| GET | `/evaluations/sessions/{sessionId}` | Get session detail with traces |
| POST | `/evaluations/analyze` | Trigger AI pattern analysis on low-scoring sessions |
| GET | `/evaluations/analyze/{jobId}` | Get analysis job status/results |
| POST | `/evaluations/improve-prompt` | Generate prompt improvements from analysis |
| GET | `/evaluations/improve-prompt/status/{jobId}` | Get prompt improvement job status |
| POST | `/evaluations/setup` | Setup AgentCore online evaluation config |
| GET | `/evaluations/metrics` | Get AgentCore evaluation metrics |
| GET | `/evaluations/configs` | List online evaluation configurations |
| GET | `/evaluations/configs/{configId}` | Get specific config |
| PUT | `/evaluations/configs/{configId}` | Update config |
| DELETE | `/evaluations/configs/{configId}` | Delete config |
| GET | `/evaluations/evaluators` | List all available evaluators |
| POST | `/evaluations/evaluators/custom` | Create custom evaluator |
| POST | `/evaluations/evaluate` | On-demand evaluation of a session |
| POST | `/evaluations/evaluate-batch` | Batch evaluation across sessions |

### DevOps Agent API
| Method | Path | Description |
|--------|------|-------------|
| POST | `/devops-agent/incident` | Submit incident to DevOps Agent via signed webhook |
| GET | `/devops-agent/investigations` | Fetch investigation results |

## Lambda Functions

| Function | Directory | Purpose |
|----------|-----------|---------|
| Feedback | `lambdas/feedback/` | Stores user feedback in DynamoDB |
| Evaluation | `lambdas/evaluation/` | Sessions, metrics, on-demand eval, AI analysis, prompt improvement |
| DevOps Agent | `lambdas/devops-agent/` | Proxy that HMAC-signs and forwards incidents to DevOps Agent webhook |
| Zip Packager | `lambdas/zip-packager/` | Packages agent code for ZIP deployment (only used with `deployment_type: zip`) |

## Resources Created

- Cognito User Pool + OAuth client
- AgentCore Runtime (Docker or ZIP)
- AgentCore Memory
- API Gateway (REST, with WAF, throttling, access logging)
- DynamoDB tables (feedback, analysis jobs)
- S3 buckets (agent code, Amplify staging)
- Amplify Hosting app
- CloudWatch Log Groups (runtime, application, usage, OTel)
- IAM roles (AgentCore execution, Gateway, Lambda)

## Configuration

Edit `config.yaml`:

```yaml
stack_name_base: prod-agent-monitoring-stack  # max 35 chars
admin_user_email: null                         # optional
backend:
  pattern: strands-swarm-agent
  deployment_type: docker                      # docker or zip
```

## Quick Start

```bash
npm install
npx cdk bootstrap    # first time only
npx cdk deploy
```

## Useful Commands

| Command | Description |
|---------|-------------|
| `npm run build` | Compile TypeScript |
| `npm run watch` | Watch mode |
| `npm test` | Run unit tests |
| `npx cdk deploy` | Deploy to AWS |
| `npx cdk diff` | Compare with deployed state |
| `npx cdk synth` | Emit CloudFormation template |
| `npx cdk destroy` | Remove all resources |

## Project Structure

```
infra-cdk/
├── bin/
│   └── fast-cdk.ts              # CDK app entry point
├── lib/
│   ├── fast-main-stack.ts       # Parent stack
│   ├── backend-stack.ts         # Runtime, Memory, Gateway, APIs, DevOps Agent
│   ├── evaluation-stack.ts      # Evaluation Lambda + DynamoDB
│   ├── cognito-stack.ts         # Authentication
│   ├── amplify-hosting-stack.ts # Frontend hosting
│   └── utils/
│       ├── agentcore-role.ts    # AgentCore IAM role
│       └── config-manager.ts    # Config loader
├── lambdas/
│   ├── feedback/                # Feedback API handler
│   ├── evaluation/              # Evaluation API handler
│   ├── devops-agent/            # DevOps Agent webhook proxy
│   └── zip-packager/            # ZIP deployment packager
├── config.yaml                  # Deployment configuration
├── cdk.json                     # CDK app config
└── tsconfig.json
```

## Related Docs

- [Deployment Guide](../docs/DEPLOYMENT.md)
- [Agent Configuration](../docs/AGENT_CONFIGURATION.md)
- [DevOps Agent Setup](../docs/DEVOPS_AGENT_SETUP.md)
- [Gateway Integration](../docs/GATEWAY.md)
