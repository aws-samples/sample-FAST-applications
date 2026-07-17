// 03-aws-mcp-destructive.cedar
//
// Allow ONLY the finance department to call destructive tools exposed by the
// AWS MCP Server:
//
//   * aws___call_aws  — invokes any AWS API the gateway role permits
//   * aws___run_script — executes arbitrary Python in the AWS MCP sandbox
//
// Both can mutate AWS resources or produce non-trivial side-effects, so they
// are restricted to finance/admin in keeping with the least-privilege spirit
// of Cedar V2. Engineering and guest are denied by default.
//
// To loosen this gate (e.g. let engineering call_aws too), edit the when block
// or replace the equality with an OR list — note that this file must contain
// exactly one Cedar statement (the AgentCore CreatePolicy API limit).

permit(
  principal is AgentCore::OAuthUser,
  action in [
    AgentCore::Action::"aws-mcp___aws___call_aws",
    AgentCore::Action::"aws-mcp___aws___run_script"
  ],
  resource == AgentCore::Gateway::"{{GATEWAY_ARN}}"
)
when {
  principal.hasTag("department") &&
  principal.getTag("department") == "finance"
};
