import * as cdk from "aws-cdk-lib"
import * as cognito from "aws-cdk-lib/aws-cognito"
import * as iam from "aws-cdk-lib/aws-iam"
import * as ssm from "aws-cdk-lib/aws-ssm"
import * as secretsmanager from "aws-cdk-lib/aws-secretsmanager"
import * as logs from "aws-cdk-lib/aws-logs"
import * as agentcore from "aws-cdk-lib/aws-bedrockagentcore"
import * as lambda from "aws-cdk-lib/aws-lambda"
import * as cr from "aws-cdk-lib/custom-resources"
import { Construct } from "constructs"
import { AppConfig } from "../utils/config-manager"
import * as path from "path"

/** An extra Lambda tool target to register on the gateway. */
export interface ExtraGatewayTarget {
  name: string
  description: string
  lambdaFunction: lambda.IFunction
  toolSpecPath: string
}

export interface GatewayConstructProps {
  config: AppConfig
  /** User pool, used for the OIDC issuer/discovery URL and Cognito describe permissions. */
  userPool: cognito.IUserPool
  /** Machine (M2M) client whose id is the gateway authorizer's allowed client. */
  machineClient: cognito.UserPoolClient
  /** Machine client secret, read by the OAuth2 credential provider Lambda. */
  machineClientSecret: secretsmanager.Secret
  /** Additional Lambda tool targets (e.g. doc_search, structured_search). */
  extraTargets?: ExtraGatewayTarget[]
}

/**
 * AgentCore Gateway and the OAuth2 credential provider the runtime uses to
 * authenticate to it. The gateway enforces authentication (inbound JWT); this
 * sample does not add an authorization layer. The tool targets (doc_search,
 * structured_search) are passed in as extraTargets and registered below.
 *
 * Inbound auth is created here from config so it can be swapped (e.g. a different
 * JWT issuer) without changing callers.
 */
export class GatewayConstruct extends Construct {
  public readonly gatewayUrl: string
  public readonly gatewayArn: string
  /**
   * Name of the OAuth2 credential provider the runtime uses to authenticate to this
   * gateway. Pass to AgentConstruct's gatewayCredentialProviderName prop to wire the
   * agent to this gateway. Source of truth for the agent<->gateway integration.
   */
  public readonly credentialProviderName: string
  private readonly region: string
  private readonly account: string

