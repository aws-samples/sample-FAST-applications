# Fullstack AgentCore Solution Template (FAST) - Sample Applications

This repository contains sample applications built using the [Fullstack AgentCore Solution Template (FAST)](https://github.com/awslabs/fullstack-solution-template-for-agentcore) as a starting point. Each sample demonstrates how to customize FAST for different use cases while leveraging AWS AgentCore.

## Purpose

While FAST provides a fully functional out-of-the-box chat application, it's designed to be customized for any use case that leverages AgentCore. These samples serve as:

- **Reference implementations** for common patterns and use cases
- **Starting points** for similar projects
- **Best practice examples** of how to extend FAST
- **Learning resources** for engineers

## Available Samples

### [Restaurant Assistant](samples/restaurant-assistant/)
**Description**: A restaurant assistant application with knowledge base integration, reservation management, and a professional customer-facing interface.
**Built on FAST**: v0.4.0
**Key Differences from FAST**: Adds OpenSearch Serverless knowledge base, DynamoDB reservations table, custom reservation tools, restaurant-themed landing page with chat widget, and file upload capabilities
**Use Case**: Building customer service assistants for hospitality businesses or any domain requiring knowledge base integration with transactional capabilities

![Restaurant Assistant UI](samples/restaurant-assistant/docs/img/restaurant-assistant-screenshot.png)

<!-- Template for new samples:
### [Sample Name](samples/sample-directory-name/)
**Description**: Brief description of what this sample demonstrates
**Built on FAST**: version
**Key Differences from FAST**: What makes this sample unique
**Use Case**: When you might want to use this pattern
-->

## Repository Structure

```
├── README.md              # This file
├── CONTRIBUTING.md        # Contribution guidelines
└── samples/               # Sample applications
    └── restaurant-assistant/
```

## Getting Started

1. **Fork FAST**: Start by forking the base [FAST repository](https://github.com/awslabs/fullstack-solution-template-for-agentcore)
2. **Browse samples**: Look through the available samples to find one similar to your use case
3. **Apply sample patterns**: Use a sample as reference to customize your FAST fork for your specific needs
4. **Deploy and test**: Follow the sample's README for deployment instructions

## Contributing

Have you built something with FAST? We'd love to see it! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on how to contribute your sample application.

## Important Notes

- **No customer data**: Samples should not contain any customer-specific data or information
- **Security**: Follow the same security best practices established in the main FAST repository
- **Documentation**: Each sample should include clear documentation about what it does and how it differs from base FAST

## Security

See [CONTRIBUTING](CONTRIBUTING.md#security-issue-notifications) for more information.

## Support

For questions about:
- **FAST itself**: See the main [FAST repository](https://github.com/awslabs/fullstack-solution-template-for-agentcore)
- **Specific samples**: Open an issue in this repository
- **Contributing samples**: See [CONTRIBUTING.md](CONTRIBUTING.md)

## License

This project is licensed under the Apache-2.0 License.

