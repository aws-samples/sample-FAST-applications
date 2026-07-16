import * as cdk from "aws-cdk-lib"
import * as ec2 from "aws-cdk-lib/aws-ec2"
import { Construct } from "constructs"
import { AppConfig } from "../utils/config-manager"

export interface NetworkConstructProps {
  config: AppConfig
}

/**
 * Minimal VPC for the data plane. Aurora and the in-VPC tool Lambdas
 * (structured_search, the DB seeder) run in private isolated subnets with no NAT
 * gateway (nothing here needs the internet). A Secrets Manager interface
 * endpoint lets those Lambdas read the DB credentials without leaving the VPC.
 *
 * The AgentCore Runtime and the doc_search Lambda stay OUT of this VPC: the
 * runtime is PUBLIC in v1, and doc_search calls the managed Bedrock Retrieve
 * API. To move the runtime fully into the VPC later, add the interface/gateway
 * endpoints for the AWS services used and switch network_mode to VPC.
 */
export class NetworkConstruct extends Construct {
  public readonly vpc: ec2.Vpc
  /** Security group for Aurora; only accepts 5432 from the Lambda SG. */
  public readonly databaseSecurityGroup: ec2.SecurityGroup
  /** Security group for in-VPC Lambdas that reach Aurora + the Secrets endpoint. */
  public readonly lambdaSecurityGroup: ec2.SecurityGroup

  constructor(scope: Construct, id: string, props: NetworkConstructProps) {
    super(scope, id)

    this.vpc = new ec2.Vpc(this, "Vpc", {
      vpcName: `${props.config.stack_name_base}-vpc`,
      maxAzs: 2,
      natGateways: 0,
      subnetConfiguration: [
        {
          name: "data",
          subnetType: ec2.SubnetType.PRIVATE_ISOLATED,
          cidrMask: 24,
        },
      ],
    })

    this.lambdaSecurityGroup = new ec2.SecurityGroup(this, "LambdaSg", {
      vpc: this.vpc,
      description: "In-VPC tool/seed Lambdas (Aurora + Secrets Manager endpoint)",
      allowAllOutbound: true,
    })

    this.databaseSecurityGroup = new ec2.SecurityGroup(this, "DatabaseSg", {
      vpc: this.vpc,
      description: "Aurora PostgreSQL; ingress 5432 from the Lambda SG only",
      allowAllOutbound: false,
    })
    this.databaseSecurityGroup.addIngressRule(
      this.lambdaSecurityGroup,
      ec2.Port.tcp(5432),
      "PostgreSQL from in-VPC Lambdas"
    )

    // Secrets Manager interface endpoint so in-VPC Lambdas can read DB creds
    // without a NAT gateway. Private DNS is on by default.
    const secretsEndpointSg = new ec2.SecurityGroup(this, "SecretsEndpointSg", {
      vpc: this.vpc,
      description: "Secrets Manager interface endpoint",
      allowAllOutbound: true,
    })
    secretsEndpointSg.addIngressRule(
      this.lambdaSecurityGroup,
      ec2.Port.tcp(443),
      "HTTPS from in-VPC Lambdas"
    )
    this.vpc.addInterfaceEndpoint("SecretsManagerEndpoint", {
      service: ec2.InterfaceVpcEndpointAwsService.SECRETS_MANAGER,
      securityGroups: [secretsEndpointSg],
    })

    new cdk.CfnOutput(this, "VpcId", {
      value: this.vpc.vpcId,
      description: "Data-plane VPC ID",
    })
  }
}
