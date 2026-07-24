import * as cdk from "aws-cdk-lib"
import * as ec2 from "aws-cdk-lib/aws-ec2"
import * as iam from "aws-cdk-lib/aws-iam"
import * as ssm from "aws-cdk-lib/aws-ssm"
import * as agentcore from "aws-cdk-lib/aws-bedrockagentcore"
import * as ecr_assets from "aws-cdk-lib/aws-ecr-assets"
import * as s3 from "aws-cdk-lib/aws-s3"
import { Construct } from "constructs"
import { AppConfig } from "../utils/config-manager"
import { AgentCoreRole } from "../utils/agentcore-role"
import * as path from "path"

export interface AgentConstructProps {
  config: AppConfig
  /** User pool id for the runtime's JWT authorizer issuer. */
  userPoolId: string
  /** User pool client id allowed by the runtime's JWT authorizer. */
  userPoolClientId: string
  /**
   * OPTIONAL gateway integration. When set (to GatewayConstruct.credentialProviderName),
   * the agent is wired to authenticate to that gateway. Omit to run the agent without a
   * gateway — this is the single agent->gateway seam, controlled from the composition root.
   */
  gatewayCredentialProviderName?: string
  /** KB documents bucket; the runtime presigns citation URLs from it. */
  documentsBucket: s3.IBucket
}

/**
 * The agent runtime and its short-term memory. Loosely coupled to the gateway: it reads
 * the gateway URL from SSM and authenticates via the OAuth2 credential provider (looked up
 * by name), so it has no deploy-time dependency on the gateway construct.
 */
export class AgentConstruct extends Construct {
  public runtimeArn: string
  public memoryArn: string
  private readonly region: string
  private readonly account: string
  private readonly documentsBucket: s3.IBucket
  private agentRuntime: agentcore.Runtime

  constructor(scope: Construct, id: string, props: AgentConstructProps) {
    super(scope, id)

    const stack = cdk.Stack.of(this)
    this.region = stack.region
    this.account = stack.account
    this.documentsBucket = props.documentsBucket
    this.createAgentCoreRuntime(
      props.config,
      props.userPoolId,
      props.userPoolClientId,
      props.gatewayCredentialProviderName
    )

    new ssm.StringParameter(this, "RuntimeArnParam", {
      parameterName: `/${props.config.stack_name_base}/runtime-arn`,
      stringValue: this.runtimeArn,
    })
  }

