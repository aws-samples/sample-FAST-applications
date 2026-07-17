import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import { Construct } from "constructs";
import {
	AppConfig,
	DEFAULT_RUNTIME_AZS,
	DEFAULT_RUNTIME_VPC_CIDR,
} from "./utils/config-manager";

export interface VpcStackProps extends cdk.NestedStackProps {
	config: AppConfig;
}

/**
 * Networking stack for the AgentCore Runtime VPC mode. A NestedStack of
 * FastMainStack so the Runtime, VPC, and S3 Files share one
 * deployable/destroyable unit. The VPC and shared security group are exposed as
 * public readonly properties for BackendStack to consume.
 *
 * Resources: VPC (10.20.0.0/16) + 2 public + 2 ISOLATED private subnets, NO NAT
 * gateways, 10 interface endpoints, 2 gateway endpoints, and a self-referencing
 * security group shared by the Runtime ENI, endpoint ENIs, and the S3 Files
 * mount targets.
 *
 * Closed network: the Runtime never reaches the public internet. Per-user
 * identity reaches Cognito via the Token Vault (bedrock-agentcore VPC endpoint)
 * carrying cognito:groups, so no NAT egress to the public Cognito hosted domain
 * is needed. Private subnets are PRIVATE_ISOLATED and all AWS access goes
 * through the VPC endpoints below.
 */
export class VpcStack extends cdk.NestedStack {
	public readonly vpc: ec2.IVpc;
	public readonly runtimeSecurityGroup: ec2.ISecurityGroup;

