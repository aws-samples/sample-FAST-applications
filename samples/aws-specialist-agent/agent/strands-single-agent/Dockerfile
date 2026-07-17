# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app

# Configure UV for container environment
ENV UV_SYSTEM_PYTHON=1 \
    UV_COMPILE_BYTECODE=1 \
    DOCKER_CONTAINER=1 \
    OTEL_PYTHON_LOG_CORRELATION=true \
    PYTHONUNBUFFERED=1

# Copy pyproject.toml and shared packages for installation
COPY pyproject.toml .
COPY gateway/ gateway/

# Copy and install agent-specific requirements first
COPY agent/strands-single-agent/requirements.txt requirements.txt
RUN uv pip install --no-cache -r requirements.txt && \
    uv pip install --no-cache aws-opentelemetry-distro==0.16.0

# Install FAST package with only core dependencies (no dev/agent optional deps)
RUN uv pip install --no-cache -e . --no-deps && \
    uv pip install --no-cache requests>=2.31.0

# Create non-root user. Use the numeric UID for USER: a non-numeric USER can
# trigger an S3 Files "Failed to mount overlay" (HTTP 424) on images with many
# layers, and the numeric UID also makes the match with the S3 Files access
# point posixUser (1000:1000) explicit.
RUN useradd -m -u 1000 bedrock_agentcore
USER 1000

EXPOSE 8080

# Copy agent code and tools
COPY agent/strands-single-agent/basic_agent.py .
COPY agent/strands-single-agent/models.py .
COPY agent/strands-single-agent/tools/ tools/
COPY agent/utils/ utils/

# Healthcheck
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/ping', timeout=2)" || exit 1

# Start agent with OpenTelemetry instrumentation
CMD ["opentelemetry-instrument", "python", "-m", "basic_agent"]
