# Deployment Guide

This guide walks you through deploying the Fullstack AgentCore Solution Template (FAST) to AWS.

## Prerequisites

Before deploying, ensure you have:

- **Node.js 20+** installed (see [AWS guide for installing Node.js on EC2](https://docs.aws.amazon.com/sdk-for-javascript/v2/developer-guide/setting-up-node-on-ec2-instance.html))
- **AWS CLI** configured with credentials (`aws configure`) - see [AWS CLI Configuration guide](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-quickstart.html)
- **AWS CDK CLI** installed: `npm install -g aws-cdk` (see [CDK Getting Started guide](https://docs.aws.amazon.com/cdk/v2/guide/getting-started.html))
- **Python 3.11 or above+** (standard library only - no virtual environment needed for deployment)
- **Docker** - Required for all deployments. See [Install Docker Engine](https://docs.docker.com/engine/install/). Verify with `docker ps`. Alternatively, [Finch](https://github.com/runfinch/finch) can be used on Mac. See below if you have a non-ARM machine.
- An AWS account with sufficient permissions to create:
  - S3 buckets
  - CloudFront distributions
  - Cognito User Pools
  - Amplify Hosting projects
  - Bedrock AgentCore resources
  - IAM roles and policies

### Deploying to a different account or region

The committed `config.yaml` is validated for **us-east-1**. Within the same account and region you can deploy as-is — just give the stack a non-overlapping `vpc_cidr` and a real `admin_user_email`. Deploying into a **different account or region** additionally requires:

1. **Region must be us-east-1 (for now).** AgentCore Web Search is only available in us-east-1, and the deploy intentionally fails fast elsewhere. The OpenAI (GPT) models are also reached through the in-region `bedrock-mantle` endpoint. To deploy to another region you would need to remove the Web Search target and the OpenAI models first.
2. **Pin account-correct AZs.** AgentCore Runtime VPC mode only supports specific AZ **ids** (us-east-1: `use1-az1`, `use1-az2`, `use1-az4`), and the AZ name → id mapping is account-specific. The default `availability_zones: [us-east-1b, us-east-1d]` maps to supported ids in the validation account, but may not in yours. Re-derive the right names with `aws ec2 describe-availability-zones --query "AvailabilityZones[].[ZoneName,ZoneId]" --output table` and set `backend.availability_zones` to two names that map to supported ids; otherwise subnet creation fails.
3. **Enable Bedrock model access.** The selectable models (Claude Fable 5 / Opus / Sonnet / Haiku and OpenAI GPT) must be enabled in your account. Claude Fable 5 additionally needs the account's Bedrock data retention mode set to `provider_data_share` in the calling region (see "Enabling Claude Fable 5" below). Models you do not have access to should be set `available: false` in `infra-cdk/lib/utils/model-registry.ts`.

## Configuration

### 1. Update Configuration File

Edit `infra-cdk/config.yaml` to customize your deployment:

```yaml
stack_name_base: your-project-name # Change this to your preferred stack name (max 35 chars)

admin_user_email: null # Optional: admin@example.com (auto-creates user & emails credentials)

backend:
  pattern: strands-single-agent # Available patterns: strands-single-agent
  deployment_type: docker # Available deployment types: docker (default), zip
```

**Important**:

- Change `stack_name_base` to a unique name for your project to avoid conflicts
- Maximum length is 35 characters (due to AWS AgentCore runtime naming constraints)
- The committed `infra-cdk/config.yaml` ships `admin_user_email` as a placeholder (`your-email+fastprojectadmin@example.com`). **Replace it with your own real, reachable address before deploying** — the admin user's temporary password is emailed there, so a placeholder leaves you unable to sign in.
- Department/role authorization is driven by Cognito **group membership**, not the email address. Group membership is operational data that CDK does not manage, so after deploying, add the admin user to a group (e.g. `aws cognito-idp admin-add-user-to-group --user-pool-id <pool-id> --username <email> --group-name finance`) — otherwise every Gateway tool call is denied as "guest".

### Deployment Types

FAST supports two deployment types for AgentCore Runtime. Set `deployment_type` in `infra-cdk/config.yaml`:

| Type               | Description                             |
| ------------------ | --------------------------------------- |
| `docker` (default) | Builds container image, pushes to ECR   |
| `zip`              | Packages code via Lambda, uploads to S3 |

**Note**: Docker is required for both deployment types. The `zip` option only affects how the agent runtime is packaged. Other Lambda functions in the stack still use Docker for dependency bundling.

**Use Docker (default) when:**

- You need native C/C++ libraries without ARM64 wheels on PyPI
- Your deployment package exceeds 250 MB
- You need custom OS-level dependencies
- You want maximum compatibility

**Use ZIP when:**

- You want faster iteration during development
- Your dependencies are pure Python or have ARM64 wheels available
- You need higher session throughput

**ZIP packaging includes**: The `agent/<your-pattern>/`, `agent/utils/`, `gateway/`, and `tools/` directories are bundled together with dependencies from `requirements.txt`. This matches the `COPY` commands in the Docker deployment's Dockerfile.

### VPC Deployment (Private Network)

By default, the AgentCore Runtime runs in PUBLIC network mode with internet access. To deploy the runtime into an existing VPC for private network isolation, set `network_mode: VPC` in `infra-cdk/config.yaml` and provide your VPC details.

#### What runs inside vs outside the VPC

When VPC mode is enabled, the **AgentCore Runtime** (your agent code) runs inside your VPC's private subnets. All network calls the agent makes are subject to VPC networking rules and reach AWS services through VPC endpoints — the agent never makes direct internet calls.

The following components run **outside** the VPC in AWS-managed infrastructure:

- **Gateway tool Lambdas** — The agent calls the Gateway through the `bedrock-agentcore.gateway` VPC endpoint (private networking). The Gateway then invokes Lambda functions on AWS-managed infrastructure. The agent's network call stays private; only the Lambda execution happens outside the VPC.
- **Code Interpreter** — The agent calls the Code Interpreter API through the `bedrock-agentcore` VPC endpoint. The sandbox execution happens in Bedrock's managed environment.
- **Bedrock model invocations** — Model calls go through the `bedrock-runtime` VPC endpoint to Bedrock's managed infrastructure.
- **Frontend (Amplify/CloudFront)** — Entirely separate, public-facing, and not part of the VPC deployment.

In short: the agent's outbound network traffic stays on private AWS networking via VPC endpoints. The services it calls (Bedrock, Gateway, Code Interpreter) may execute on infrastructure outside the VPC, but the network path from the agent to those service APIs is private.

#### Configuration

```yaml
backend:
  pattern: strands-single-agent
  deployment_type: docker
  network_mode: VPC
  vpc:
    vpc_id: vpc-0abc1234def56789a
    subnet_ids:
      - subnet-aaaa1111bbbb2222c
      - subnet-cccc3333dddd4444e
    security_group_ids: # Optional - a default SG is created if omitted
      - sg-0abc1234def56789a
```

The `vpc_id` and `subnet_ids` fields are required. The `security_group_ids` field is optional — if omitted, the CDK construct will create a default security group for the runtime.

#### Required VPC Endpoints

When deploying in VPC mode, the runtime runs in private subnets without internet access. Your VPC must have the following VPC endpoints configured so the agent can reach the AWS services it depends on:

| Endpoint                                           | Service                          | Type      |
| -------------------------------------------------- | -------------------------------- | --------- |
| `com.amazonaws.{region}.bedrock-runtime`           | Bedrock model invocation         | Interface |
| `com.amazonaws.{region}.bedrock-agentcore`         | AgentCore Identity (Token Vault) | Interface |
| `com.amazonaws.{region}.bedrock-agentcore.gateway` | AgentCore Gateway (MCP tools)    | Interface |
| `com.amazonaws.{region}.ssm`                       | SSM Parameter Store              | Interface |
| `com.amazonaws.{region}.secretsmanager`            | Secrets Manager                  | Interface |
| `com.amazonaws.{region}.logs`                      | CloudWatch Logs                  | Interface |
| `com.amazonaws.{region}.ecr.api`                   | ECR API (Docker deployment)      | Interface |
| `com.amazonaws.{region}.ecr.dkr`                   | ECR Docker (Docker deployment)   | Interface |
| `com.amazonaws.{region}.s3`                        | S3 (ZIP deployment, ECR layers)  | Gateway   |
| `com.amazonaws.{region}.dynamodb`                  | DynamoDB (feedback table)        | Gateway   |
| `com.amazonaws.{region}.xray`                      | X-Ray (OTel trace export)        | Interface |
| `com.amazonaws.{region}.bedrock-mantle`            | OpenAI GPT-5.x (Responses API)   | Interface |

Replace `{region}` with your deployment region (e.g. `us-east-1`).

All interface endpoints must have private DNS enabled and must be associated with the same subnets and security groups that allow traffic from the AgentCore Runtime.

#### Subnet Requirements

- The CDK-managed VPC uses **fully isolated private subnets** (`PRIVATE_ISOLATED`): no `0.0.0.0/0` route at all
- Subnets are pinned to AgentCore-supported AZs (at least two) for high availability
- Subnets must have sufficient available IP addresses for the runtime ENIs

#### No NAT Gateway (fully closed network)

The default deployment has **no NAT Gateway** and no outbound internet access. This works because every dependency is reached through a VPC endpoint:

- The Gateway M2M token is obtained through AgentCore Identity (the Token Vault), which runs the Cognito token exchange server-side and is reachable via the `bedrock-agentcore` VPC endpoint — the Runtime never calls the public Cognito hosted domain.
- User identity is propagated into the M2M token via `aws_client_metadata` (no extra egress).
- S3 Files (skills) mount over NFS (port 2049) to the mount-target ENI **inside the VPC**. Per the AgentCore VPC docs, the mount only needs TCP 2049 connectivity between the runtime ENIs and the mount targets (allowed by the self-referencing security group) — no dedicated VPC endpoint and no NAT. (TLS and IAM auth are handled automatically.)

> **Note:** If you add custom tools that make outbound _public-internet_ calls (or use the Browser tool), you would need to reintroduce a NAT Gateway. AWS services can instead be reached by adding the corresponding VPC endpoint.

#### Security Group Configuration

The CDK stack auto-creates a security group for the AgentCore Runtime. This same security group is typically applied to your VPC endpoints. You must add a self-referencing inbound rule to allow the runtime to reach the endpoints:

- Protocol: TCP, Port: 443, Source: the security group itself

### OpenAI models (GPT-5.x via bedrock-mantle)

The OpenAI models in the picker (GPT-5.4 / GPT-5.5) are served from the `bedrock-mantle` endpoint (OpenAI Responses API), available in us-east-1 since 2026-06. The CDK-managed VPC always provisions the in-region `bedrock-mantle` interface endpoint (see the endpoint table above), so no extra stacks, flags, or cross-region networking are needed — a plain `cdk deploy` covers GPT models (the former `OPENAI_MANTLE` us-east-2 peering stacks are gone).

### Enabling Claude Fable 5 (data retention prerequisite)

Before Claude Fable 5 can be used, the account's Bedrock data retention mode must be set to `provider_data_share` via the Data Retention API. Fable 5 declares `allowed_modes: ["provider_data_share"]` — with any other effective mode (`default` / `none`) the model is reported as unavailable and invocations fail with `ValidationException: data retention mode 'default' is not available for this model` (the agent then looks silently unresponsive in the UI; the error only shows in the Runtime logs). Setting this mode explicitly acknowledges that prompts and outputs sent to Fable 5 are shared with Anthropic and retained for up to 30 days ([data retention docs](https://docs.aws.amazon.com/bedrock/latest/userguide/data-retention.html)).

Operational notes:

- **Set it in the region the runtime calls Bedrock from** (us-east-1 for this stack). The check is evaluated against the account setting of the source region — the bedrock-runtime endpoint that receives the request — not the regions the `global.` inference profile routes to, so configuring other regions is unnecessary.
- There is no console UI for this; use the API. It is a one-time account-level runtime setting outside CloudFormation/CDK, so re-creating the stack keeps it, but moving to a **new account or a different source region requires setting it again** — a forgotten setting resurfaces as the silent failure above.
- The caller needs `bedrock:PutAccountDataRetention` / `bedrock:GetAccountDataRetention` (account-level, `Resource: "*"`). The effective mode resolves as project -> account -> model default; an account-level setting is sufficient here.
- Models whose `allowed_modes` include `default` (e.g. Claude Opus 4.8) keep their data inside AWS even after this change — the account mode only sets what you allow, each model's `allowed_modes` decides what actually happens.

```bash
# Using a Bedrock API key (bearer token):
curl -X PUT https://bedrock.us-east-1.amazonaws.com/data-retention \
  -H "Authorization: Bearer $AWS_BEARER_TOKEN_BEDROCK" \
  -H "Content-Type: application/json" \
  -d '{ "mode": "provider_data_share" }'

# Or with SigV4 credentials via boto3 (the AWS CLI does not expose these
# operations yet as of v2.31):
python3 -c "
import boto3
c = boto3.client('bedrock', region_name='us-east-1')
print(c.put_account_data_retention(mode='provider_data_share'))"

# Verify:
python3 -c "
import boto3
print(boto3.client('bedrock', region_name='us-east-1').get_account_data_retention()['mode'])"
```

### Deploying Multiple Environments (same account/region)

A second environment (e.g. a dev stack alongside production) needs no code changes. Create a sibling config file and select it with the `CONFIG_FILE` env var:

```yaml
# infra-cdk/config.dev.yaml  (gitignored — embeds a real admin email)
stack_name_base: FAST-dev # MUST differ: CloudFormation exports, Cognito domain etc. derive from it
admin_user_email: you@example.com
backend:
  pattern: strands-single-agent
  deployment_type: docker
  network_mode: VPC
  vpc_management: CDK
  vpc_cidr: 10.30.0.0/16 # MUST not overlap other environments (prod uses 10.20.0.0/16)
  use_long_term_memory: true
  skills:
    enabled: true
    mount_path: /mnt/skills
```

```bash
cd infra-cdk
# Everything (VPC + skills + LTM + OpenAI models) in one shot:
CONFIG_FILE=config.dev.yaml npx cdk deploy --all --require-approval never
cd ..
python scripts/deploy-frontend.py FAST-dev # frontend reads outputs from the named stack
```

Rules for coexistence:

- `stack_name_base` must be unique per environment — every named resource and CloudFormation export derives from it.
- `vpc_cidr` must not overlap any other environment, so the networks stay unambiguous to operate (and remain peerable should that ever be needed).
- A plain `cdk deploy` (no `CONFIG_FILE`) always targets production's `config.yaml` — the env var is the only switch, so there is no file to forget to revert.
- When deploying into a **different AWS account**, also set `backend.availability_zones`: AgentCore Runtime VPC mode only supports specific AZ _ids_ per region (us-east-1: use1-az1/az2/az4), and the AZ name -> id mapping is account-specific. Derive the right names with `aws ec2 describe-availability-zones`.

## Deployment Steps

### TL;DR version

Here are the commands to deploy backend and frontend:

```bash
cd infra-cdk
npm install
cdk bootstrap # Once ever
cdk deploy
cd ..
python scripts/deploy-frontend.py
```

### Deploy Without Local Tooling (via CodeBuild)

If you don't have Node.js, Docker, or CDK installed locally, you can deploy entirely in the cloud using a temporary CodeBuild project. Requires only Python 3.8+ and AWS CLI:

```bash
python scripts/deploy-with-codebuild.py
```

See `scripts/README.md` for details and required IAM permissions.

### 1. Install Dependencies

Install infrastructure dependencies:

```bash
cd infra-cdk
npm install
```

**Note**: Frontend dependencies are automatically installed during deployment via Docker bundling, so no separate frontend `npm install` is required.

### 2. Bootstrap CDK (First Time Only)

If this is your first time using CDK in this AWS account/region:

```bash
cdk bootstrap
```

### 3. Deploy backend with CDK

Build and deploy the complete stack:

```bash
cdk deploy
```

The deployment will:

1. Create a Cognito User Pool for authentication
1. Build and push the agent container to ECR
1. Create the AgentCore runtime
1. Set up CloudFront distribution for the frontend

**Note**: The deployment takes approximately 5-10 minutes due to container building and AgentCore setup.

**You do not need to run the skill scripts by hand before deploying.** The vendored AWS skills under `skills/agent-toolkit-for-aws/` are committed to the repo, and `cdk` regenerates the `fast-project-guide` skill automatically at synth time (it runs `scripts/build-project-guide.py` through the skills-storage stack's bundling). `scripts/vendor-skills.py` is only needed to refresh the vendored skills from a newer upstream commit — a maintenance task, not a deployment step. `cdk deploy` is self-contained.

### 4. Deploy frontend

```bash
# From root directory
python scripts/deploy-frontend.py
```

This script automatically:

- Generates fresh `aws-exports.json` from CDK stack outputs (see below for more information about `aws-exports.json`)
- Installs/updates npm dependencies if needed
- Builds the frontend
- Deploys to AWS Amplify Hosting

You will see the URL for application in the script's output, which will look similar to this:

```
ℹ App URL: https://main.d123abc456def7.amplifyapp.com
```

### 5. Create a Cognito User (if necessary)

**If you provided `admin_user_email` in config:**

- Check your email for temporary password
- Sign in and change password on first login

**If you didn't provide email:**

1. Go to the [AWS Cognito Console](https://console.aws.amazon.com/cognito/)
2. Find your User Pool (named `{stack_name_base}-user-pool`)
3. Click on the User Pool
4. Go to "Users" tab
5. Click "Create user"
6. Fill in the user details:
   - **Email**: Your email address
   - **Temporary password**: Create a temporary password
   - **Mark email as verified**: Check this box
7. Click "Create user"

**For a demo: create role users in bulk with a script**

`scripts/create-demo-users.py` provisions one user per role (`finance`, `engineer`, `guest`) across several "sets" (one set per demo PC) with a shared password, and assigns each user to the right Cognito group. `engineer` is mapped to the `engineering` group; `guest` is left group-less on purpose so Cedar denies it every Gateway tool. The secrets (email prefix, domain, password) live in `scripts/.env` (git-ignored), and the User Pool / Client IDs are resolved from the target stack's CloudFormation outputs, so nothing sensitive or environment-specific is hard-coded.

```bash
cp scripts/.env.example scripts/.env   # then fill in real values
uv run scripts/create-demo-users.py create    # provision all users (idempotent)
uv run scripts/create-demo-users.py verify     # log in as each user, check groups
uv run scripts/create-demo-users.py cleanup     # delete demo users after the demo
```

`create` is idempotent: re-running it never creates duplicates (existing users are reported as `exists` and only their password/group are re-applied). `cleanup` never touches the `admin_user_email` account.

### 6. Access the Application

1. Open the Amplify Hosting URL in your browser
1. Sign in with the Cognito user you created
1. You'll be prompted to change your temporary password on first login

## Post-Deployment

### Updating the Application

To update the frontend code:

```bash
# From root directory
python scripts/deploy-frontend.py
```

To update the backend agent:

**Docker deployment:**

```bash
cd infra-cdk
cdk deploy --all
```

### Monitoring and Logs

- **Frontend logs**: Check CloudFront access logs
- **Backend logs**: Check CloudWatch logs for the AgentCore runtime
- **Build logs**: Check CodeBuild project logs for container builds

## Cleanup

To remove all resources:

```bash
cd infra-cdk
cdk destroy --force
```

**Warning**: This will delete all data including S3 buckets created during deployment and ECR images.

## Troubleshooting

### Common Issues

1. **`cdk deploy` fails with Docker errors**
   - Ensure Docker is installed and the daemon is running: `docker ps`
   - On Mac, open Docker Desktop or start Finch: `finch vm start`
   - On Linux: `sudo systemctl start docker`

2. **"Architecture incompatible" or "exec format error" during Docker build**
   - This occurs when deploying from a non-ARM machine without cross-platform build setup
   - Follow the "Docker Cross-Platform Build Setup" instructions in the Prerequisites section
   - Ensure you've installed QEMU emulation: `docker run --privileged --rm tonistiigi/binfmt --install all`
   - Verify ARM64 support: `docker buildx ls` should show `linux/arm64` in platforms

3. **"Agent Runtime ARN not configured"**
   - Ensure the backend stack deployed successfully
   - Check that SSM parameters were created correctly

4. **Authentication errors**
   - Verify you created a Cognito user
   - Check that the user's email is verified

5. **Build failures**
   - Check CodeBuild logs in the AWS Console
   - Ensure your agent code in `agent/` is valid

6. **Permission errors**
   - Verify your AWS credentials have sufficient permissions
   - Check IAM roles created by the stack

### Getting Help

- Check CloudWatch logs for detailed error messages
- Review the CDK deployment output for any warnings
- Ensure all prerequisites are met

## Security Considerations

- The Cognito User Pool is configured with strong password policies
- All communication uses HTTPS via CloudFront
- AgentCore runtime uses JWT authentication
- IAM roles follow least-privilege principles

For production deployments, consider:

- Enabling MFA on Cognito users
- Setting up custom domains with your own certificates
- Configuring additional monitoring and alerting
- Implementing backup strategies for any persistent data

## Docker Cross-Platform Build Setup (Required for non-ARM machines)

**Important**: BedrockAgentCore Runtime only supports ARM64 architecture. If you're deploying from a non-ARM machine (x86_64/amd64), you need to enable Docker's cross-platform building capabilities.

Check your machine architecture:

```bash
uname -m
```

If the output is `x86_64` (not `aarch64` or `arm64`), run these commands:

1. **Install QEMU for ARM64 emulation:**

   ```bash
   docker run --privileged --rm tonistiigi/binfmt --install all
   ```

2. **Enable Docker buildx and create a multi-platform builder:**

   ```bash
   docker buildx create --use --name multiarch --driver docker-container
   docker buildx inspect --bootstrap
   ```

3. **Verify ARM64 support is available:**
   ```bash
   docker buildx ls
   ```
   You should see `linux/arm64` in the platforms list.

**Note**: This setup is only required once per machine. The CDK deployment will automatically use these capabilities to build ARM64 containers.

## Understanding aws-exports.json

The `aws-exports.json` file is a critical configuration file that enables the React frontend to communicate with AWS Cognito for authentication. This file is automatically generated during frontend deployment and contains the necessary configuration parameters for Cognito authentication.

**What is aws-exports.json?**

The `aws-exports.json` file contains authentication configuration that the React application reads to properly configure Cognito Authentication. It's created automatically by the deployment script and placed in `frontend/public/aws-exports.json`.

**Why is it necessary?**

This configuration file is essential because:

- It provides the React application with the correct Cognito User Pool and Client IDs
- It specifies the authentication endpoints and redirect URIs
- It configures the authentication flow parameters
- Without this file, Cognito authentication will not work

**How is it created?**

The file is automatically generated by `deploy-frontend.py` which:

1. Extracts configuration from your deployed CDK stack outputs
2. Automatically detects the AWS region from the CloudFormation stack ARN
3. Retrieves the required values: `CognitoClientId`, `CognitoUserPoolId`, and `AmplifyUrl`
4. Generates the configuration file with the following structure:

```json
{
  "authority": "https://cognito-idp.region.amazonaws.com/user-pool-id",
  "client_id": "your-client-id",
  "redirect_uri": "https://your-amplify-url",
  "post_logout_redirect_uri": "https://your-amplify-url",
  "response_type": "code",
  "scope": "email openid profile",
  "automaticSilentRenew": true
}
```

**Important**: You should not manually edit this file as it's regenerated on each deployment. If authentication isn't working, redeploy the frontend to ensure you have the latest configuration.

## Design notes

Selected design decisions specific to this derivative (rationale beyond the upstream baseline):

- **Fully closed network (no NAT)**: outbound traffic to AWS APIs goes through interface VPC endpoints exclusively. A NAT gateway is intentionally not provisioned, eliminating the recurring NAT cost and limiting egress to the explicitly configured endpoint set.
- **AgentCore Gateway VPC endpoint is mandatory**: when the runtime calls Gateway from inside the VPC, the `bedrock-agentcore` interface endpoint must be present, or tool invocations fail at network level.
- **Endpoint set is curated**: each interface endpoint is justified by an actual code path that calls it from the VPC. Unused endpoints (e.g. `bedrock-agent-runtime` once direct Bedrock Agent calls were removed) are pruned to reduce cost and attack surface.
- **VPC CIDR and AZ are parameterized**: `config.yaml` exposes `vpc_cidr` and the AZ list so multiple environments can coexist in one account without overlap, and so AZ selection can avoid AZs that lack required service support.
- **Cold-start mitigation**: the AgentCore Runtime is configured with a long-running container lifecycle and a frontend pre-warm ping. This keeps first-token latency low without a separately scheduled warmer Lambda.
