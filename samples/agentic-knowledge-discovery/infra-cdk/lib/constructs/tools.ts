import * as cdk from "aws-cdk-lib"
import * as ec2 from "aws-cdk-lib/aws-ec2"
import * as iam from "aws-cdk-lib/aws-iam"
import * as rds from "aws-cdk-lib/aws-rds"
import * as lambda from "aws-cdk-lib/aws-lambda"
import * as ecr_assets from "aws-cdk-lib/aws-ecr-assets"
import * as logs from "aws-cdk-lib/aws-logs"
import { Construct } from "constructs"
import { AppConfig } from "../utils/config-manager"
import * as path from "path"

export interface ToolsConstructProps {
  config: AppConfig
  vpc: ec2.IVpc
  lambdaSecurityGroup: ec2.ISecurityGroup
  cluster: rds.DatabaseCluster
  databaseName: string
  /** Optional existing Knowledge Base id for doc_search (config passthrough). */
  knowledgeBaseId?: string
}

/** Describes a Gateway Lambda target for the GatewayConstruct to register. */
export interface GatewayToolTarget {
  name: string
  description: string
  lambdaFunction: lambda.IFunction
  toolSpecPath: string
}

/**
 * The two retrieval tool Lambdas exposed as AgentCore Gateway targets:
 * - structured_search: in-VPC container Lambda (psycopg2) for read-only SQL
 *   queries against Aurora.
 * - doc_search: standalone Lambda calling the managed Bedrock Retrieve API.
 * Exposes them as GatewayToolTarget entries for the GatewayConstruct.
 */
export class ToolsConstruct extends Construct {
  public readonly targets: GatewayToolTarget[]

  constructor(scope: Construct, id: string, props: ToolsConstructProps) {
    super(scope, id)

    const { config, vpc, lambdaSecurityGroup, cluster, databaseName, knowledgeBaseId } = props
    const toolsDir = path.join(__dirname, "../../../gateway/tools") // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal

    // structured_search: container image (psycopg2), inside the VPC to reach Aurora.
    const structuredFn = new lambda.DockerImageFunction(this, "StructuredSearchLambda", {
      functionName: `${config.stack_name_base}-structured-search`,
      code: lambda.DockerImageCode.fromImageAsset(path.join(toolsDir, "structured_search"), {
        platform: ecr_assets.Platform.LINUX_ARM64,
      }),
      architecture: lambda.Architecture.ARM_64,
      timeout: cdk.Duration.seconds(60),
      memorySize: 512,
      vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
      securityGroups: [lambdaSecurityGroup],
      environment: {
        DB_SECRET_ARN: cluster.secret!.secretArn,
        DB_CLUSTER_ENDPOINT: cluster.clusterEndpoint.hostname,
        DB_NAME: databaseName,
      },
      logGroup: new logs.LogGroup(this, "StructuredSearchLogGroup", {
        logGroupName: `/aws/lambda/${config.stack_name_base}-structured-search`,
        retention: logs.RetentionDays.ONE_WEEK,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      }),
    })
    cluster.secret!.grantRead(structuredFn)

    // doc_search: standalone (no VPC); calls the managed Bedrock Retrieve API.
    const docSearchFn = new lambda.Function(this, "DocSearchLambda", {
      functionName: `${config.stack_name_base}-doc-search`,
      runtime: lambda.Runtime.PYTHON_3_13,
      architecture: lambda.Architecture.ARM_64,
      handler: "doc_search_lambda.handler",
      code: lambda.Code.fromAsset(path.join(toolsDir, "doc_search")),
      timeout: cdk.Duration.seconds(60),
      memorySize: 256,
      environment: {
        KNOWLEDGE_BASE_ID: knowledgeBaseId ?? "",
      },
      logGroup: new logs.LogGroup(this, "DocSearchLogGroup", {
        logGroupName: `/aws/lambda/${config.stack_name_base}-doc-search`,
        retention: logs.RetentionDays.ONE_WEEK,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      }),
    })

    // doc_search needs Bedrock KB Retrieve. Scope to a specific KB when provided,
    // otherwise a wildcard so the tool still deploys before a KB is attached.
    const kbResource = knowledgeBaseId
      ? `arn:aws:bedrock:${cdk.Stack.of(this).region}:${cdk.Stack.of(this).account}:knowledge-base/${knowledgeBaseId}`
      : `arn:aws:bedrock:${cdk.Stack.of(this).region}:${cdk.Stack.of(this).account}:knowledge-base/*`
    docSearchFn.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ["bedrock:Retrieve"],
        resources: [kbResource],
      })
    )

    this.targets = [
      {
        name: "structured-search-target",
        description: "Structured document metadata (Aurora PostgreSQL) query tools",
        lambdaFunction: structuredFn,
        toolSpecPath: path.join(toolsDir, "structured_search/tool_spec.json"),
      },
      {
        name: "doc-search-target",
        description: "Unstructured document search over a Bedrock Knowledge Base",
        lambdaFunction: docSearchFn,
        toolSpecPath: path.join(toolsDir, "doc_search/tool_spec.json"),
      },
    ]
  }
}
