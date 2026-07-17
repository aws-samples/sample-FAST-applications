"""Host the Strands Agents documentation MCP server on AgentCore Runtime.

This is a thin launcher around the upstream open-source server
(strands-agents-mcp-server, Apache-2.0). That package ships a stdio-only
entry point (its main() calls mcp.run() with no transport), so it is not
deployable on AgentCore Runtime as-is. Rather than fork it, we import its
already-constructed FastMCP instance, retarget it to the AgentCore Runtime
MCP contract, and start it over streamable HTTP:

  host 0.0.0.0, port 8000, path /mcp, stateless streamable HTTP.

The platform injects an Mcp-Session-Id header for microVM stickiness;
FastMCP in stateless mode accepts it. Overriding host/port/stateless_http
via mcp.settings before run() is verified to take effect (the streamable
HTTP app exposes /mcp). If a future upstream release stops honouring the
settings object, pin the working version in requirements.txt and revisit.

The two tools it exposes (search_docs, fetch_doc) query strandsagents.com
over outbound HTTPS and reject every other host in code, so this runtime is
placed on the AgentCore managed public network (it cannot run inside the
project's closed VPC, which has no NAT). Inbound auth is IAM/SigV4, matching
the gateway's GATEWAY_IAM_ROLE outbound credential.
"""

# The ignore covers the missing stubs when the upstream package is not on the
# type-checker's path; unit tests stub the module before import.
from mcp.server.transport_security import (  # type: ignore[import-not-found]
    TransportSecuritySettings,
)
from strands_mcp_server.server import (  # type: ignore[import-not-found]
    cache,
    mcp,
)

# AgentCore Runtime MCP contract.
mcp.settings.host = "0.0.0.0"
mcp.settings.port = 8000
mcp.settings.stateless_http = True

# Disable the MCP SDK's DNS-rebinding protection. The upstream package builds
# its server as FastMCP(APP_NAME) with the default host 127.0.0.1, and newer
# mcp releases (present in the pinned 1.27.1, absent in 1.22.0) auto-enable
# Host-header validation with a localhost-only allowlist when the server is
# CONSTRUCTED for a localhost host. Retargeting settings.host afterwards does
# not undo that, so the gateway's tools/list — arriving with the platform's
# internal Host header (a non-localhost host the allowlist does not include)
# — was rejected with HTTP 421 at target creation. Servers constructed with
# host="0.0.0.0" (e.g. the ltm-mcp target) never get the allowlist,
# which is why only this wrapped-upstream server needs the explicit opt-out.
# Safe here: the runtime is not reachable on a stable public DNS name a
# browser could be tricked into hitting, and inbound auth is IAM/SigV4.
mcp.settings.transport_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=False,
)


def main() -> None:
    """Warm the doc index, then serve over streamable HTTP.

    Mirrors the upstream main() (which warms the cache before run()) but
    starts the streamable-http transport instead of the default stdio one.
    """
    cache.ensure_ready()
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