	constructor(scope: Construct, id: string, props: VpcStackProps) {
		const description = "Fullstack AgentCore Solution Template - VPC Stack";
		super(scope, id, { ...props, description });

		// CIDR and AZs are configurable (config.yaml backend.vpc_cidr /
		// availability_zones) so a second environment in the same account/region, or
		// a deployment into a third-party account, can avoid CIDR collisions and pin
		// account-correct AZs without editing this file. Both fall back to the
		// DEFAULT_* constants so an existing config without these keys is unchanged
		// (changing a deployed VPC's CIDR/AZs would replace it, so the fallback must
		// preserve the original values exactly).
		const vpcCidr = props.config.backend.vpc_cidr ?? DEFAULT_RUNTIME_VPC_CIDR;
		const availabilityZones =
			props.config.backend.availability_zones ?? DEFAULT_RUNTIME_AZS;

		// 1. VPC. enableDnsHostnames/Support are the prerequisites for Private DNS
		//    on interface endpoints; both default to true but are stated explicitly.
		//
		//    AZ pinning is REQUIRED, not cosmetic: AgentCore Runtime VPC mode only
		//    supports specific AZ IDs per region (us-east-1: use1-az1, use1-az2,
		//    use1-az4) and "fails during resource creation" for subnets in any
		//    other AZ. Default maxAzs picks us-east-1a + us-east-1b alphabetically,
		//    but in some accounts us-east-1a maps to the UNSUPPORTED use1-az6.
		//    The default us-east-1b (use1-az1) + us-east-1d (use1-az4) are both
		//    supported and physically distinct in the account this template was
		//    validated against. The AZ name -> AZ ID mapping is account-specific,
		//    so another account must override backend.availability_zones with
		//    names that map to supported ids (re-derive via
		//    `aws ec2 describe-availability-zones`).
		this.vpc = new ec2.Vpc(this, "Vpc", {
			ipAddresses: ec2.IpAddresses.cidr(vpcCidr),
			availabilityZones,
			// Closed network: no NAT. natGateways:0 alone is not enough — a
			// PRIVATE_WITH_EGRESS subnet still pulls in a NAT gateway, so the
			// private tier MUST be PRIVATE_ISOLATED (no internet route at all).
			// All AWS access is via the VPC endpoints created below.
			natGateways: 0,
			enableDnsHostnames: true,
			enableDnsSupport: true,
			subnetConfiguration: [
				// Public subnets are retained only to host the interface-endpoint
				// IGW-free baseline and keep AZ layout stable; nothing egresses.
				{ name: "Public", subnetType: ec2.SubnetType.PUBLIC, cidrMask: 24 },
				{
					name: "Private",
					subnetType: ec2.SubnetType.PRIVATE_ISOLATED,
					cidrMask: 24,
				},
			],
		});

		// 2. Security group shared by the Runtime ENI, endpoint ENIs, and S3 Files
		//    mount targets. Self-referencing so members can reach each other:
		//    443 for the interface endpoints, 2049 (NFS) for the S3 Files mount.
		const sg = new ec2.SecurityGroup(this, "RuntimeAndEndpointSg", {
			vpc: this.vpc,
			allowAllOutbound: true,
			description:
				"Shared SG for AgentCore Runtime, VPC endpoints, and S3 Files mount targets",
		});
		sg.addIngressRule(
			sg,
			ec2.Port.tcp(443),
			"HTTPS for VPC endpoints (self-ref)",
		);
		sg.addIngressRule(
			sg,
			ec2.Port.tcp(2049),
			"NFS for S3 Files mount target (self-ref)",
		);
		this.runtimeSecurityGroup = sg;

		// 3. Interface endpoints. aws-cdk-lib 2.257 ships presets for the bedrock
		//    services (verified against `describe-vpc-endpoint-services` in us-east-1),
		//    so we use the typed presets instead of raw service-name strings.
		const interfaceServices: Array<{
			id: string;
			service: ec2.InterfaceVpcEndpointAwsService;
		}> = [
			{
				id: "BedrockRuntime",
				service: ec2.InterfaceVpcEndpointAwsService.BEDROCK_RUNTIME,
			},
			{
				// Data plane endpoint shared by ALL data plane primitives: Runtime,
				// Memory (History Lambda list_events), Identity / Token Vault, and the
				// built-in Code Interpreter. AgentCore exposes only 3 PrivateLink
				// endpoints (data plane / control plane / gateway), so there is NO
				// dedicated Code Interpreter endpoint; its data plane API rides this
				// one via the regional DNS name (bedrock-agentcore.<region>.amazonaws.com),
				// which this endpoint's Private DNS resolves. Gateway is the lone
				// exception (its own endpoint, below).
				id: "BedrockAgentcore",
				service: ec2.InterfaceVpcEndpointAwsService.BEDROCK_AGENTCORE,
			},
			{
				// Gateway is the ONLY data plane primitive with a separate endpoint
				// (host *.gateway.bedrock-agentcore.<region>.amazonaws.com). Required
				// for every Runtime to Gateway MCP tool call; removing it breaks all
				// tools with "Name or service not known" (verified in testing).
				id: "BedrockAgentcoreGateway",
				service: ec2.InterfaceVpcEndpointAwsService.BEDROCK_AGENTCORE_GATEWAY,
			},
			{ id: "Ssm", service: ec2.InterfaceVpcEndpointAwsService.SSM },
			{
				id: "SecretsManager",
				service: ec2.InterfaceVpcEndpointAwsService.SECRETS_MANAGER,
			},
			{
				id: "Logs",
				service: ec2.InterfaceVpcEndpointAwsService.CLOUDWATCH_LOGS,
			},
			{ id: "Xray", service: ec2.InterfaceVpcEndpointAwsService.XRAY },
			{ id: "EcrApi", service: ec2.InterfaceVpcEndpointAwsService.ECR },
			{ id: "EcrDkr", service: ec2.InterfaceVpcEndpointAwsService.ECR_DOCKER },
			{
				id: "BedrockMantle",
				// OpenAI GPT-5.x is served from the bedrock-mantle endpoint (OpenAI
				// Responses API), a separate service from bedrock-runtime. Available
				// in-region since 2026-06, so it is a plain interface
				// endpoint here like the others; aws-cdk-lib 2.257 has no preset for
				// it yet, hence the explicit service name.
				service: new ec2.InterfaceVpcEndpointAwsService("bedrock-mantle"),
			},
		];
		for (const { id, service } of interfaceServices) {
			this.vpc.addInterfaceEndpoint(`${id}Endpoint`, {
				service,
				subnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
				securityGroups: [sg],
				privateDnsEnabled: true,
			});
		}

		// 4. Gateway endpoints (free): S3 for ECR layer pulls / Skills sync, DynamoDB
		//    for the Feedback table.
		this.vpc.addGatewayEndpoint("S3GatewayEndpoint", {
			service: ec2.GatewayVpcEndpointAwsService.S3,
		});
		this.vpc.addGatewayEndpoint("DynamoDbGatewayEndpoint", {
			service: ec2.GatewayVpcEndpointAwsService.DYNAMODB,
		});

		// 5. Tags so the demo resources are easy to find and tear down.
		cdk.Tags.of(this).add("Project", "FAST");
		cdk.Tags.of(this).add("Purpose", "demo");
		cdk.Tags.of(this).add("ManagedBy", "CDK");
	}
}
