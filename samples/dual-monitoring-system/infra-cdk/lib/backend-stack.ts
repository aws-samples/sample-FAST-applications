import * as cdk from "aws-cdk-lib"
import * as cognito from "aws-cdk-lib/aws-cognito"
import * as iam from "aws-cdk-lib/aws-iam"
import * as ssm from "aws-cdk-lib/aws-ssm"
import * as secretsmanager from "aws-cdk-lib/aws-secretsmanager"
import * as dynamodb from "aws-cdk-lib/aws-dynamodb"
import * as apigateway from "aws-cdk-lib/aws-apigateway"
import * as logs from "aws-cdk-lib/aws-logs"
import * as s3 from "aws-cdk-lib/aws-s3"
import * as wafv2 from "aws-cdk-lib/aws-wafv2"
import * as agentcore from "@aws-cdk/aws-bedrock-agentcore-alpha"
import { PythonFunction } from "@aws-cdk/aws-lambda-python-alpha"
import * as lambda from "aws-cdk-lib/aws-lambda"
import * as ecr_assets from "aws-cdk-lib/aws-ecr-assets"
import * as cr from "aws-cdk-lib/custom-resources"
import { Construct } from "constructs"
import { AppConfig } from "./utils/config-manager"
import { AgentCoreRole } from "./utils/agentcore-role"
import * as path from "path"
import * as fs from "fs"

export interface BackendStackProps extends cdk.NestedStackProps {
  config: AppConfig
  userPoolId: string
  userPoolClientId: string
  userPoolDomain: cognito.UserPoolDomain
  frontendUrl: string
  evaluationLambda?: lambda.IFunction
}

export class BackendStack extends cdk.NestedStack {
  public readonly userPoolId: string
  public readonly userPoolClientId: string
  public readonly userPoolDomain: cognito.UserPoolDomain
  public feedbackApiUrl: string
  public devopsIncidentApiUrl: string
  public apiGateway: apigateway.RestApi
  public apiAuthorizer: apigateway.CognitoUserPoolsAuthorizer
  public runtimeArn: string
  public memoryArn: string
  private agentName: cdk.CfnParameter
  private networkMode: cdk.CfnParameter
  private userPool: cognito.IUserPool
  private machineClient: cognito.UserPoolClient
  private agentRuntime: agentcore.Runtime

  constructor(scope: Construct, id: string, props: BackendStackProps) {
    super(scope, id, props)

    // Store the Cognito values
    this.userPoolId = props.userPoolId
    this.userPoolClientId = props.userPoolClientId
    this.userPoolDomain = props.userPoolDomain

    // Import the Cognito resources from the other stack
    this.userPool = cognito.UserPool.fromUserPoolId(
      this,
      "ImportedUserPoolForBackend",
      props.userPoolId
    )
    // then create the user pool client
    cognito.UserPoolClient.fromUserPoolClientId(
      this,
      "ImportedUserPoolClient",
      props.userPoolClientId
    )

    // Create Machine-to-Machine authentication components
    this.createMachineAuthentication(props.config)

    // DEPLOYMENT ORDER EXPLANATION:
    // 1. Cognito User Pool & Client (created in separate CognitoStack)
    // 2. Machine Client & Resource Server (created above for M2M auth)
    // 3. AgentCore Runtime (created next)
    //
    // This order ensures that authentication components are available before
    // the runtime that depends on them.

    // Create AgentCore Runtime resources
    this.createAgentCoreRuntime(props.config)

    // Store runtime ARN in SSM for frontend stack
    this.createRuntimeSSMParameters(props.config)

    // Store Cognito configuration in SSM for testing and frontend
    this.createCognitoSSMParameters(props.config)

    // Create Feedback DynamoDB table (example of application data storage)
    const feedbackTable = this.createFeedbackTable(props.config)

    // Create API Gateway Feedback API resources (example of best-practice API Gateway + Lambda
    // pattern)
    this.createFeedbackApi(props.config, props.frontendUrl, feedbackTable)
    
    // Add evaluation routes if evaluation lambda is provided
    if (props.evaluationLambda) {
      this.addEvaluationRoutes(props.config, props.evaluationLambda)
    }

    // Add DevOps Agent routes
    this.addDevOpsAgentRoutes(props.config, props.frontendUrl)
  }

