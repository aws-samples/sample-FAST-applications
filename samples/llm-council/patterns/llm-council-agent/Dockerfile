FROM public.ecr.aws/docker/library/python:3.13-slim

WORKDIR /app

# Copy and install agent-specific requirements
COPY patterns/llm-council-agent/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared utilities
COPY gateway/ gateway/
COPY tools/ tools/

# Copy the agent code as a package
COPY patterns/llm-council-agent/ llm_council_agent/

# Create non-root user
RUN useradd -m -u 1000 bedrock_agentcore
USER bedrock_agentcore

EXPOSE 8080

CMD ["python", "-m", "llm_council_agent.council_agent"]