  private createAgentCoreRuntime(
    config: AppConfig,
    userPoolId: string,
    userPoolClientId: string,
    gatewayCredentialProviderName?: string
  ): void {
    const pattern = config.backend?.pattern || "strands-agent"
    const stack = cdk.Stack.of(this)
    // DOCKER DEPLOYMENT: the agent runtime is built from the pattern's Dockerfile as an
    // ARM64 container image and pushed to ECR. Docker (or finch) must be available.
    const agentRuntimeArtifact: agentcore.AgentRuntimeArtifact =
      agentcore.AgentRuntimeArtifact.fromAsset(
        path.resolve(__dirname, "..", "..", ".."), // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal
        {
          platform: ecr_assets.Platform.LINUX_ARM64,
          file: `patterns/${pattern}/Dockerfile`,
        }
      )

    // [Network] Plug point for runtime networking. PUBLIC (default) or VPC, selected by
    // config.backend.network_mode. To change networking behavior, edit only
    // buildNetworkConfiguration(); the rest of the agent is network-agnostic.
    const networkConfiguration = this.buildNetworkConfiguration(config)

    // JWT authorizer (Cognito issuer).
    const authorizerConfiguration = agentcore.RuntimeAuthorizerConfiguration.usingJWT(
      `https://cognito-idp.${stack.region}.amazonaws.com/${userPoolId}/.well-known/openid-configuration`,
      [userPoolClientId]
    )

    const agentRole = new AgentCoreRole(this, "AgentCoreRole")

    // Model selection: allow inference through the Bedrock Mantle endpoint so the
    // UI can pick GPT (openai.*) models in addition to native Bedrock models.
    agentRole.addManagedPolicy(
      iam.ManagedPolicy.fromAwsManagedPolicyName("AmazonBedrockMantleInferenceAccess")
    )

    const memory = new agentcore.Memory(this, "AgentMemory", {
      memoryName: cdk.Names.uniqueResourceName(this, { maxLength: 48 }),
      expirationDuration: cdk.Duration.days(30),
      description: `Short-term memory for ${config.stack_name_base} agent`,
      // [Long-term memory] OPTIONAL, off by default (config.backend.use_long_term_memory).
      // When enabled, a SemanticMemoryStrategy extracts and stores facts across sessions
      // (incurs per-record cost). When disabled, the memory is short-term only — no
      // strategy, no extraction, no cost. Retrieval is additionally gated in the agent via
      // the USE_LONG_TERM_MEMORY env var. To remove LTM entirely, drop this strategy list.
      memoryStrategies: config.backend.use_long_term_memory
        ? [
            agentcore.MemoryStrategy.usingSemantic({
              strategyName: "FactExtractor",
              namespaces: ["/facts/{actorId}"],
            }),
          ]
        : [],
      executionRole: agentRole,
      tags: {
        Name: `${config.stack_name_base}_Memory`,
        ManagedBy: "CDK",
      },
    })
    const memoryId = memory.memoryId
    this.memoryArn = memory.memoryArn

    agentRole.addToPolicy(
      new iam.PolicyStatement({
        sid: "MemoryResourceAccess",
        effect: iam.Effect.ALLOW,
        actions: [
          "bedrock-agentcore:CreateEvent",
          "bedrock-agentcore:GetEvent",
          "bedrock-agentcore:ListEvents",
          "bedrock-agentcore:RetrieveMemoryRecords",
        ],
        resources: [this.memoryArn],
      })
    )

    agentRole.addToPolicy(
      new iam.PolicyStatement({
        sid: "SSMParameterAccess",
        effect: iam.Effect.ALLOW,
        actions: ["ssm:GetParameter", "ssm:GetParameters"],
        resources: [
          `arn:aws:ssm:${this.region}:${this.account}:parameter/${config.stack_name_base}/*`,
        ],
      })
    )

    const envVars: { [key: string]: string } = {
      AWS_REGION: stack.region,
      AWS_DEFAULT_REGION: stack.region,
      MEMORY_ID: memoryId,
      STACK_NAME: config.stack_name_base,
      // [Long-term memory] runtime config: USE_LONG_TERM_MEMORY gates retrieval in the
      // agent (paired with the strategy above); the two LTM_* values tune retrieval.
      USE_LONG_TERM_MEMORY: config.backend.use_long_term_memory ? "true" : "false",
      LTM_TOP_K: String(config.backend.ltm_top_k),
      LTM_RELEVANCE_SCORE: String(config.backend.ltm_relevance_score),
      // Source documents bucket — cite_sources presigns citation URLs from it.
      DOCS_BUCKET: this.documentsBucket.bucketName,
    }

    // Allow the runtime to presign GET URLs for source documents (cite_sources).
    this.documentsBucket.grantRead(agentRole)

    // Agent <-> Gateway integration. This is the SINGLE point of coupling between the
    // agent and the gateway, and it is opt-in: it runs only when the composition root
    // passes gatewayCredentialProviderName. Omit that prop (and the GatewayConstruct) to
    // run the agent without a gateway — nothing else in the agent depends on it.
    if (gatewayCredentialProviderName) {
      this.addGatewayIntegration(config, gatewayCredentialProviderName, agentRole, envVars)
    }

    this.agentRuntime = new agentcore.Runtime(this, "Runtime", {
      runtimeName: `${config.stack_name_base.replace(/-/g, "_")}_${config.backend.agent_name}`,
      agentRuntimeArtifact: agentRuntimeArtifact,
      executionRole: agentRole,
      networkConfiguration: networkConfiguration,
      protocolConfiguration: agentcore.ProtocolType.HTTP,
      environmentVariables: envVars,
      authorizerConfiguration: authorizerConfiguration,
      requestHeaderConfiguration: {
        allowlistedHeaders: ["Authorization"],
      },
      description: `${pattern} agent runtime for ${config.stack_name_base}`,
    })

    this.runtimeArn = this.agentRuntime.agentRuntimeArn

    new cdk.CfnOutput(this, "AgentRuntimeId", {
      description: "ID of the created agent runtime",
      value: this.agentRuntime.agentRuntimeId,
    })

    new cdk.CfnOutput(this, "AgentRuntimeArn", {
      description: "ARN of the created agent runtime",
      value: this.agentRuntime.agentRuntimeArn,
      exportName: `${config.stack_name_base}-AgentRuntimeArn`,
    })

    new cdk.CfnOutput(this, "AgentRoleArn", {
      description: "ARN of the agent execution role",
      value: agentRole.roleArn,
    })

    new cdk.CfnOutput(this, "MemoryArn", {
      description: "ARN of the agent memory resource",
      value: this.memoryArn,
    })
  }

