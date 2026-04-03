# Contributing to FAST Samples

Thank you for your interest in contributing! Whether you're an AWS employee or an external community member, we welcome sample applications that demonstrate how to build on [FAST](https://github.com/awslabs/fullstack-solution-template-for-agentcore).

Please read through this document before submitting any issues or pull requests to ensure we have all the necessary information to effectively respond to your bug report or contribution.

## Before You Start

1. **Check for duplicates**: Make sure a similar sample doesn't already exist in this repository.
2. **Verify your sample is built on FAST**: Samples must use [FAST](https://github.com/awslabs/fullstack-solution-template-for-agentcore) as their starting point.
3. **Plan for finality**: It's difficult to make significant changes to a sample once it's published. Make sure you've incorporated all the features you want before submitting.

## Preparing Your Sample

### Content Requirements

Your sample's codebase **must** include:
- A well-written top-level `README.md` (see [Required Documentation](#required-documentation) below)
- An accurate architecture diagram (modify the original FAST draw.io diagram to reflect your sample's structure)
- A screenshot or short GIF of the UI in `docs/img/`

Your sample's codebase **must not** contain:
- Customer names or customer data
- Proprietary datasets or models
- Hardcoded credentials, secrets, or deployment-specific values (AWS account IDs, ARNs, Cognito pool IDs, Amplify domains, API Gateway URLs, etc.)
- Company or organization names — use generic names or anonymize

After copying your project, search for files containing deployment-specific values (e.g. `frontend/public/aws-exports.json`) and replace them with generic placeholders like `<account-id>`, `<region>`, `<your-amplify-domain>`, etc.

### Directory Structure

```
samples/your-sample-name/
├── README.md              # Sample-specific documentation
├── docs/
│   └── img/               # Screenshots and architecture diagrams
└── [your application files and directories]
```

### Required Documentation

Your sample's `README.md` should include:
- **Overview**: What your sample does and its use case
- **Key Differences**: How it differs from base FAST
- **Architecture**: Any architectural changes or additions
- **Prerequisites**: What users need before deploying (outside of the original core FAST requirements)
- **Deployment**: Step-by-step deployment instructions (if different from the core FAST deployment process)
- **Usage**: How to use the deployed application (e.g. sample queries or otherwise)

### Copying Your Sample

Use rsync to copy your FAST-based application, excluding build artifacts and git history:

```bash
cd /path/to/sample-FAST-applications
mkdir -p samples/your-sample-name

rsync -av \
  --exclude='.git' \
  --exclude='node_modules' \
  --exclude='cdk.out' \
  --exclude='cdk.context.json' \
  --exclude='.next' \
  --exclude='frontend/build' \
  --exclude='__pycache__' \
  --exclude='.ruff_cache' \
  --exclude='.venv' \
  --exclude='.agentcore.json' \
  /path/to/your-FAST-project/ samples/your-sample-name/
```

> **Tip**: If your sample is CDK-based, consider removing the `infra-terraform` directory (or vice versa). This improves clarity for users and reduces security scan findings.

### Passing CI Checks

The CI pipeline runs linting and security scans on every pull request. To catch issues before pushing:

```bash
cd samples/your-sample-name

# Python lint + format check
pip install ruff
ruff check
ruff format --check

# Auto-fix Python issues
ruff check --fix
ruff format

# JS/TS lint + format check
cd frontend && npm ci && npx eslint src/ && npx prettier --check "src/**/*.{ts,tsx,js,jsx,css,json}"
```

### Naming Conventions

Use descriptive, kebab-case names that indicate the key technology or use case:
- `langchain-async-research-agent`
- `multi-modal-document-analysis`
- `real-time-streaming-chat`

## Submitting Your Contribution
### Steps to Contribute
1. Fork this repository
2. Create a new directory under `samples/` with a descriptive name
3. Add your application code and documentation
4. Test that your deployment instructions work from a clean environment
5. Thoroughly check that you are abiding by all guidance in this CONTRIBUTING.md file
6. Push to your fork and open a pull request

### What Your Pull Request Should Contain

Your PR should:
- Add your sample as a new directory under `samples/` — avoid modifying other samples
- Update the "Available Samples" table in the root `README.md` with your sample's entry:

```markdown
### [Your Sample Name](samples/your-sample-directory/)
**Description**: Brief description of what this sample demonstrates
**Built on FAST**: version
**Key Differences from FAST**: What makes this sample unique
**Use Case**: When someone might want to use this pattern

![Sample UI](samples/your-sample-directory/docs/img/screenshot.png)
```

- Include clear commit messages and a PR description explaining what your sample does

### Review Process

Your contribution will be reviewed for:
- **Completeness**: All required documentation is present
- **Security**: No sensitive data, credentials, or security issues; automated [ASH](https://github.com/awslabs/automated-security-helper) security scans must pass
- **Quality**: Code and documentation meet basic quality standards
- **Functionality**: Deployment instructions are clear and complete

## Reporting Bugs / Opening Issues

When filing an issue, please include:
- Which sample the issue relates to
- Steps to reproduce the problem
- Expected vs. actual behavior
- Any relevant logs or error messages

## Contributing Back to FAST

If your sample reveals improvements that could benefit the base FAST template, consider submitting a pull request to the main [FAST repository](https://github.com/awslabs/fullstack-solution-template-for-agentcore).

## Code of Conduct

This project has adopted the [Amazon Open Source Code of Conduct](https://aws.github.io/code-of-conduct).
For more information see the [Code of Conduct FAQ](https://aws.github.io/code-of-conduct-faq) or contact
opensource-codeofconduct@amazon.com with any additional questions or comments.

## Security

If you discover a potential security issue in this project, we ask that you notify AWS/Amazon Security via our [vulnerability reporting page](http://aws.amazon.com/security/vulnerability-reporting/). Please do **not** create a public GitHub issue.

## Licensing

See the [LICENSE](LICENSE) file for our project's licensing. We will ask you to confirm the licensing of your contribution.
