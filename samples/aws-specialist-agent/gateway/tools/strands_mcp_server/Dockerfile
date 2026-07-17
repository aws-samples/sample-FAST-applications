# MCP server image for the Strands Agents docs server. Self-contained
# build context: only this directory is sent to Docker, unlike the agent pattern
# image which copies the whole repo.
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app

ENV UV_SYSTEM_PYTHON=1 \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN uv pip install --no-cache -r requirements.txt

# Numeric UID for USER, matching the agent pattern image convention
# (see agent/strands-single-agent/Dockerfile).
RUN useradd -m -u 1000 bedrock_agentcore
USER 1000

# AgentCore Runtime MCP contract: the platform connects to port 8000, path /mcp.
EXPOSE 8000

COPY server.py .

# The MCP endpoint only speaks streamable HTTP POST, so probe TCP reachability
# of the listener rather than an HTTP GET (which would 4xx).
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import socket; socket.create_connection(('localhost', 8000), timeout=2)" || exit 1

CMD ["python", "server.py"]
