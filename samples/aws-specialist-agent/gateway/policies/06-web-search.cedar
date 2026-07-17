// 06-web-search.cedar
//
// Allow finance and engineering departments to call the managed AgentCore
// Web Search tool exposed by the web-search-tool connector target.
// Web Search is a read-only lookup of external web content, so the same
// audience as the other read-only tools (02-aws-mcp-read.cedar,
// 05-strands-mcp.cedar) applies. Guests are denied by Cedar deny-by-default.
//
// The tool name comes from the built-in connector (WebSearch); the target
// name "web-search-tool" is set in CDK (backend-stack.ts:
// createWebSearchTarget). Action names follow "<TargetName>___<tool>".

permit(
  principal is AgentCore::OAuthUser,
  action in [
    AgentCore::Action::"web-search-tool___WebSearch"
  ],
  resource == AgentCore::Gateway::"{{GATEWAY_ARN}}"
)
when {
  principal.hasTag("department") &&
  (principal.getTag("department") == "finance" ||
   principal.getTag("department") == "engineering")
};
