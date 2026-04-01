# Contributing to FAST Samples

Thank you for your interest in contributing to our project. We greatly value feedback and contributions from our community.

Please read through this document before submitting any issues or pull requests to ensure we have all the necessary
information to effectively respond to your bug report or contribution.

## Contributing a Sample Application

Have you built something with FAST? We'd love to see it! This section will help you prepare and submit your FAST-based project as a sample for others to learn from.

### Before You Contribute

#### Prerequisites
- Your sample application should be built starting from [FAST](https://github.com/awslabs/fullstack-solution-template-for-agentcore)
- The application should be functional and deployable
- You should be willing to be listed as a contact person for questions about your sample

#### Content Guidelines
- **No proprietary data**: Remove all company-specific data, credentials, and sensitive information
- **Anonymize references**: Avoid company or organization names when possible; use generic names or anonymize
- **Security**: Ensure your sample follows the same security best practices as FAST
- **Documentation**: Your sample should be well-documented and easy to understand

### 1. Prepare Your Sample

#### Directory Structure
Create your sample in the following structure:
```
samples/your-sample-name/
├── README.md              # Sample-specific documentation including an architecture diagram
└── [your application files and directories]
```

#### Copying Your Sample
To copy your FAST-based application to the samples repository, use rsync to exclude build artifacts and git history:

```bash
# Navigate to the samples repository
cd /path/to/sample-FAST-applications

# Create your sample directory
mkdir -p samples/your-sample-name

# Copy files excluding build artifacts, git history, and cache files
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
  --exclude='.gitlab-ci.yml' \
  /path/to/your-FAST-project/ samples/your-sample-name/
```

This ensures only source code and documentation are copied, keeping the repository clean and focused.

After copying, check for any files containing deployment-specific values (e.g. `frontend/public/aws-exports.json`) and replace real AWS account IDs, Cognito pool IDs, ARNs, Amplify domains, and API Gateway URLs with generic placeholders like `<account-id>`, `<region>`, `<your-amplify-domain>`, etc.

#### Passing CI Lint Checks

The CI pipeline runs Python linting (ruff) and JS/TS linting (ESLint + Prettier) on changed samples. To catch issues before pushing, run from your sample directory:

```bash
cd samples/your-sample-name

# Install ruff if you don't have it (also listed in requirements-dev.txt)
pip install ruff

# Python lint + format check (or use: make lint-cicd)
ruff check
ruff format --check

# Auto-fix Python issues (or use: make lint)
ruff check --fix
ruff format

# JS/TS lint + format check
cd frontend && npm ci && npx eslint src/ && npx prettier --check "src/**/*.{ts,tsx,js,jsx,css,json}"
```

#### Required Documentation
Your sample's `README.md` should include:
- **Overview**: What your sample does and its use case
- **Key Differences**: How it differs from base FAST
- **Architecture**: Any architectural changes or additions
- **Prerequisites**: What users need before deploying
- **Deployment**: Step-by-step deployment instructions
- **Usage**: How to use the deployed application

### 2. Submit Your Contribution

#### Step 1: Add Your Sample
1. Fork this repository
2. Create a new directory under `samples/` with a descriptive name (use kebab-case)
3. Add your application code and documentation
4. Test that your deployment instructions work

#### Step 2: Update the Main README
Add your sample to the "Available Samples" section in the main README.md:

```markdown
### [Your Sample Name](samples/your-sample-directory/)
**Description**: Brief description of what this sample demonstrates
**Built on FAST**: version
**Key Differences from FAST**: What makes this sample unique
**Use Case**: When someone might want to use this pattern
```

#### Step 3: Create a Pull Request
1. Commit your changes with clear commit messages
2. Push to your fork
3. Create a pull request with:
   - Clear title describing your sample
   - Description explaining what your sample does
   - Any special notes for reviewers

### 3. Review Process

Your contribution will be reviewed for:
- **Completeness**: All required documentation is present
- **Security**: No sensitive data or security issues
- **Quality**: Code and documentation meet basic quality standards
- **Functionality**: Deployment instructions work as documented

### Sample Naming Conventions

Use descriptive, kebab-case names that indicate the key technology or use case:
- `langchain-async-research-agent`
- `multi-modal-document-analysis`
- `real-time-streaming-chat`

### Contributing Back to FAST

If your sample reveals improvements that could benefit the base FAST repository, consider submitting a pull request to the main [FAST repository](https://github.com/awslabs/fullstack-solution-template-for-agentcore) with your insights and suggested changes.


## Code of Conduct

This project has adopted the [Amazon Open Source Code of Conduct](https://aws.github.io/code-of-conduct).
For more information see the [Code of Conduct FAQ](https://aws.github.io/code-of-conduct-faq) or contact
opensource-codeofconduct@amazon.com with any additional questions or comments.

## Security Issue Notifications

If you discover a potential security issue in this project we ask that you notify AWS/Amazon Security via our [vulnerability reporting page](http://aws.amazon.com/security/vulnerability-reporting/). Please do **not** create a public GitHub issue.

## Licensing

See the [LICENSE](LICENSE) file for our project's licensing. We will ask you to confirm the licensing of your contribution.