  private createAgentCoreRuntime(config: AppConfig): void {
    const pattern = config.backend?.pattern || "strands-single-agent"

    // Parameters
    this.agentName = new cdk.CfnParameter(this, "AgentName", {
      type: "String",
      default: "StrandsAgent",
      description: "Name for the agent runtime",
    })

    this.networkMode = new cdk.CfnParameter(this, "NetworkMode", {
      type: "String",
      default: "PUBLIC",
      description: "Network mode for AgentCore resources",
      allowedValues: ["PUBLIC", "PRIVATE"],
    })

    const stack = cdk.Stack.of(this)
    const deploymentType = config.backend.deployment_type

    // Create the agent runtime artifact based on deployment type
    let agentRuntimeArtifact: agentcore.AgentRuntimeArtifact
    let zipPackagerResource: cdk.CustomResource | undefined

    if (deploymentType === "zip") {
      // ZIP DEPLOYMENT: Use Lambda to package and upload to S3 (no Docker required)
      const repoRoot = path.resolve(__dirname, "..", "..")
      const patternDir = path.join(repoRoot, "patterns", pattern) // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal

      // Create S3 bucket for agent code
      const accessLogBucket = new s3.Bucket(this, "AgentCodeAccessLogBucket", {
        removalPolicy: cdk.RemovalPolicy.DESTROY,
        autoDeleteObjects: true,
        blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
        enforceSSL: true,
        lifecycleRules: [{ expiration: cdk.Duration.days(90) }],
      })

      const agentCodeBucket = new s3.Bucket(this, "AgentCodeBucket", {
        removalPolicy: cdk.RemovalPolicy.DESTROY,
        autoDeleteObjects: true,
        versioned: true,
        blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
        serverAccessLogsBucket: accessLogBucket,
        serverAccessLogsPrefix: "agent-code-access-logs/",
        lifecycleRules: [
          {
            noncurrentVersionExpiration: cdk.Duration.days(30),
            abortIncompleteMultipartUploadAfter: cdk.Duration.days(7),
          },
        ],
      })

      // Lambda to package agent code
      const packagerLambda = new lambda.Function(this, "ZipPackagerLambda", {
        runtime: lambda.Runtime.PYTHON_3_12,
        handler: "index.handler",
        code: lambda.Code.fromAsset(path.join(__dirname, "..", "lambdas", "zip-packager")),
        timeout: cdk.Duration.minutes(10),
        memorySize: 1024,
        ephemeralStorageSize: cdk.Size.gibibytes(2),
        tracing: lambda.Tracing.ACTIVE,
      })

      agentCodeBucket.grantReadWrite(packagerLambda)

      // Read agent code files and encode as base64
      const agentCode: Record<string, string> = {}
      
      // Read pattern .py files
      for (const file of fs.readdirSync(patternDir)) {
        if (file.endsWith(".py")) {
          const content = fs.readFileSync(path.join(patternDir, file)) // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal
          agentCode[file] = content.toString("base64")
        }
      }

      // Read shared modules (gateway/, tools/)
      for (const module of ["gateway", "tools"]) {
        const moduleDir = path.join(repoRoot, module)
        if (fs.existsSync(moduleDir)) {
          this.readDirRecursive(moduleDir, module, agentCode)
        }
      }

      // Read requirements
      const requirementsPath = path.join(patternDir, "requirements.txt") // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal
      const requirements = fs.readFileSync(requirementsPath, "utf-8")
        .split("\n")
        .map(line => line.trim())
        .filter(line => line && !line.startsWith("#"))

      // Create hash for change detection
      // We use this to trigger update when content changes
      const contentHash = this.hashContent(JSON.stringify({ requirements, agentCode }))

      // Custom Resource to trigger packaging
      const provider = new cr.Provider(this, "ZipPackagerProvider", {
        onEventHandler: packagerLambda,
      })

      // Enable X-Ray tracing on the Provider framework Lambda via escape hatch
      const frameworkFn = provider.node.findChild("framework-onEvent") as lambda.Function
      const cfnFrameworkFn = frameworkFn.node.defaultChild as lambda.CfnFunction
      cfnFrameworkFn.tracingConfig = { mode: "Active" }
      frameworkFn.role?.addManagedPolicy(
        iam.ManagedPolicy.fromAwsManagedPolicyName("AWSXrayDaemonWriteAccess")
      )

      zipPackagerResource = new cdk.CustomResource(this, "ZipPackager", {
        serviceToken: provider.serviceToken,
        properties: {
          BucketName: agentCodeBucket.bucketName,
          ObjectKey: "deployment_package.zip",
          Requirements: requirements,
          AgentCode: agentCode,
          ContentHash: contentHash,
        },
      })

      // Store bucket name in SSM for updates
      new ssm.StringParameter(this, "AgentCodeBucketNameParam", {
        parameterName: `/${config.stack_name_base}/agent-code-bucket`,
        stringValue: agentCodeBucket.bucketName,
        description: "S3 bucket for agent code deployment packages",
      })

      agentRuntimeArtifact = agentcore.AgentRuntimeArtifact.fromS3(
        {
          bucketName: agentCodeBucket.bucketName,
          objectKey: "deployment_package.zip",
        },
        agentcore.AgentCoreRuntime.PYTHON_3_12,
        ["opentelemetry-instrument", "basic_agent.py"]
      )
    } else {
      // DOCKER DEPLOYMENT: Use container-based deployment
      agentRuntimeArtifact = agentcore.AgentRuntimeArtifact.fromAsset(
        path.resolve(__dirname, "..", ".."),
        {
          platform: ecr_assets.Platform.LINUX_ARM64,
          file: `patterns/${pattern}/Dockerfile`,
        }
      )
    }

    // Configure network mode
    const networkConfiguration =
      this.networkMode.valueAsString === "PRIVATE"
        ? undefined // For private mode, you would need to configure VPC settings
        : agentcore.RuntimeNetworkConfiguration.usingPublicNetwork()

    // Configure JWT authorizer with Cognito
    const authorizerConfiguration = agentcore.RuntimeAuthorizerConfiguration.usingJWT(
      `https://cognito-idp.${stack.region}.amazonaws.com/${this.userPoolId}/.well-known/openid-configuration`,
      [this.userPoolClientId]
    )

    // Create AgentCore execution role
    const agentRole = new AgentCoreRole(this, "AgentCoreRole")

    // Create memory resource with short-term memory (conversation history) as default
    // To enable long-term strategies (summaries, preferences, facts), see docs/MEMORY_INTEGRATION.md
    const memory = new cdk.CfnResource(this, "AgentMemory", {
      type: "AWS::BedrockAgentCore::Memory",
      properties: {
        Name: cdk.Names.uniqueResourceName(this, { maxLength: 48 }),
        EventExpiryDuration: 30,
        Description: `Short-term memory for ${config.stack_name_base} agent`,
        MemoryStrategies: [], // Empty array = short-term only (conversation history)
        MemoryExecutionRoleArn: agentRole.roleArn,
        Tags: {
          Name: `${config.stack_name_base}_Memory`,
          ManagedBy: "CDK",
        },
      },
    })
    const memoryId = memory.getAtt("MemoryId").toString()
    const memoryArn = memory.getAtt("MemoryArn").toString()

    // Store the memory ARN for access from main stack
    this.memoryArn = memoryArn

    // Add memory-specific permissions to agent role
    agentRole.addToPolicy(
      new iam.PolicyStatement({
        sid: "MemoryResourceAccess",
        effect: iam.Effect.ALLOW,
        actions: [
          "bedrock-agentcore:CreateEvent",
          "bedrock-agentcore:GetEvent",
          "bedrock-agentcore:ListEvents",
          "bedrock-agentcore:RetrieveMemoryRecords", // Only needed for long-term strategies
        ],
        resources: [memoryArn],
      })
    )

    // Add SSM permissions for Gateway URL lookup
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

    // Add Code Interpreter permissions
    agentRole.addToPolicy(
      new iam.PolicyStatement({
        sid: "CodeInterpreterAccess",
        effect: iam.Effect.ALLOW,
        actions: [
          "bedrock-agentcore:StartCodeInterpreterSession",
          "bedrock-agentcore:StopCodeInterpreterSession",
          "bedrock-agentcore:InvokeCodeInterpreter",
        ],
        resources: [`arn:aws:bedrock-agentcore:${this.region}:aws:code-interpreter/*`],
      })
    )

    // Environment variables for the runtime
    const envVars: { [key: string]: string } = {
      AWS_REGION: stack.region,
      AWS_DEFAULT_REGION: stack.region,
      MEMORY_ID: memoryId,
      STACK_NAME: config.stack_name_base, // Required for agent to find SSM parameters
    }

    // Create CloudWatch Log Groups for AgentCore Runtime
    // These log groups are required for application logs and usage logs
    const runtimeName = `${config.stack_name_base.replace(/-/g, "_")}_${this.agentName.valueAsString}`
    
    // Application logs - captures agent execution logs
    const applicationLogGroup = new logs.LogGroup(this, "RuntimeApplicationLogs", {
      logGroupName: `/aws/vendedlogs/bedrock-agentcore/runtime/APPLICATION_LOGS/${runtimeName}`,
      retention: logs.RetentionDays.ONE_WEEK,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    })

    // Usage logs - captures runtime usage metrics
    const usageLogGroup = new logs.LogGroup(this, "RuntimeUsageLogs", {
      logGroupName: `/aws/vendedlogs/bedrock-agentcore/runtime/USAGE_LOGS/${runtimeName}`,
      retention: logs.RetentionDays.ONE_WEEK,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    })

    // Runtime logs - captures runtime system logs
    const runtimeLogGroup = new logs.LogGroup(this, "RuntimeLogs", {
      logGroupName: `/aws/vendedlogs/bedrock-agentcore/runtime/RUNTIME_LOGS/${runtimeName}`,
      retention: logs.RetentionDays.ONE_WEEK,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    })

    // OpenTelemetry logs - captures OTEL traces and spans
    const otelLogGroup = new logs.LogGroup(this, "RuntimeOtelLogs", {
      logGroupName: `/aws/vendedlogs/bedrock-agentcore/runtime/OTEL_LOGS/${runtimeName}`,
      retention: logs.RetentionDays.ONE_WEEK,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    })

    // Grant agent role permissions to write to log groups
    applicationLogGroup.grantWrite(agentRole)
    usageLogGroup.grantWrite(agentRole)
    runtimeLogGroup.grantWrite(agentRole)
    otelLogGroup.grantWrite(agentRole)

    // Create the runtime using L2 construct
    this.agentRuntime = new agentcore.Runtime(this, "Runtime", {
      runtimeName: runtimeName,
      agentRuntimeArtifact: agentRuntimeArtifact,
      executionRole: agentRole,
      networkConfiguration: networkConfiguration,
      protocolConfiguration: agentcore.ProtocolType.HTTP,
      environmentVariables: envVars,
      authorizerConfiguration: authorizerConfiguration,
      description: `${pattern} agent runtime for ${config.stack_name_base}`,
    })

    // Ensure log groups are created before runtime
    this.agentRuntime.node.addDependency(applicationLogGroup)
    this.agentRuntime.node.addDependency(usageLogGroup)
    this.agentRuntime.node.addDependency(runtimeLogGroup)
    this.agentRuntime.node.addDependency(otelLogGroup)

    // Make sure that ZIP is uploaded before Runtime is created
    if (zipPackagerResource) {
      this.agentRuntime.node.addDependency(zipPackagerResource)
    }

    // Store the runtime ARN
    this.runtimeArn = this.agentRuntime.agentRuntimeArn

    // Outputs
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

    // Memory ARN output
    new cdk.CfnOutput(this, "MemoryArn", {
      description: "ARN of the agent memory resource",
      value: memoryArn,
    })

    // Log group outputs
    new cdk.CfnOutput(this, "ApplicationLogGroup", {
      description: "CloudWatch Log Group for agent application logs",
      value: applicationLogGroup.logGroupName,
    })

    new cdk.CfnOutput(this, "UsageLogGroup", {
      description: "CloudWatch Log Group for agent usage logs",
      value: usageLogGroup.logGroupName,
    })

    new cdk.CfnOutput(this, "RuntimeLogGroup", {
      description: "CloudWatch Log Group for runtime system logs",
      value: runtimeLogGroup.logGroupName,
    })

    new cdk.CfnOutput(this, "OtelLogGroup", {
      description: "CloudWatch Log Group for OpenTelemetry traces and spans",
      value: otelLogGroup.logGroupName,
    })

    // Enable X-Ray tracing on CDK-managed singleton Lambda (AWS679f53fa)
    // This Lambda is created by autoDeleteObjects on S3 buckets
    const awsSdkLambda = this.node.tryFindChild("AWS679f53fac002430cb0da5b7982bd2287") as lambda.Function | undefined
    if (awsSdkLambda) {
      const cfnAwsSdkFn = awsSdkLambda.node.defaultChild as lambda.CfnFunction
      cfnAwsSdkFn.tracingConfig = { mode: "Active" }
      awsSdkLambda.role?.addManagedPolicy(
        iam.ManagedPolicy.fromAwsManagedPolicyName("AWSXrayDaemonWriteAccess")
      )
    }
  }

