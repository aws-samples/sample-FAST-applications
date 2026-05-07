# Evaluation Lambda Dependencies

## Overview

This document describes the dependencies required for the Evaluation Lambda function and how they are configured for deployment.

## Dependency Configuration

### Lambda Runtime Dependencies

The following dependencies are **automatically provided** by the AWS Lambda runtime and do NOT need to be included in `requirements.txt`:

- **boto3** (>= 1.34.0) - AWS SDK for Python
  - Included in all Python Lambda runtimes
  - Used for CloudWatch Logs API calls

### Lambda Layer Dependencies

The following dependencies are installed via **AWS Lambda Layers** and do NOT need to be included in `requirements.txt`:

- **aws-lambda-powertools** (>= 2.31.0) - Lambda utilities for logging, tracing, and API handling
  - Installed via CDK layer: `AWSLambdaPowertoolsPythonV3-python313-arm64:18`
  - Provides: Logger, Tracer, APIGatewayRestResolver

### Application Dependencies

The following dependencies are **included in requirements.txt** and will be packaged with the Lambda:

- **strands-agents** (>= 0.1.0)
  - Purpose: AI analysis engine for pattern identification and prompt improvement
  - Used by: `ai_engine.py`
  - Features: Bedrock integration, agent orchestration

- **hypothesis** (>= 6.92.0)
  - Purpose: Property-based testing framework
  - Used by: Test files (`test_*.py`)
  - Features: Automated test case generation, property validation

## Local Development

For local development and testing, install all dependencies including those provided by Lambda runtime/layers:

```bash
# Install all dependencies for local development
pip install boto3 "aws-lambda-powertools[all]" -r requirements.txt

# Or use the project's pyproject.toml
pip install -e ".[dev,agent-strands]"
```

## Validation

To validate that dependencies are correctly configured, run:

```bash
python3 validate_dependencies.py
```

This script checks:
- Required dependencies are present in requirements.txt
- Runtime/layer dependencies are NOT duplicated in requirements.txt
- Version constraints are correct

## CDK Deployment

The CDK stack (`evaluation-stack.ts`) handles dependency installation automatically:

1. **PythonFunction** construct reads `requirements.txt`
2. Dependencies are installed during CDK synthesis
3. Lambda deployment package includes all application dependencies
4. Runtime and layer dependencies are available at execution time

## Troubleshooting

### Import Errors

If you encounter import errors during Lambda execution:

1. Check CloudWatch Logs for the specific missing module
2. Verify the module is in `requirements.txt` (if not runtime/layer provided)
3. Ensure version constraints are compatible
4. Redeploy the stack to rebuild the Lambda package

### Version Conflicts

If you encounter version conflicts:

1. Check the Lambda runtime Python version (currently 3.13)
2. Verify dependency compatibility with Python 3.13
3. Update version constraints in `requirements.txt`
4. Test locally with the same Python version

### Layer Issues

If aws-lambda-powertools is not available:

1. Verify the layer ARN in `evaluation-stack.ts`
2. Check the layer is available in your AWS region
3. Ensure the layer version matches your Python runtime
4. Update the layer version if needed

## References

- [AWS Lambda Python Runtimes](https://docs.aws.amazon.com/lambda/latest/dg/lambda-python.html)
- [AWS Lambda Powertools](https://docs.powertools.aws.dev/lambda/python/)
- [Strands Agents Documentation](https://github.com/awslabs/strands-agents)
- [Hypothesis Documentation](https://hypothesis.readthedocs.io/)
