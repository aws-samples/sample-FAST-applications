// 02-aws-mcp-read.cedar
//
// Allow finance and engineering departments to call read-only tools exposed
// by the AWS MCP Server (Agent Toolkit for AWS). These tools cover AWS
// documentation lookup, regional availability, and skill retrieval — they do
// not mutate AWS resources.
//
// The destructive tools (aws___call_aws, aws___run_script) are gated
// separately in 03-aws-mcp-destructive.cedar.

permit(
  principal is AgentCore::OAuthUser,
  action in [
    AgentCore::Action::"aws-mcp___aws___list_regions",
    AgentCore::Action::"aws-mcp___aws___search_documentation",
    AgentCore::Action::"aws-mcp___aws___read_documentation",
    AgentCore::Action::"aws-mcp___aws___retrieve_skill",
    AgentCore::Action::"aws-mcp___aws___get_regional_availability"
  ],
  resource == AgentCore::Gateway::"{{GATEWAY_ARN}}"
)
when {
  principal.hasTag("department") &&
  (principal.getTag("department") == "finance" ||
   principal.getTag("department") == "engineering")
};