  private createRuntimeSSMParameters(config: AppConfig): void {
    // Store runtime ARN in SSM for frontend stack
    new ssm.StringParameter(this, "RuntimeArnParam", {
      parameterName: `/${config.stack_name_base}/runtime-arn`,
      stringValue: this.runtimeArn,
    })
  }

  private createCognitoSSMParameters(config: AppConfig): void {
    // Store Cognito configuration in SSM for testing and frontend access
    new ssm.StringParameter(this, "CognitoUserPoolIdParam", {
      parameterName: `/${config.stack_name_base}/cognito-user-pool-id`,
      stringValue: this.userPoolId,
      description: "Cognito User Pool ID",
    })

    new ssm.StringParameter(this, "CognitoUserPoolClientIdParam", {
      parameterName: `/${config.stack_name_base}/cognito-user-pool-client-id`,
      stringValue: this.userPoolClientId,
      description: "Cognito User Pool Client ID",
    })

    new ssm.StringParameter(this, "MachineClientIdParam", {
      parameterName: `/${config.stack_name_base}/machine_client_id`,
      stringValue: this.machineClient.userPoolClientId,
      description: "Machine Client ID for M2M authentication",
    })

    new secretsmanager.Secret(this, "MachineClientSecret", {
      secretName: `/${config.stack_name_base}/machine_client_secret`,
      secretStringValue: cdk.SecretValue.unsafePlainText(this.machineClient.userPoolClientSecret.unsafeUnwrap()),
      description: "Machine Client Secret for M2M authentication",
    })

    // Use the correct Cognito domain format from the passed domain
    new ssm.StringParameter(this, "CognitoDomainParam", {
      parameterName: `/${config.stack_name_base}/cognito_provider`,
      stringValue: `${this.userPoolDomain.domainName}.auth.${cdk.Aws.REGION}.amazoncognito.com`,
      description: "Cognito domain URL for token endpoint",
    })
  }

