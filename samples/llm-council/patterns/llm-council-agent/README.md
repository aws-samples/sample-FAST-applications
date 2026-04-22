# LLM Council Agent Pattern

A multi-agent deliberation system where multiple LLMs collaborate to answer questions through a 3-stage process. Based on [Andrej Karpathy's LLM Council](https://github.com/karpathy/llm-council) architecture, adapted for AWS Bedrock and AgentCore.

## Overview

Instead of relying on a single LLM, the Council pattern groups multiple models together to deliberate on answers. This produces higher quality responses by:

1. **Diverse Perspectives**: Different models bring different strengths
2. **Peer Review**: Models evaluate each other's work anonymously
3. **Synthesis**: A chairman model combines the best insights

## Architecture

### Stage 1: Individual Responses
All council members receive the user's question and provide independent responses in parallel. No model sees other models' answers at this stage.

### Stage 2: Anonymized Peer Review
Each model receives all responses (anonymized as "Response A", "Response B", etc.) and:
- Evaluates each response's strengths and weaknesses
- Ranks them from best to worst
- Provides reasoning for the rankings

Anonymization prevents models from favoring their own work or showing brand bias.

### Stage 3: Chairman Synthesis
A designated chairman model receives:
- All individual responses from Stage 1
- All peer evaluations and rankings from Stage 2

The chairman synthesizes this into a comprehensive final answer representing the council's collective wisdom.

## Configuration

Edit `config.py` to customize the council:

```python
# Council member models
COUNCIL_MODELS = [
    "us.anthropic.claude-sonnet-4-5-20250929-v1:0",  # Balanced
    "us.anthropic.claude-3-5-haiku-20241022-v1:0",   # Fast
    "us.anthropic.claude-opus-4-20250514-v1:0",      # Deep reasoning
]

# Chairman model
CHAIRMAN_MODEL = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
```

## Response Format

The agent returns structured data for each stage:

```json
{
  "stage1": [
    {
      "model": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
      "response": "Individual response text..."
    }
  ],
  "stage2": [
    {
      "model": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
      "ranking": "Full evaluation and ranking text...",
      "parsed_ranking": ["Response A", "Response C", "Response B"]
    }
  ],
  "stage3": {
    "model": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "response": "Final synthesized answer..."
  },
  "metadata": {
    "label_to_model": {
      "Response A": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
      "Response B": "us.anthropic.claude-3-5-haiku-20241022-v1:0",
      "Response C": "us.anthropic.claude-opus-4-20250514-v1:0"
    },
    "aggregate_rankings": [
      {
        "model": "us.anthropic.claude-opus-4-20250514-v1:0",
        "average_rank": 1.33,
        "rankings_count": 3
      }
    ]
  }
}
```

## Key Features

### Parallel Execution
All model invocations within each stage run in parallel using `asyncio.gather()`, minimizing total response time.

### Graceful Degradation
If some models fail, the council continues with successful responses. The system only fails if all models fail.

### Structured Prompts
Carefully crafted prompts ensure:
- Models provide detailed evaluations
- Rankings follow a parseable format
- Chairman has full context for synthesis

### Cost Efficiency
Direct Bedrock API calls with no intermediary services. You only pay for the model invocations.

## Files

- `council_agent.py` - Main entrypoint for AgentCore Runtime
- `council.py` - Core 3-stage orchestration logic
- `bedrock_client.py` - Async Bedrock API wrapper
- `prompts.py` - Prompt templates for each stage
- `ranking_parser.py` - Parse and aggregate peer rankings
- `config.py` - Council configuration
- `requirements.txt` - Python dependencies
- `Dockerfile` - Container configuration (for Docker deployment)

## Deployment

1. Update `infra-cdk/config.yaml`:
   ```yaml
   backend:
     pattern: "llm-council-agent"
   ```

2. Deploy the infrastructure:
   ```bash
   cd infra-cdk
   cdk deploy
   ```

3. The agent will be deployed to AgentCore Runtime and accessible through the FAST frontend.

## Performance Characteristics

- **Response Time**: ~3-4x slower than single model (due to 3 stages)
- **Cost**: ~4x more expensive (multiple model invocations)
- **Quality**: Significantly higher for complex questions
- **Bias Reduction**: Anonymization eliminates inter-model bias

## When to Use

The council pattern is ideal for:
- Complex questions requiring deep reasoning
- Scenarios where accuracy is critical
- Cases where you want diverse perspectives
- Situations where bias reduction is important

For simple queries, a single model may be more cost-effective.

## Customization

### Adding Models
Add more models to `COUNCIL_MODELS` in `config.py`. The system automatically handles any number of council members.

### Changing the Chairman
Set `CHAIRMAN_MODEL` to any Bedrock model. It can be the same as a council member or different.

### Adjusting Prompts
Modify prompts in `prompts.py` to change how models evaluate and synthesize responses.

### Temperature Settings
Adjust temperature in `bedrock_client.py` invocations to control response randomness.

## References

- [Original LLM Council by Andrej Karpathy](https://github.com/karpathy/llm-council)
- [LLM Council Architecture Analysis](https://akillness.github.io/posts/llm-council-complete-architecture-analysis/)
- [AWS Bedrock Documentation](https://docs.aws.amazon.com/bedrock/)
- [AgentCore Documentation](https://docs.aws.amazon.com/bedrock/latest/userguide/agentcore.html)