  /**
   * Wires the agent to the AgentCore Gateway: the credential-provider name the runtime's
   * @requires_access_token flow looks up, plus the OAuth2 / Secrets Manager permissions to
   * fetch gateway access tokens at runtime.
   *
   * This is the ONLY agent->gateway dependency. To run the agent without a gateway,
   * remove the single call to this method (and the GatewayConstruct from the stack);
   * nothing else in the agent references the gateway.
   */
  private addGatewayIntegration(
    config: AppConfig,
    credentialProviderName: string,
    agentRole: AgentCoreRole,
    envVars: { [key: string]: string }
  ): void {
    // Name used by @requires_access_token to look up the gateway credential provider.
    envVars["GATEWAY_CREDENTIAL_PROVIDER_NAME"] = credentialProviderName

    agentRole.addToPolicy(
      new iam.PolicyStatement({
        sid: "OAuth2CredentialProviderAccess",
        effect: iam.Effect.ALLOW,
        actions: [
          "bedrock-agentcore:GetOauth2CredentialProvider",
          "bedrock-agentcore:GetResourceOauth2Token",
        ],
        resources: [
          `arn:aws:bedrock-agentcore:${this.region}:${this.account}:oauth2-credential-provider/*`,
          `arn:aws:bedrock-agentcore:${this.region}:${this.account}:token-vault/*`,
          `arn:aws:bedrock-agentcore:${this.region}:${this.account}:workload-identity-directory/*`,
        ],
      })
    )

    agentRole.addToPolicy(
      new iam.PolicyStatement({
        sid: "SecretsManagerOAuth2Access",
        effect: iam.Effect.ALLOW,
        actions: ["secretsmanager:GetSecretValue"],
        resources: [
          `arn:aws:secretsmanager:${this.region}:${this.account}:secret:/${config.stack_name_base}/machine_client_secret*`,
          `arn:aws:secretsmanager:${this.region}:${this.account}:secret:bedrock-agentcore-identity!default/oauth2/${credentialProviderName}*`,
        ],
      })
    )
  }

  private buildNetworkConfiguration(config: AppConfig): agentcore.RuntimeNetworkConfiguration {
    if (config.backend.network_mode === "VPC") {
      const vpcConfig = config.backend.vpc
      if (!vpcConfig) {
        throw new Error("backend.vpc configuration is required when network_mode is 'VPC'.")
      }

      const vpc = ec2.Vpc.fromLookup(this, "ImportedVpc", {
        vpcId: vpcConfig.vpc_id,
      })

      const subnets: ec2.ISubnet[] = vpcConfig.subnet_ids.map((subnetId: string, index: number) =>
        ec2.Subnet.fromSubnetId(this, `ImportedSubnet${index}`, subnetId)
      )

      const securityGroups =
        vpcConfig.security_group_ids && vpcConfig.security_group_ids.length > 0
          ? vpcConfig.security_group_ids.map((sgId: string, index: number) =>
              ec2.SecurityGroup.fromSecurityGroupId(this, `ImportedSG${index}`, sgId)
            )
          : undefined

      const vpcConfigProps: agentcore.VpcConfigProps = {
        vpc: vpc,
        vpcSubnets: {
          subnets: subnets,
        },
        securityGroups: securityGroups,
      }

      return agentcore.RuntimeNetworkConfiguration.usingVpc(this, vpcConfigProps)
    }

    return agentcore.RuntimeNetworkConfiguration.usingPublicNetwork()
  }
}