  // Creates a DynamoDB table for storing user feedback.
  private createFeedbackTable(config: AppConfig): dynamodb.Table {
    const feedbackTable = new dynamodb.Table(this, "FeedbackTable", {
      tableName: `${config.stack_name_base}-feedback`,
      partitionKey: {
        name: "feedbackId",
        type: dynamodb.AttributeType.STRING,
      },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      pointInTimeRecoverySpecification: {
        pointInTimeRecoveryEnabled: true,
      },
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
    })

    // Add GSI for querying by feedbackType with timestamp sorting
    feedbackTable.addGlobalSecondaryIndex({
      indexName: "feedbackType-timestamp-index",
      partitionKey: {
        name: "feedbackType",
        type: dynamodb.AttributeType.STRING,
      },
      sortKey: {
        name: "timestamp",
        type: dynamodb.AttributeType.NUMBER,
      },
      projectionType: dynamodb.ProjectionType.ALL,
    })

    return feedbackTable
  }

  /**
   * Creates an API Gateway with Lambda integration for the feedback endpoint.
   * This is an EXAMPLE implementation demonstrating best practices for API Gateway + Lambda.
   *
   * API Contract - POST /feedback
   * Authorization: Bearer <cognito-access-token> (required)
   *
   * Request Body:
   *   sessionId: string (required, max 100 chars, alphanumeric with -_) - Conversation session ID
   *   message: string (required, max 5000 chars) - Agent's response being rated
   *   feedbackType: "positive" | "negative" (required) - User's rating
   *   comment: string (optional, max 5000 chars) - User's explanation for rating
   *
   * Success Response (200):
   *   { success: true, feedbackId: string }
   *
   * Error Responses:
   *   400: { error: string } - Validation failure (missing fields, invalid format)
   *   401: { error: "Unauthorized" } - Invalid/missing JWT token
   *   500: { error: "Internal server error" } - DynamoDB or processing error
   *
   * Implementation: infra-cdk/lambdas/feedback/index.py
   */
  private createFeedbackApi(
    config: AppConfig,
    frontendUrl: string,
    feedbackTable: dynamodb.Table
  ): void {
    // Create Lambda function for feedback using Python
    const feedbackLambda = new PythonFunction(this, "FeedbackLambda", {
      functionName: `${config.stack_name_base}-feedback`,
      runtime: lambda.Runtime.PYTHON_3_13,
      entry: path.join(__dirname, "..", "lambdas", "feedback"),
      handler: "handler",
      environment: {
        TABLE_NAME: feedbackTable.tableName,
        CORS_ALLOWED_ORIGINS: `${frontendUrl},http://localhost:3000`,
      },
      timeout: cdk.Duration.seconds(30),
      tracing: lambda.Tracing.ACTIVE,
      layers: [
        lambda.LayerVersion.fromLayerVersionArn(
          this,
          "PowertoolsLayer",
          `arn:aws:lambda:${
            cdk.Stack.of(this).region
          }:017000801446:layer:AWSLambdaPowertoolsPythonV3-python313-arm64:18`
        ),
      ],
      logGroup: new logs.LogGroup(this, "FeedbackLambdaLogGroup", {
        logGroupName: `/aws/lambda/${config.stack_name_base}-feedback`,
        retention: logs.RetentionDays.ONE_WEEK,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      }),
    })

    // Grant Lambda permissions to write to DynamoDB
    feedbackTable.grantWriteData(feedbackLambda)

    // Create API Gateway
    this.apiGateway = new apigateway.RestApi(this, "FeedbackApi", {
      restApiName: `${config.stack_name_base}-api`,
      description: "API for user feedback, evaluation, and future endpoints",
      defaultCorsPreflightOptions: {
        allowOrigins: [frontendUrl, "http://localhost:3000"],
        allowMethods: ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allowHeaders: ["Content-Type", "Authorization"],
        allowCredentials: true,
      },
      deployOptions: {
        stageName: "prod",
        throttlingRateLimit: 100,
        throttlingBurstLimit: 200,
        cachingEnabled: false,
        cacheClusterEnabled: false,
        loggingLevel: apigateway.MethodLoggingLevel.INFO,
        dataTraceEnabled: true,
        metricsEnabled: true,
        accessLogDestination: new apigateway.LogGroupLogDestination(
          new logs.LogGroup(this, "FeedbackApiAccessLogGroup", {
            logGroupName: `/aws/apigateway/${config.stack_name_base}-api-access`,
            retention: logs.RetentionDays.ONE_WEEK,
            removalPolicy: cdk.RemovalPolicy.DESTROY,
          })
        ),
        accessLogFormat: apigateway.AccessLogFormat.jsonWithStandardFields(),
        tracingEnabled: true,
      },
    })

    // Store the API URL
    this.feedbackApiUrl = this.apiGateway.url

    // Create Cognito authorizer
    this.apiAuthorizer = new apigateway.CognitoUserPoolsAuthorizer(this, "FeedbackApiAuthorizer", {
      cognitoUserPools: [this.userPool],
      identitySource: "method.request.header.Authorization",
      authorizerName: `${config.stack_name_base}-authorizer`,
    })

    // Add request validator for API security
    const requestValidator = new apigateway.RequestValidator(this, "FeedbackApiRequestValidator", {
      restApi: this.apiGateway,
      requestValidatorName: `${config.stack_name_base}-feedback-request-validator`,
      validateRequestBody: true,
      validateRequestParameters: true,
    })

    // Create /feedback resource and POST method
    const feedbackResource = this.apiGateway.root.addResource("feedback")
    feedbackResource.addMethod("POST", new apigateway.LambdaIntegration(feedbackLambda), {
      authorizer: this.apiAuthorizer,
      authorizationType: apigateway.AuthorizationType.COGNITO,
      requestValidator: requestValidator,
    })

    // Store API URL in SSM for frontend
    new ssm.StringParameter(this, "FeedbackApiUrlParam", {
      parameterName: `/${config.stack_name_base}/feedback-api-url`,
      stringValue: this.apiGateway.url,
      description: "Feedback API Gateway URL",
    })

    // WAF WebACL for API Gateway protection (Issue #14)
    const webAcl = new wafv2.CfnWebACL(this, "ApiWafWebAcl", {
      defaultAction: { allow: {} },
      scope: "REGIONAL",
      visibilityConfig: {
        cloudWatchMetricsEnabled: true,
        metricName: `${config.stack_name_base}-api-waf`,
        sampledRequestsEnabled: true,
      },
      rules: [
        {
          name: "AWSManagedRulesCommonRuleSet",
          priority: 1,
          overrideAction: { none: {} },
          statement: {
            managedRuleGroupStatement: {
              vendorName: "AWS",
              name: "AWSManagedRulesCommonRuleSet",
            },
          },
          visibilityConfig: {
            cloudWatchMetricsEnabled: true,
            metricName: `${config.stack_name_base}-common-rules`,
            sampledRequestsEnabled: true,
          },
        },
        {
          name: "AWSManagedRulesKnownBadInputsRuleSet",
          priority: 2,
          overrideAction: { none: {} },
          statement: {
            managedRuleGroupStatement: {
              vendorName: "AWS",
              name: "AWSManagedRulesKnownBadInputsRuleSet",
            },
          },
          visibilityConfig: {
            cloudWatchMetricsEnabled: true,
            metricName: `${config.stack_name_base}-bad-inputs`,
            sampledRequestsEnabled: true,
          },
        },
      ],
    })

    new wafv2.CfnWebACLAssociation(this, "ApiWafAssociation", {
      resourceArn: this.apiGateway.deploymentStage.stageArn,
      webAclArn: webAcl.attrArn,
    })
  }

