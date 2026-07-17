import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import { Construct } from "constructs";
import { AppConfig } from "./utils/config-manager";
import { selectableModelsForFrontend } from "./utils/model-registry";

// Import nested stacks
import { BackendStack } from "./backend-stack";
import { AmplifyHostingStack } from "./amplify-hosting-stack";
import { CognitoStack } from "./cognito-stack";
import { VpcStack } from "./vpc-stack";
import { SkillsStorageStack } from "./skills-storage-stack";

export interface FastAmplifyStackProps extends cdk.StackProps {
	config: AppConfig;
}

export class FastMainStack extends cdk.Stack {
	public readonly amplifyHostingStack: AmplifyHostingStack;
	public readonly backendStack: BackendStack;
	public readonly cognitoStack: CognitoStack;
	public readonly vpcStack?: VpcStack;
	public readonly skillsStorageStack?: SkillsStorageStack;

	constructor(scope: Construct, id: string, props: FastAmplifyStackProps) {
		const description =
			"Fullstack AgentCore Solution Template - Main Stack (v0.4.1) (uksb-v6dos0t5g8)";
		super(scope, id, { ...props, description });

		// Step 1: Create the Amplify stack to get the predictable domain
		this.amplifyHostingStack = new AmplifyHostingStack(this, `${id}-amplify`, {
			config: props.config,
		});

		this.cognitoStack = new CognitoStack(this, `${id}-cognito`, {
			config: props.config,
			callbackUrls: [
				"http://localhost:3000",
				this.amplifyHostingStack.amplifyUrl,
			],
		});

		// Create the VPC when running in VPC mode with CDK-managed networking.
		// The Runtime ENIs, VPC endpoints, and S3 Files mount targets all live in
		// this VPC. Skipped for PUBLIC mode or vpc_management: EXISTING.
		if (
			props.config.backend.network_mode === "VPC" &&
			props.config.backend.vpc_management === "CDK"
		) {
			this.vpcStack = new VpcStack(this, `${id}-vpc`, { config: props.config });
		}

		// Skills storage: S3 Files file system + mount targets + access point,
		// synced from skills/. Requires the CDK-managed VPC (mount
		// targets must sit in the runtime's private subnets/AZs).
		if (props.config.backend.skills?.enabled && this.vpcStack) {
			// Closed network: mount targets sit in the isolated private tier.
			// Must match VpcStack's PRIVATE_ISOLATED subnet type.
			const privateSubnets = this.vpcStack.vpc.selectSubnets({
				subnetType: ec2.SubnetType.PRIVATE_ISOLATED,
			}).subnets;
			this.skillsStorageStack = new SkillsStorageStack(this, `${id}-skills`, {
				config: props.config,
				privateSubnets,
				securityGroup: this.vpcStack.runtimeSecurityGroup,
			});
		}

		// Step 2: Create backend stack with the predictable Amplify URL and Cognito details
		this.backendStack = new BackendStack(this, `${id}-backend`, {
			config: props.config,
			userPoolId: this.cognitoStack.userPoolId,
			userPoolClientId: this.cognitoStack.userPoolClientId,
			userPoolDomain: this.cognitoStack.userPoolDomain,
			frontendUrl: this.amplifyHostingStack.amplifyUrl,
			vpc: this.vpcStack?.vpc,
			runtimeSecurityGroup: this.vpcStack?.runtimeSecurityGroup,
			skillsAccessPointArn: this.skillsStorageStack?.accessPointArn,
			skillsFileSystemArn: this.skillsStorageStack?.fileSystemArn,
		});

		// Outputs
		new cdk.CfnOutput(this, "AmplifyAppId", {
			value: this.amplifyHostingStack.amplifyApp.appId,
			description: "Amplify App ID - use this for manual deployment",
			exportName: `${props.config.stack_name_base}-AmplifyAppId`,
		});

		new cdk.CfnOutput(this, "CognitoUserPoolId", {
			value: this.cognitoStack.userPoolId,
			description: "Cognito User Pool ID",
			exportName: `${props.config.stack_name_base}-CognitoUserPoolId`,
		});

		new cdk.CfnOutput(this, "CognitoClientId", {
			value: this.cognitoStack.userPoolClientId,
			description: "Cognito User Pool Client ID",
			exportName: `${props.config.stack_name_base}-CognitoClientId`,
		});

		new cdk.CfnOutput(this, "CognitoDomain", {
			value: `${this.cognitoStack.userPoolDomain.domainName}.auth.${cdk.Aws.REGION}.amazoncognito.com`,
			description: "Cognito Domain for OAuth",
			exportName: `${props.config.stack_name_base}-CognitoDomain`,
		});

		new cdk.CfnOutput(this, "RuntimeArn", {
			value: this.backendStack.runtimeArn,
			description: "AgentCore Runtime ARN",
			exportName: `${props.config.stack_name_base}-RuntimeArn`,
		});

		new cdk.CfnOutput(this, "MemoryArn", {
			value: this.backendStack.memoryArn,
			description: "AgentCore Memory ARN",
			exportName: `${props.config.stack_name_base}-MemoryArn`,
		});

		new cdk.CfnOutput(this, "FeedbackApiUrl", {
			value: this.backendStack.feedbackApiUrl,
			description: "Feedback API Gateway URL",
			exportName: `${props.config.stack_name_base}-FeedbackApiUrl`,
		});

		// Re-surface the History API URL at the parent stack (NestedStack
		// CfnOutputs do not propagate), so deploy-frontend.py can read it for
		// aws-exports.json.
		new cdk.CfnOutput(this, "HistoryApiUrl", {
			value: this.backendStack.historyApiUrl,
			description: "Chat History API Gateway URL",
			exportName: `${props.config.stack_name_base}-HistoryApiUrl`,
		});

		// Re-surface the selectable model list at the parent stack so
		// deploy-frontend.py can write it into aws-exports.json. Derived from the
		// single source of truth (utils/model-registry.ts); key/label/available
		// only, no physical model id.
		new cdk.CfnOutput(this, "SelectableModelsJson", {
			value: selectableModelsForFrontend(),
			description: "JSON array of selectable chat models (key/label/available)",
			exportName: `${props.config.stack_name_base}-SelectableModelsJson`,
		});

		// VPC outputs (only when CDK-managed VPC is created). Surfaced here because
		// NestedStack CfnOutputs do not propagate to the parent stack.
		if (this.vpcStack) {
			new cdk.CfnOutput(this, "VpcId", {
				value: this.vpcStack.vpc.vpcId,
				description: "ID of the CDK-managed VPC for the runtime",
				exportName: `${props.config.stack_name_base}-VpcId`,
			});
			new cdk.CfnOutput(this, "RuntimeSecurityGroupId", {
				value: this.vpcStack.runtimeSecurityGroup.securityGroupId,
				description:
					"Shared SG for runtime, VPC endpoints, and S3 Files mounts",
				exportName: `${props.config.stack_name_base}-RuntimeSecurityGroupId`,
			});
		}

		new cdk.CfnOutput(this, "AmplifyConsoleUrl", {
			value: `https://console.aws.amazon.com/amplify/apps/${this.amplifyHostingStack.amplifyApp.appId}`,
			description: "Amplify Console URL for monitoring deployments",
		});

		new cdk.CfnOutput(this, "AmplifyUrl", {
			value: this.amplifyHostingStack.amplifyUrl,
			description: "Amplify Frontend URL (available after deployment)",
		});

		new cdk.CfnOutput(this, "StagingBucketName", {
			value: this.amplifyHostingStack.stagingBucket.bucketName,
			description: "S3 bucket for Amplify deployment staging",
			exportName: `${props.config.stack_name_base}-StagingBucket`,
		});
	}
}