  constructor(scope: Construct, id: string, props: GatewayConstructProps) {
    super(scope, id)

    const stack = cdk.Stack.of(this)
    this.region = stack.region
    this.account = stack.account
    const { config, userPool, machineClient, machineClientSecret } = props

    // Comprehensive IAM role for the gateway
    const gatewayRole = new iam.Role(this, "GatewayRole", {
      assumedBy: new iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
      description: "Role for AgentCore Gateway with comprehensive permissions",
    })

    gatewayRole.addToPolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
        resources: [
          "arn:aws:bedrock:*::foundation-model/*",
          `arn:aws:bedrock:*:${this.account}:inference-profile/*`,
        ],
      })
    )

    gatewayRole.addToPolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ["ssm:GetParameter", "ssm:GetParameters"],
        resources: [
          `arn:aws:ssm:${this.region}:${this.account}:parameter/${config.stack_name_base}/*`,
        ],
      })
    )

    // Cognito permissions (IdP-specific; swap for a different identity provider).
    gatewayRole.addToPolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ["cognito-idp:DescribeUserPoolClient", "cognito-idp:InitiateAuth"],
        resources: [userPool.userPoolArn],
      })
    )

    gatewayRole.addToPolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
        resources: [
          `arn:aws:logs:${this.region}:${this.account}:log-group:/aws/bedrock-agentcore/*`,
        ],
      })
    )

    // OIDC issuer/discovery URL for the inbound authorizer (Cognito today).
    const cognitoIssuer = `https://cognito-idp.${this.region}.amazonaws.com/${userPool.userPoolId}`
    const cognitoDiscoveryUrl = `${cognitoIssuer}/.well-known/openid-configuration`

    // OAuth2 Credential Provider so the runtime can authenticate to the gateway.
    const providerName = `${config.stack_name_base}-runtime-gateway-auth`
    this.credentialProviderName = providerName

    const oauth2ProviderLambda = new lambda.Function(this, "OAuth2ProviderLambda", {
      runtime: lambda.Runtime.PYTHON_3_13,
      handler: "index.handler",
      code: lambda.Code.fromAsset(
        path.join(__dirname, "../../lambdas/oauth2-provider") // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal
      ),
      timeout: cdk.Duration.minutes(5),
      logGroup: new logs.LogGroup(this, "OAuth2ProviderLambdaLogGroup", {
        logGroupName: `/aws/lambda/${config.stack_name_base}-oauth2-provider`,
        retention: logs.RetentionDays.ONE_WEEK,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      }),
    })

    machineClientSecret.grantRead(oauth2ProviderLambda)

    oauth2ProviderLambda.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          "bedrock-agentcore:CreateOauth2CredentialProvider",
          "bedrock-agentcore:DeleteOauth2CredentialProvider",
          "bedrock-agentcore:GetOauth2CredentialProvider",
        ],
        resources: [
          `arn:aws:bedrock-agentcore:${this.region}:${this.account}:token-vault/default`,
          `arn:aws:bedrock-agentcore:${this.region}:${this.account}:token-vault/default/oauth2credentialprovider/*`,
        ],
      })
    )

    oauth2ProviderLambda.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          "bedrock-agentcore:CreateTokenVault",
          "bedrock-agentcore:GetTokenVault",
          "bedrock-agentcore:DeleteTokenVault",
        ],
        resources: [
          `arn:aws:bedrock-agentcore:${this.region}:${this.account}:token-vault/default`,
          `arn:aws:bedrock-agentcore:${this.region}:${this.account}:token-vault/default/*`,
        ],
      })
    )

    oauth2ProviderLambda.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          "secretsmanager:CreateSecret",
          "secretsmanager:DeleteSecret",
          "secretsmanager:DescribeSecret",
          "secretsmanager:PutSecretValue",
        ],
        resources: [
          `arn:aws:secretsmanager:${this.region}:${this.account}:secret:bedrock-agentcore-identity!default/oauth2/*`,
        ],
      })
    )

    const oauth2Provider = new cr.Provider(this, "OAuth2ProviderProvider", {
      onEventHandler: oauth2ProviderLambda,
    })

    new cdk.CustomResource(this, "RuntimeCredentialProvider", {
      serviceToken: oauth2Provider.serviceToken,
      properties: {
        ProviderName: providerName,
        ClientSecretArn: machineClientSecret.secretArn,
        DiscoveryUrl: cognitoDiscoveryUrl,
        ClientId: machineClient.userPoolClientId,
      },
    })

    // Create Gateway using L2 construct.
    // [SWAP POINT] authorizerConfiguration is the inbound-auth plug point (Cognito custom
    // JWT here; GatewayAuthorizer also supports IAM / other JWT issuers). Tool targets are
    // registered from extraTargets below (Lambda here; targets can also be OpenAPI/Smithy).
    const gateway = new agentcore.Gateway(this, "AgentCoreGateway", {
      gatewayName: `${config.stack_name_base}-gateway`,
      role: gatewayRole,
      protocolConfiguration: new agentcore.McpProtocolConfiguration({
        supportedVersions: [agentcore.MCPProtocolVersion.MCP_2025_03_26],
      }),
      authorizerConfiguration: agentcore.GatewayAuthorizer.usingCustomJwt({
        discoveryUrl: cognitoDiscoveryUrl,
        allowedClients: [machineClient.userPoolClientId],
      }),
      description: "AgentCore Gateway with MCP protocol and JWT authentication",
    })

    gateway.node.addDependency(machineClient)
    gateway.node.addDependency(gatewayRole)

    // Register the Lambda tool targets (doc_search, structured_search).
    // addLambdaTarget grants the gateway invoke + the resource-based permission the
    // CreateGatewayTarget dry-run validation requires.
    for (const target of props.extraTargets ?? []) {
      gateway.addLambdaTarget(target.name, {
        gatewayTargetName: target.name,
        description: target.description,
        lambdaFunction: target.lambdaFunction,
        toolSchema: agentcore.ToolSchema.fromLocalAsset(target.toolSpecPath),
      })
    }

    this.gatewayUrl = gateway.gatewayUrl!
    this.gatewayArn = gateway.gatewayArn

    // Store AgentCore Gateway URL in SSM for AgentCore Runtime access
    new ssm.StringParameter(this, "GatewayUrlParam", {
      parameterName: `/${config.stack_name_base}/gateway_url`,
      stringValue: gateway.gatewayUrl!,
      description: "AgentCore Gateway URL",
    })

    new cdk.CfnOutput(this, "GatewayId", {
      value: gateway.gatewayId,
      description: "AgentCore Gateway ID",
    })

    new cdk.CfnOutput(this, "GatewayUrl", {
      value: gateway.gatewayUrl!,
      description: "AgentCore Gateway URL",
    })

    new cdk.CfnOutput(this, "GatewayArn", {
      value: gateway.gatewayArn,
      description: "AgentCore Gateway ARN",
    })
  }
}
