// 01-sample-tool.cedar
//
// Allow finance and engineering departments to call the demo Lambda-backed
// "sample-tool-target" (text_analysis_tool). Guests are denied automatically
// by Cedar deny-by-default — they are not listed in the when block.
//
// Tool name comes from gateway/tools/sample_tool/tool_spec.json; the target
// name is set in CDK (`backend-stack.ts:createAgentCoreGateway`).

permit(
  principal is AgentCore::OAuthUser,
  action == AgentCore::Action::"sample-tool-target___text_analysis_tool",
  resource == AgentCore::Gateway::"{{GATEWAY_ARN}}"
)
when {
  principal.hasTag("department") &&
  (principal.getTag("department") == "finance" ||
   principal.getTag("department") == "engineering")
};
