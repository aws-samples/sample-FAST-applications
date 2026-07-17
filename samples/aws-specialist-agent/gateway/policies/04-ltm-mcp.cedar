// 04-ltm-mcp.cedar
//
// Allow finance and engineering departments to list their own long-term
// memory facts via the ltm-mcp target (the MCP server hosted on AgentCore
// Runtime). The tool is read-only and scoped to the caller's own
// /facts namespace by the propagated actor-id header, so the same audience
// as the other read-only tools (02-aws-mcp-read.cedar) applies. Guests are
// denied by Cedar deny-by-default.

permit(
  principal is AgentCore::OAuthUser,
  action == AgentCore::Action::"ltm-mcp___list_long_term_memories",
  resource == AgentCore::Gateway::"{{GATEWAY_ARN}}"
)
when {
  principal.hasTag("department") &&
  (principal.getTag("department") == "finance" ||
   principal.getTag("department") == "engineering")
};
