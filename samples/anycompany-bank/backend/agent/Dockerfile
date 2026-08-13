# AgentCore Runtime container for the KYC assessment agent.
# Must be built for linux/arm64 — AgentCore Runtime runs ARM64 only.
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app

ENV UV_SYSTEM_PYTHON=1 \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    OTEL_PYTHON_LOG_CORRELATION=true

COPY requirements.txt .
RUN uv pip install --no-cache -r requirements.txt && \
    uv pip install --no-cache aws-opentelemetry-distro==0.17.1

RUN useradd -m -u 1000 bedrock_agentcore
USER bedrock_agentcore

EXPOSE 8080

# Glob rather than naming modules individually: an explicit list silently drops
# newly added top-level modules from the image, failing only at runtime.
COPY *.py ./
COPY agents/ agents/
COPY lib/ lib/

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/ping', timeout=2)" || exit 1

CMD ["opentelemetry-instrument", "python", "-m", "kyc_agent"]