  private addEvaluationRoutes(config: AppConfig, evaluationLambda: lambda.IFunction): void {
    // Add request validator
    const requestValidator = new apigateway.RequestValidator(
      this,
      "EvaluationApiRequestValidator",
      {
        restApi: this.apiGateway,
        requestValidatorName: `${config.stack_name_base}-evaluation-validator`,
        validateRequestBody: true,
        validateRequestParameters: true,
      }
    )

    // Lambda integration
    const lambdaIntegration = new apigateway.LambdaIntegration(evaluationLambda)

    // Create /evaluations resource
    const evaluationsResource = this.apiGateway.root.addResource("evaluations")

    // GET /evaluations/sessions - List sessions with filtering
    const sessionsResource = evaluationsResource.addResource("sessions")
    sessionsResource.addMethod("GET", lambdaIntegration, {
      authorizer: this.apiAuthorizer,
      authorizationType: apigateway.AuthorizationType.COGNITO,
      requestValidator: requestValidator,
      requestParameters: {
        "method.request.querystring.start_date": false,
        "method.request.querystring.end_date": false,
        "method.request.querystring.min_score": false,
        "method.request.querystring.max_score": false,
        "method.request.querystring.limit": false,
        "method.request.querystring.next_token": false,
      },
    })

    // GET /evaluations/sessions/{sessionId} - Get session detail
    const sessionDetailResource = sessionsResource.addResource("{sessionId}")
    sessionDetailResource.addMethod("GET", lambdaIntegration, {
      authorizer: this.apiAuthorizer,
      authorizationType: apigateway.AuthorizationType.COGNITO,
      requestValidator: requestValidator,
      requestParameters: {
        "method.request.path.sessionId": true,
      },
    })

    // POST /evaluations/analyze - Trigger AI analysis
    const analyzeResource = evaluationsResource.addResource("analyze")
    analyzeResource.addMethod("POST", lambdaIntegration, {
      authorizer: this.apiAuthorizer,
      authorizationType: apigateway.AuthorizationType.COGNITO,
      requestValidator: requestValidator,
    })

    // GET /evaluations/analyze/{jobId} - Get analysis job status
    const analyzeJobResource = analyzeResource.addResource("{jobId}")
    analyzeJobResource.addMethod("GET", lambdaIntegration, {
      authorizer: this.apiAuthorizer,
      authorizationType: apigateway.AuthorizationType.COGNITO,
      requestValidator: requestValidator,
      requestParameters: {
        "method.request.path.jobId": true,
      },
    })

    // POST /evaluations/improve-prompt - Generate prompt improvements
    const improvePromptResource = evaluationsResource.addResource("improve-prompt")
    improvePromptResource.addMethod("POST", lambdaIntegration, {
      authorizer: this.apiAuthorizer,
      authorizationType: apigateway.AuthorizationType.COGNITO,
      requestValidator: requestValidator,
    })

    // GET /evaluations/improve-prompt/status/{jobId} - Get prompt improvement job status
    const improvePromptStatusResource = improvePromptResource.addResource("status")
    const improvePromptJobResource = improvePromptStatusResource.addResource("{jobId}")
    improvePromptJobResource.addMethod("GET", lambdaIntegration, {
      authorizer: this.apiAuthorizer,
      authorizationType: apigateway.AuthorizationType.COGNITO,
      requestValidator: requestValidator,
      requestParameters: {
        "method.request.path.jobId": true,
      },
    })

    // POST /evaluations/setup - Setup AgentCore online evaluation
    const setupResource = evaluationsResource.addResource("setup")
    setupResource.addMethod("POST", lambdaIntegration, {
      authorizer: this.apiAuthorizer,
      authorizationType: apigateway.AuthorizationType.COGNITO,
      requestValidator: requestValidator,
    })

    // GET /evaluations/metrics - Get AgentCore evaluation metrics
    const metricsResource = evaluationsResource.addResource("metrics")
    metricsResource.addMethod("GET", lambdaIntegration, {
      authorizer: this.apiAuthorizer,
      authorizationType: apigateway.AuthorizationType.COGNITO,
      requestValidator: requestValidator,
      requestParameters: {
        "method.request.querystring.config_id": true,
        "method.request.querystring.start_date": false,
        "method.request.querystring.end_date": false,
      },
    })

    // GET /evaluations/configs - List online evaluation configurations
    const configsResource = evaluationsResource.addResource("configs")
    configsResource.addMethod("GET", lambdaIntegration, {
      authorizer: this.apiAuthorizer,
      authorizationType: apigateway.AuthorizationType.COGNITO,
      requestValidator: requestValidator,
    })

    // GET /evaluations/configs/{configId} - Get specific configuration
    const configDetailResource = configsResource.addResource("{configId}")
    configDetailResource.addMethod("GET", lambdaIntegration, {
      authorizer: this.apiAuthorizer,
      authorizationType: apigateway.AuthorizationType.COGNITO,
      requestValidator: requestValidator,
      requestParameters: {
        "method.request.path.configId": true,
      },
    })

    // PUT /evaluations/configs/{configId} - Update configuration
    configDetailResource.addMethod("PUT", lambdaIntegration, {
      authorizer: this.apiAuthorizer,
      authorizationType: apigateway.AuthorizationType.COGNITO,
      requestValidator: requestValidator,
      requestParameters: {
        "method.request.path.configId": true,
      },
    })

    // DELETE /evaluations/configs/{configId} - Delete configuration
    configDetailResource.addMethod("DELETE", lambdaIntegration, {
      authorizer: this.apiAuthorizer,
      authorizationType: apigateway.AuthorizationType.COGNITO,
      requestValidator: requestValidator,
      requestParameters: {
        "method.request.path.configId": true,
      },
    })

    // GET /evaluations/evaluators - List all evaluators
    const evaluatorsResource = evaluationsResource.addResource("evaluators")
    evaluatorsResource.addMethod("GET", lambdaIntegration, {
      authorizer: this.apiAuthorizer,
      authorizationType: apigateway.AuthorizationType.COGNITO,
      requestValidator: requestValidator,
    })

    // POST /evaluations/evaluators/custom - Create custom evaluator
    const customEvaluatorResource = evaluatorsResource.addResource("custom")
    customEvaluatorResource.addMethod("POST", lambdaIntegration, {
      authorizer: this.apiAuthorizer,
      authorizationType: apigateway.AuthorizationType.COGNITO,
      requestValidator: requestValidator,
    })

    // POST /evaluations/evaluate - On-demand evaluation
    const evaluateResource = evaluationsResource.addResource("evaluate")
    evaluateResource.addMethod("POST", lambdaIntegration, {
      authorizer: this.apiAuthorizer,
      authorizationType: apigateway.AuthorizationType.COGNITO,
      requestValidator: requestValidator,
    })

    // POST /evaluations/evaluate-batch - Batch evaluation
    const evaluateBatchResource = evaluationsResource.addResource("evaluate-batch")
    evaluateBatchResource.addMethod("POST", lambdaIntegration, {
      authorizer: this.apiAuthorizer,
      authorizationType: apigateway.AuthorizationType.COGNITO,
      requestValidator: requestValidator,
    })
  }

