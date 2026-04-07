# Terraform Deployment

This sample includes a Terraform equivalent of the CDK infrastructure in `infra-terraform/`. Both deploy identical resources — choose the tool you're more comfortable with.

> **Recommendation:** Pick one and delete the other directory (`infra-cdk/` or `infra-terraform/`) from your fork to avoid confusion.

## Prerequisites

- Terraform >= 1.5.0 — see [Install Terraform](https://developer.hashicorp.com/terraform/install)
- AWS CLI configured (`aws configure`)
- Docker running (for `backend_deployment_type = "docker"`)
- Python 3.8+ (for the frontend deploy script)

## Configure

```bash
cd infra-terraform
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars`:

```hcl
stack_name_base  = "my-generative-ui"   # max 35 chars
admin_user_email = "you@example.com"     # optional, auto-creates Cognito user
backend_pattern  = "langgraph-single-agent"  # or: strands-single-agent
```

## Deploy

```bash
terraform init
terraform apply
python scripts/deploy-frontend.py
```

The Amplify URL is printed at the end of `deploy-frontend.py`.

## Update

**Agent or infrastructure changes:**
```bash
terraform apply
python scripts/deploy-frontend.py  # picks up new runtime ARN
```

**Frontend only:**
```bash
python scripts/deploy-frontend.py
```

## Tear down

```bash
terraform destroy
```

This removes all resources including Cognito, ECR, AgentCore, and Amplify.

## Deployment types

Set `backend_deployment_type` in `terraform.tfvars`:

- `docker` (default) — builds ARM64 container, pushes to ECR. Requires Docker.
- `zip` — packages agent as ZIP, no Docker required.

## Key difference from CDK

The CDK deploy scripts (`deploy-langgraph.sh`, `deploy-strands.sh`) run CDK + frontend in one command. With Terraform, these are two separate steps: `terraform apply` then `python scripts/deploy-frontend.py`.