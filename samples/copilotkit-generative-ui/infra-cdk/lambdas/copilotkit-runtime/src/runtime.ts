/**
 * Shared CopilotKit runtime — the single source of truth.
 * Imported by index.ts (Lambda) and server.ts (local dev).
 */
import { HttpAgent } from "@ag-ui/client";
import { AgentCoreRunner } from "@copilotkit/agentcore-runner";
import { CopilotRuntime, createCopilotHonoHandler } from "@copilotkit/runtime/v2";

function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} environment variable is required`);
  return value;
}

export function buildAgents(): Record<string, HttpAgent> {
  const agents: Record<string, HttpAgent> = {};

  const agentUrls = {
    "langgraph-single-agent": process.env.LANGGRAPH_AGENTCORE_AG_UI_URL,
    "strands-single-agent": process.env.STRANDS_AGENTCORE_AG_UI_URL,
  };

  for (const [name, url] of Object.entries(agentUrls)) {
    if (url) agents[name] = new HttpAgent({ url, headers: {} });
  }

  if (Object.keys(agents).length === 0) {
    agents.default = new HttpAgent({
      url: requireEnv("AGENTCORE_AG_UI_URL"),
      headers: {},
    });
  }

  return agents;
}

export function buildApp() {
  const agents = buildAgents();
  const agentName = process.env.COPILOTKIT_AGENT_NAME ?? "default";
  const defaultAgent =
    agents[agentName] ?? agents.default ?? Object.values(agents)[0];

  if (!defaultAgent)
    throw new Error("At least one CopilotKit agent URL must be configured");

  const runtime = new CopilotRuntime({
    agents: { ...agents, default: defaultAgent },
    // AgentCore keeps conversation history in AgentCore Memory. AgentCoreRunner
    // makes reconnects (e.g. a page refresh) work against that history: it answers
    // connect() for threads this instance has not run yet with an empty snapshot,
    // and adds the tool-call results CopilotKit needs to a replayed snapshot.
    runner: new AgentCoreRunner(),
    // Generative UI: when the frontend registers a component catalog
    // (`generativeUI={{ ui }}` on CopilotKitProvider), the runtime gives the
    // agent a `render_ui` tool plus the catalog guide, validates every streamed
    // operation against that catalog, and forwards the surface to the browser.
    // It is on by default for such runs; set explicitly here for clarity.
    generativeUI: true,
    // Open generative UI: MCP Apps (interactive UI served by an MCP server).
    mcpApps: {
      servers: [
        {
          type: "http",
          url: process.env.MCP_SERVER_URL || "https://mcp.excalidraw.com/mcp",
          serverId: "example_mcp_app",
        },
      ],
    },
  });

  return createCopilotHonoHandler({ runtime, basePath: "/copilotkit" });
}