  // Gateway removed — gateway/ asset directory not present in this repo.
  // If you need AgentCore Gateway, see docs/GATEWAY.md for setup instructions.

  private createMachineAuthentication(config: AppConfig): void {
    // Create Resource Server for Machine-to-Machine (M2M) authentication
    // This defines the API scopes that machine clients can request access to
    const resourceServer = new cognito.UserPoolResourceServer(this, "ResourceServer", {
      userPool: this.userPool,
      identifier: `${config.stack_name_base}-gateway`,
      userPoolResourceServerName: `${config.stack_name_base}-gateway-resource-server`,
      scopes: [
        new cognito.ResourceServerScope({
          scopeName: "read",
          scopeDescription: "Read access to gateway",
        }),
        new cognito.ResourceServerScope({
          scopeName: "write",
          scopeDescription: "Write access to gateway",
        }),
      ],
    })

    // Create Machine Client for AgentCore Gateway authentication
    //
    // WHAT IS A MACHINE CLIENT?
    // A machine client is a Cognito User Pool Client configured for server-to-server authentication
    // using the OAuth2 Client Credentials flow. Unlike user-facing clients, it doesn't require
    // human interaction or user credentials.
    //
    // HOW IS IT DIFFERENT FROM THE REGULAR USER POOL CLIENT?
    // - Regular client: Uses Authorization Code flow for human users (frontend login)
    // - Machine client: Uses Client Credentials flow for service-to-service authentication
    // - Regular client: No client secret (public client for frontend security)
    // - Machine client: Has client secret (confidential client for backend security)
    // - Regular client: Scopes are openid, email, profile (user identity)
    // - Machine client: Scopes are custom resource server scopes (API permissions)
    //
    // WHY IS IT NEEDED?
    // The AgentCore Gateway needs to authenticate with Cognito to validate tokens and make
    // API calls on behalf of the system. The machine client provides the credentials for
    // this service-to-service authentication without requiring user interaction.
    this.machineClient = new cognito.UserPoolClient(this, "MachineClient", {
      userPool: this.userPool,
      userPoolClientName: `${config.stack_name_base}-machine-client`,
      generateSecret: true, // Required for client credentials flow
      oAuth: {
        flows: {
          clientCredentials: true, // Enable OAuth2 Client Credentials flow
        },
        scopes: [
          // Grant access to the resource server scopes defined above
          cognito.OAuthScope.resourceServer(
            resourceServer,
            new cognito.ResourceServerScope({
              scopeName: "read",
              scopeDescription: "Read access to gateway",
            })
          ),
          cognito.OAuthScope.resourceServer(
            resourceServer,
            new cognito.ResourceServerScope({
              scopeName: "write",
              scopeDescription: "Write access to gateway",
            })
          ),
        ],
      },
    })

    // Machine client must be created after resource server
    this.machineClient.node.addDependency(resourceServer)
  }

