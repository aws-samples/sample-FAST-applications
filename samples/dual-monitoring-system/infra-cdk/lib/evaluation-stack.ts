import * as cdk from "aws-cdk-lib"
import * as cognito from "aws-cdk-lib/aws-cognito"
import * as iam from "aws-cdk-lib/aws-iam"
import * as apigateway from "aws-cdk-lib/aws-apigateway"
import * as logs from "aws-cdk-lib/aws-logs"
import * as dynamodb from "aws-cdk-lib/aws-dynamodb"
import { PythonFunction } from "@aws-cdk/aws-lambda-python-alpha"
import * as lambda from "aws-cdk-lib/aws-lambda"
import { Construct } from "constructs"
import { AppConfig } from "./utils/config-manager"
import * as path from "path"

export interface EvaluationStackProps extends cdk.NestedStackProps {
  config: AppConfig
  frontendUrl: string
}

export class EvaluationStack extends cdk.NestedStack {
  public readonly evaluationLambda: lambda.Function
  public readonly analysisJobsTable: dynamodb.Table

  constructor(scope: Construct, id: string, props: EvaluationStackProps) {
    super(scope, id, props)

    // Create DynamoDB table for analysis jobs
    this.analysisJobsTable = new dynamodb.Table(this, "AnalysisJobsTable", {
      tableName: `${props.config.stack_name_base}-evaluation-analysis-jobs`,
      partitionKey: { name: "jobId", type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      pointInTimeRecovery: true,
    })

    // Create Lambda function for evaluation API
    this.evaluationLambda = new PythonFunction(this, "EvaluationLambda", {
      functionName: `${props.config.stack_name_base}-evaluation`,
      runtime: lambda.Runtime.PYTHON_3_13,
      entry: path.join(__dirname, "..", "lambdas", "evaluation"),
      handler: "handler",
      environment: {
        CORS_ALLOWED_ORIGINS: `${props.frontendUrl},http://localhost:3000`,
        RUNTIME_ARN: cdk.Fn.importValue(`${props.config.stack_name_base}-AgentRuntimeArn`),
        ANALYSIS_JOBS_TABLE: this.analysisJobsTable.tableName,
      },
      timeout: cdk.Duration.minutes(5), // Longer timeout for AI analysis
      memorySize: 1024, // More memory for AI workloads
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
      logGroup: new logs.LogGroup(this, "EvaluationLambdaLogGroup", {
        logGroupName: `/aws/lambda/${props.config.stack_name_base}-evaluation`,
        retention: logs.RetentionDays.ONE_WEEK,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      }),
    })

    // Grant DynamoDB permissions for analysis jobs
    this.analysisJobsTable.grantReadWriteData(this.evaluationLambda)

    // Grant Lambda invoke permissions (for async invocation)
    // Use function name instead of ARN to avoid circular dependency
    this.evaluationLambda.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ["lambda:InvokeFunction"],
        resources: [
          `arn:aws:lambda:${cdk.Stack.of(this).region}:${cdk.Stack.of(this).account}:function:${props.config.stack_name_base}-evaluation`
        ],
      })
    )

    // Grant CloudWatch Logs read permissions
    this.evaluationLambda.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: [
          "logs:DescribeLogStreams",
          "logs:GetLogEvents",
          "logs:FilterLogEvents",
          "logs:StartQuery",
          "logs:GetQueryResults",
          "logs:StopQuery",
        ],
        resources: [
          `arn:aws:logs:${cdk.Stack.of(this).region}:${
            cdk.Stack.of(this).account
          }:log-group:/aws/vendedlogs/bedrock-agentcore/*`,
          `arn:aws:logs:${cdk.Stack.of(this).region}:${
            cdk.Stack.of(this).account
          }:log-group:/aws/bedrock-agentcore/*`,
          `arn:aws:logs:${cdk.Stack.of(this).region}:${
            cdk.Stack.of(this).account
          }:log-group:/aws/spans:*`,
          `arn:aws:logs:${cdk.Stack.of(this).region}:${
            cdk.Stack.of(this).account
          }:log-group:/aws/spans`,
          // Add aws/spans without leading slash (CloudWatch Transaction Search format)
          `arn:aws:logs:${cdk.Stack.of(this).region}:${
            cdk.Stack.of(this).account
          }:log-group:aws/spans:*`,
          `arn:aws:logs:${cdk.Stack.of(this).region}:${
            cdk.Stack.of(this).account
          }:log-group:aws/spans`,
        ],
      })
    )

    // Grant CloudWatch Logs list/describe permissions (account-level)
    // These actions don't support resource-level permissions
    this.evaluationLambda.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: [
          "logs:DescribeLogGroups",
        ],
        resources: ["*"],
      })
    )

    // Grant CloudWatch Logs index policy permissions (account-level)
    // Required for AgentCore online evaluation to manage log group index policies
    this.evaluationLambda.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: [
          "logs:PutIndexPolicy",
          "logs:GetIndexPolicy",
          "logs:DeleteIndexPolicy",
          "logs:DescribeIndexPolicies",
        ],
        resources: ["*"],
      })
    )

    // Grant Bedrock permissions for AI analysis
    this.evaluationLambda.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
        resources: [
          "arn:aws:bedrock:*::foundation-model/*",
          `arn:aws:bedrock:*:${cdk.Stack.of(this).account}:inference-profile/*`,
        ],
      })
    )

    // Grant AgentCore permissions
    this.evaluationLambda.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: [
          // Runtime access
          "bedrock-agentcore:GetAgentRuntime",
          // Evaluator management
          "bedrock-agentcore:CreateEvaluator",
          "bedrock-agentcore:ListEvaluators",
          "bedrock-agentcore:GetEvaluator",
          // Online evaluation config management
          "bedrock-agentcore:CreateOnlineEvaluationConfig",
          "bedrock-agentcore:ListOnlineEvaluationConfigs",
          "bedrock-agentcore:GetOnlineEvaluationConfig",
          "bedrock-agentcore:UpdateOnlineEvaluationConfig",
          "bedrock-agentcore:DeleteOnlineEvaluationConfig",
          // Evaluation execution
          "bedrock-agentcore:Evaluate",
        ],
        resources: ["*"],
      })
    )

    // Grant IAM permissions for SDK to auto-create execution roles
    this.evaluationLambda.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: [
          "iam:CreateRole",
          "iam:PutRolePolicy",
          "iam:GetRole",
          "iam:PassRole",
        ],
        resources: [
          `arn:aws:iam::${cdk.Stack.of(this).account}:role/AgentCoreEvalsSDK-*`,
        ],
      })
    )

    // Outputs
    new cdk.CfnOutput(this, "EvaluationLambdaArn", {
      description: "ARN of the Evaluation API Lambda function",
      value: this.evaluationLambda.functionArn,
    })

    new cdk.CfnOutput(this, "AnalysisJobsTableName", {
      description: "Name of the DynamoDB table for analysis jobs",
      value: this.analysisJobsTable.tableName,
    })

    new cdk.CfnOutput(this, "AnalysisJobsTableArn", {
      description: "ARN of the DynamoDB table for analysis jobs",
      value: this.analysisJobsTable.tableArn,
    })

    // CloudWatch alarms for Lambda functions
    // Suppressed: CloudWatch alarms are optional for this sample application.
    // Configure alarms based on your operational requirements.
  }
}
