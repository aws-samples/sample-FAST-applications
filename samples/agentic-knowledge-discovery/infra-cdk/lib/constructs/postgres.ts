import * as cdk from "aws-cdk-lib"
import * as ec2 from "aws-cdk-lib/aws-ec2"
import * as rds from "aws-cdk-lib/aws-rds"
import * as lambda from "aws-cdk-lib/aws-lambda"
import * as ecr_assets from "aws-cdk-lib/aws-ecr-assets"
import * as logs from "aws-cdk-lib/aws-logs"
import * as customResources from "aws-cdk-lib/custom-resources"
import * as crypto from "crypto"
import * as fs from "fs"
import { Construct } from "constructs"
import { AppConfig } from "../utils/config-manager"
import * as path from "path"

export interface PostgresConstructProps {
  config: AppConfig
  vpc: ec2.IVpc
  databaseSecurityGroup: ec2.ISecurityGroup
  lambdaSecurityGroup: ec2.ISecurityGroup
}

/**
 * Aurora Serverless v2 PostgreSQL holding the structured document metadata the
 * agent queries via the structured_search tool. The
 * cluster lives in the private isolated subnets and is reachable only from the
 * Lambda security group. Credentials are generated into Secrets Manager.
 *
 * A seed Lambda (container image with psycopg2) runs once at deploy via a
 * custom resource: it creates the schema and inserts the document metadata
 * rows. The seed re-runs whenever the SQL files change (content hash).
 */
export class PostgresConstruct extends Construct {
  public readonly cluster: rds.DatabaseCluster
  public readonly databaseName = "ragmeta"

  constructor(scope: Construct, id: string, props: PostgresConstructProps) {
    super(scope, id)

    const { config, vpc, databaseSecurityGroup, lambdaSecurityGroup } = props

    this.cluster = new rds.DatabaseCluster(this, "Cluster", {
      engine: rds.DatabaseClusterEngine.auroraPostgres({
        version: rds.AuroraPostgresEngineVersion.of("17.9", "17"),
      }),
      vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
      securityGroups: [databaseSecurityGroup],
      defaultDatabaseName: this.databaseName,
      credentials: rds.Credentials.fromGeneratedSecret("ragmeta_admin", {
        secretName: `${config.stack_name_base}/db-credentials`,
      }),
      serverlessV2MinCapacity: 0.5,
      serverlessV2MaxCapacity: 2,
      writer: rds.ClusterInstance.serverlessV2("writer"),
      storageEncrypted: true,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    })

    this.seedDatabase(config, vpc, lambdaSecurityGroup)
  }

  /** Runs schema + seed SQL once at deploy through a custom resource. */
  private seedDatabase(
    config: AppConfig,
    vpc: ec2.IVpc,
    lambdaSecurityGroup: ec2.ISecurityGroup
  ): void {
    const seedDir = path.join(__dirname, "../../lambdas/db-seed") // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal

    const seedFn = new lambda.DockerImageFunction(this, "SeedLambda", {
      functionName: `${config.stack_name_base}-db-seed`,
      code: lambda.DockerImageCode.fromImageAsset(seedDir, {
        platform: ecr_assets.Platform.LINUX_ARM64,
      }),
      architecture: lambda.Architecture.ARM_64,
      timeout: cdk.Duration.minutes(5),
      memorySize: 512,
      vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
      securityGroups: [lambdaSecurityGroup],
      environment: {
        DB_SECRET_ARN: this.cluster.secret!.secretArn,
        DB_CLUSTER_ENDPOINT: this.cluster.clusterEndpoint.hostname,
        DB_NAME: this.databaseName,
      },
      logGroup: new logs.LogGroup(this, "SeedLambdaLogGroup", {
        logGroupName: `/aws/lambda/${config.stack_name_base}-db-seed`,
        retention: logs.RetentionDays.ONE_WEEK,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      }),
    })
    this.cluster.secret!.grantRead(seedFn)

    const provider = new customResources.Provider(this, "SeedProvider", {
      onEventHandler: seedFn,
    })

    // Content hash so the seed re-runs when the SQL changes.
    const schemaSql = fs.readFileSync(path.join(seedDir, "schema.sql"), "utf-8")
    const seedSql = fs.readFileSync(path.join(seedDir, "seed.sql"), "utf-8")
    const contentHash = crypto
      .createHash("sha256")
      .update(schemaSql + seedSql)
      .digest("hex")
      .slice(0, 16)

    const seed = new cdk.CustomResource(this, "DbSeed", {
      serviceToken: provider.serviceToken,
      properties: { ContentHash: contentHash },
    })
    seed.node.addDependency(this.cluster)

    new cdk.CfnOutput(this, "DbClusterEndpoint", {
      value: this.cluster.clusterEndpoint.hostname,
      description: "Aurora PostgreSQL cluster endpoint",
    })
  }
}