  /**
   * Recursively read directory contents and encode as base64.
   *
   * @param dirPath - Directory to read.
   * @param prefix - Prefix for file paths in output.
   * @param output - Output object to populate.
   */
  private readDirRecursive(dirPath: string, prefix: string, output: Record<string, string>): void {
    for (const entry of fs.readdirSync(dirPath, { withFileTypes: true })) {
      const fullPath = path.join(dirPath, entry.name) // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal
      const relativePath = path.join(prefix, entry.name) // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal

      if (entry.isDirectory()) {
        // Skip __pycache__ directories
        if (entry.name !== "__pycache__") {
          this.readDirRecursive(fullPath, relativePath, output)
        }
      } else if (entry.isFile()) {
        const content = fs.readFileSync(fullPath)
        output[relativePath] = content.toString("base64")
      }
    }
  }

  /**
   * Create a hash of content for change detection.
   *
   * @param content - Content to hash.
   * @returns Hash string.
   */
  private addDevOpsAgentRoutes(config: AppConfig, frontendUrl: string): void {
    // Create DevOps Agent proxy Lambda
    const devopsLambda = new lambda.Function(this, "DevOpsAgentLambda", {
      functionName: `${config.stack_name_base}-devops-agent`,
      runtime: lambda.Runtime.PYTHON_3_13,
      handler: "index.handler",
      code: lambda.Code.fromAsset(path.join(__dirname, "..", "lambdas", "devops-agent")),
      timeout: cdk.Duration.seconds(60),
      tracing: lambda.Tracing.ACTIVE,
      environment: {
        AGENT_SPACE_ID: "", // Set after DevOps Agent Space is deployed
        WEBHOOK_URL_PARAM: `/${config.stack_name_base}/devops-agent/webhook-url`,
        WEBHOOK_SECRET_ARN: `/${config.stack_name_base}/devops-agent/webhook-secret`,
        CORS_ALLOWED_ORIGIN: frontendUrl,
      },
      logGroup: new logs.LogGroup(this, "DevOpsAgentLambdaLogGroup", {
        logGroupName: `/aws/lambda/${config.stack_name_base}-devops-agent`,
        retention: logs.RetentionDays.ONE_WEEK,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      }),
    })

    // Grant SSM read access for webhook URL
    devopsLambda.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ["ssm:GetParameter"],
        resources: [
          `arn:aws:ssm:${this.region}:${this.account}:parameter/${config.stack_name_base}/devops-agent/*`,
        ],
      })
    )

    // Grant Secrets Manager read access for webhook secret
    devopsLambda.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ["secretsmanager:GetSecretValue"],
        resources: [
          `arn:aws:secretsmanager:${this.region}:${this.account}:secret:/${config.stack_name_base}/devops-agent/*`,
        ],
      })
    )

    // Grant SigV4 access for DevOps Agent API
    devopsLambda.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ["execute-api:Invoke"],
        resources: [`arn:aws:execute-api:${this.region}:*:*`],
      })
    )

    // Grant DevOps Agent API access
    devopsLambda.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ["aidevops:GetInvestigation", "aidevops:ListInvestigations"],
        resources: ["*"],
      })
    )

    // Add /devops-agent resource to existing API Gateway
    const devopsResource = this.apiGateway.root.addResource("devops-agent")

    const lambdaIntegration = new apigateway.LambdaIntegration(devopsLambda)

    // POST /devops-agent/incident
    const incidentResource = devopsResource.addResource("incident")
    incidentResource.addMethod("POST", lambdaIntegration, {
      authorizer: this.apiAuthorizer,
      authorizationType: apigateway.AuthorizationType.COGNITO,
    })

    // GET /devops-agent/investigations
    const investigationsResource = devopsResource.addResource("investigations")
    investigationsResource.addMethod("GET", lambdaIntegration, {
      authorizer: this.apiAuthorizer,
      authorizationType: apigateway.AuthorizationType.COGNITO,
    })

    // Output the DevOps incident API URL
    this.devopsIncidentApiUrl = `${this.apiGateway.url}devops-agent/`

    new cdk.CfnOutput(this, "DevOpsIncidentApiUrl", {
      value: this.devopsIncidentApiUrl,
      description: "DevOps Agent incident API URL",
    })
  }

  private hashContent(content: string): string {
    const crypto = require("crypto")
    return crypto.createHash("sha256").update(content).digest("hex").slice(0, 16)
  }
}
