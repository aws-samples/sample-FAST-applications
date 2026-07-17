// 05-strands-mcp.cedar
//
// Allow finance and engineering departments to call the Strands Agents
// documentation tools exposed by the strands-mcp target (the MCP server
// hosted on AgentCore Runtime). Both tools are read-only doc
// lookups against the public Strands docs site, so the same audience as the
// other read-only tools (02-aws-mcp-read.cedar) applies. Guests are denied
// by Cedar deny-by-default.
//
// Tool names come from the upstream server (search_docs, fetch_doc); the
// target name "strands-mcp" is set in CDK (backend-stack.ts:
// createStrandsMcpServerTarget). Action names follow "<TargetName>___<tool>".

permit(
  principal is AgentCore::OAuthUser,
  action in [
    AgentCore::Action::"strands-mcp___search_docs",
    AgentCore::Action::"strands-mcp___fetch_doc"
  ],
  resource == AgentCore::Gateway::"{{GATEWAY_ARN}}"
)
when {
  principal.hasTag("department") &&
  (principal.getTag("department") == "finance" ||
   principal.getTag("department") == "engineering")
};
