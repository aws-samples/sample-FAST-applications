import * as cdk from "aws-cdk-lib";
import * as cognito from "aws-cdk-lib/aws-cognito";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as iam from "aws-cdk-lib/aws-iam";
import * as ssm from "aws-cdk-lib/aws-ssm";
import * as secretsmanager from "aws-cdk-lib/aws-secretsmanager";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as apigateway from "aws-cdk-lib/aws-apigateway";
import * as logs from "aws-cdk-lib/aws-logs";
import * as s3 from "aws-cdk-lib/aws-s3";
// Single import for both the graduated L2 constructs (Runtime,
// AgentRuntimeArtifact, RuntimeNetworkConfiguration, ...) and the L1 Cfn*
// classes (CfnGateway, CfnGatewayTarget, CfnRuntime). The former
// @aws-cdk/aws-bedrock-agentcore-alpha package is no longer needed: every
// construct used here graduated to aws-cdk-lib/aws-bedrockagentcore (only the
// Policy submodule remains in alpha, and this stack manages Cedar policies
// through a Custom Resource Lambda instead).
import * as agentcore from "aws-cdk-lib/aws-bedrockagentcore";
import { PythonFunction } from "@aws-cdk/aws-lambda-python-alpha";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as ecr_assets from "aws-cdk-lib/aws-ecr-assets";
import * as cr from "aws-cdk-lib/custom-resources";
import { Construct } from "constructs";
import {
	AppConfig,
	DEFAULT_IDLE_RUNTIME_SESSION_TIMEOUT_SECONDS,
} from "./utils/config-manager";
import { AgentCoreRole } from "./utils/agentcore-role";
import { modelMapJson, defaultModelKey } from "./utils/model-registry";
import * as path from "path";
import * as fs from "fs";

export interface BackendStackProps extends cdk.NestedStackProps {
	config: AppConfig;
	userPoolId: string;
	userPoolClientId: string;
	userPoolDomain: cognito.UserPoolDomain;
	frontendUrl: string;
	/**
	 * VPC created by VpcStack, passed when network_mode is "VPC" and
	 * vpc_management is "CDK". When omitted, buildNetworkConfiguration falls back
	 * to ec2.Vpc.fromLookup using config.backend.vpc (vpc_management "EXISTING").
	 */
	vpc?: ec2.IVpc;
	/** Shared self-referencing SG from VpcStack (443/2049), used when vpc is passed. */
	runtimeSecurityGroup?: ec2.ISecurityGroup;
	/** S3 Files access point ARN to mount as skills (Phase 2b). Undefined disables the mount. */
	skillsAccessPointArn?: string;
	/** S3 Files file-system ARN, used to scope the runtime's s3files: IAM statement. */
	skillsFileSystemArn?: string;
}

export class BackendStack extends cdk.NestedStack {
	public readonly userPoolId: string;
	public readonly userPoolClientId: string;
	public readonly userPoolDomain: cognito.UserPoolDomain;
	public feedbackApiUrl: string;
	public historyApiUrl: string;
	public runtimeArn: string;
	public memoryArn: string;
	private memoryId: string;
	private agentName: cdk.CfnParameter;
	private userPool: cognito.IUserPool;
	private machineClient: cognito.UserPoolClient;
	private machineClientSecret: secretsmanager.Secret;
	private runtimeCredentialProvider: cdk.CustomResource;
	private agentRuntime: agentcore.Runtime;
	private gateway: agentcore.CfnGateway;
	private gatewayRole: iam.Role;
	private gatewayCedarPolicy: cdk.CustomResource;
	private readonly vpc?: ec2.IVpc;
	private readonly runtimeSecurityGroup?: ec2.ISecurityGroup;
	private readonly skillsAccessPointArn?: string;
	private readonly skillsFileSystemArn?: string;

	constructor(scope: Construct, id: string, props: BackendStackProps) {
		super(scope, id, props);

		// Store the Cognito values
		this.userPoolId = props.userPoolId;
		this.userPoolClientId = props.userPoolClientId;
		this.userPoolDomain = props.userPoolDomain;

		// VPC wiring (network_mode: VPC, vpc_management: CDK). Undefined for
		// PUBLIC mode or when reusing an existing VPC via fromLookup.
		this.vpc = props.vpc;
		this.runtimeSecurityGroup = props.runtimeSecurityGroup;

		// Skills (S3 Files) wiring (Phase 2b). Undefined disables the mount.
		this.skillsAccessPointArn = props.skillsAccessPointArn;
		this.skillsFileSystemArn = props.skillsFileSystemArn;

		// Import the Cognito resources from the other stack
		this.userPool = cognito.UserPool.fromUserPoolId(
			this,
			"ImportedUserPoolForBackend",
			props.userPoolId,
		);
		// then create the user pool client
		cognito.UserPoolClient.fromUserPoolClientId(
			this,
			"ImportedUserPoolClient",
			props.userPoolClientId,
		);

		// Create Machine-to-Machine authentication components
		this.createMachineAuthentication(props.config);

		// DEPLOYMENT ORDER EXPLANATION:
		// 1. Cognito User Pool & Client (created in separate CognitoStack)
		// 2. Machine Client & Resource Server (created above for M2M auth)
		// 3. AgentCore Gateway (created next - uses machine client for auth)
		// 4. AgentCore Runtime (created last - independent of gateway)
		//
		// This order ensures that authentication components are available before
		// the gateway that depends on them, while keeping the runtime separate
		// since it doesn't directly depend on the gateway.

		// Create AgentCore Gateway (before Runtime)
		this.createAgentCoreGateway(props.config);

		// Create AgentCore Runtime resources
		this.createAgentCoreRuntime(props.config);

		// Host the long-term-memory listing tool as a single-tool MCP server
		// on its own AgentCore Runtime and register it as a gateway MCP server
		// target. Runs after createAgentCoreRuntime because it
		// scopes IAM to the memory created there. Gated like the in-process
		// tool it replaces: the /facts namespace is never written
		// unless long-term memory is enabled.
		if (props.config.backend.use_long_term_memory) {
			this.createLtmMcpServerTarget(props.config);
		}

		// Host the Strands Agents documentation MCP server on its own AgentCore
		// Runtime and register it as a gateway MCP server target.
		// Same pattern as the LTM target above, but on the managed public
		// network: its tools fetch strandsagents.com over the internet, which
		// the closed VPC cannot reach.
		this.createStrandsMcpServerTarget(props.config);

		// Register the managed AgentCore Web Search connector as a gateway
		// target. Unlike the MCP server targets above, this is a
		// built-in connector (connectorId "web-search") — no Runtime or
		// network configuration is involved; the bedrock-agentcore service
		// hosts the search backend and serves queries entirely within AWS.
		this.createWebSearchTarget(props.config);

		// Store runtime ARN in SSM for frontend stack
		this.createRuntimeSSMParameters(props.config);

		// Store Cognito configuration in SSM for testing and frontend
		this.createCognitoSSMParameters(props.config);

		// Create Feedback DynamoDB table (example of application data storage)
		const feedbackTable = this.createFeedbackTable(props.config);

		// Create API Gateway Feedback API resources (example of best-practice API Gateway + Lambda
		// pattern)
		this.createFeedbackApi(props.config, props.frontendUrl, feedbackTable);

		// Create the Chat History API: a DynamoDB "table of
		// contents" of sessions plus Lambdas that (a) restore a session's body
		// from AgentCore Memory and (b) generate blog-style titles via Haiku.
		// Independent of the closed-network Runtime — these are ordinary Lambdas.
		this.createHistoryApi(props.config, props.frontendUrl);
	}

	private createAgentCoreRuntime(config: AppConfig): void {
		const pattern = config.backend?.pattern || "strands-single-agent";

		// Parameters
		this.agentName = new cdk.CfnParameter(this, "AgentName", {
			type: "String",
			default: "FASTAgent",
			description: "Name for the agent runtime",
		});

		const stack = cdk.Stack.of(this);
		const deploymentType = config.backend.deployment_type;

		// Create the agent runtime artifact based on deployment type
		let agentRuntimeArtifact: agentcore.AgentRuntimeArtifact;
		let zipPackagerResource: cdk.CustomResource | undefined;

		if (
			deploymentType === "zip" &&
			(pattern === "claude-agent-sdk-single-agent" ||
				pattern === "claude-agent-sdk-multi-agent")
		) {
			throw new Error(
				"claude-agent-sdk patterns require Docker deployment (deployment_type: docker) " +
					"because they need Node.js and the claude-code CLI installed at build time.",
			);
		}

		if (deploymentType === "zip") {
			// ZIP DEPLOYMENT: Use Lambda to package and upload to S3 (no Docker required)
			const repoRoot = path.resolve(__dirname, "..", ".."); // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal
			const agentDir = path.join(repoRoot, "agent", pattern); // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal

			// Create S3 bucket for agent code
			const agentCodeBucket = new s3.Bucket(this, "AgentCodeBucket", {
				removalPolicy: cdk.RemovalPolicy.DESTROY,
				autoDeleteObjects: true,
				versioned: true,
				blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
			});

			// Lambda to package agent code
			const packagerLambda = new lambda.Function(this, "ZipPackagerLambda", {
				runtime: lambda.Runtime.PYTHON_3_12,
				handler: "index.handler",
				code: lambda.Code.fromAsset(
					path.join(__dirname, "..", "lambdas", "zip-packager"),
				), // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal
				timeout: cdk.Duration.minutes(10),
				memorySize: 1024,
				ephemeralStorageSize: cdk.Size.gibibytes(2),
			});

			agentCodeBucket.grantReadWrite(packagerLambda);

			// Read agent code files and encode as base64
			const agentCode: Record<string, string> = {};

			// Read agent files recursively (all file types)
			const readAgentFiles = (dir: string, prefix: string) => {
				for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
					const fullPath = path.join(dir, entry.name); // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal
					const relativePath = prefix
						? path.join(prefix, entry.name)
						: entry.name; // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal
					if (entry.isDirectory() && entry.name !== "__pycache__") {
						readAgentFiles(fullPath, relativePath);
					} else if (entry.isFile()) {
						agentCode[relativePath] = fs
							.readFileSync(fullPath)
							.toString("base64");
					}
				}
			};
			readAgentFiles(agentDir, "");

			// Read shared modules — gateway/ keeps its name. (The former repo-root
			// tools/ packaged as agentcore_tools/ was removed when the
			// agent moved to the official strands_tools Code Interpreter.)
			const gatewayDir = path.join(repoRoot, "gateway"); // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal
			if (fs.existsSync(gatewayDir)) {
				this.readDirRecursive(gatewayDir, "gateway", agentCode);
			}

			// Read shared utilities (agent/utils/) — contains auth.py and ssm.py
			// used by all agent patterns for JWT extraction and SSM parameter access
			const utilsDir = path.join(repoRoot, "agent", "utils"); // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal
			if (fs.existsSync(utilsDir)) {
				this.readDirRecursive(utilsDir, "utils", agentCode);
			}

			// Read requirements
			const requirementsPath = path.join(agentDir, "requirements.txt"); // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal
			const requirements = fs
				.readFileSync(requirementsPath, "utf-8")
				.split("\n")
				.map((line) => line.trim())
				.filter((line) => line && !line.startsWith("#"));

			// Create hash for change detection
			// We use this to trigger update when content changes
			const contentHash = this.hashContent(
				JSON.stringify({ requirements, agentCode }),
			);

			// Custom Resource to trigger packaging
			const provider = new cr.Provider(this, "ZipPackagerProvider", {
				onEventHandler: packagerLambda,
			});

			zipPackagerResource = new cdk.CustomResource(this, "ZipPackager", {
				serviceToken: provider.serviceToken,
				properties: {
					BucketName: agentCodeBucket.bucketName,
					ObjectKey: "deployment_package.zip",
					Requirements: requirements,
					AgentCode: agentCode,
					ContentHash: contentHash,
				},
			});

			// Store bucket name in SSM for updates
			new ssm.StringParameter(this, "AgentCodeBucketNameParam", {
				parameterName: `/${config.stack_name_base}/agent-code-bucket`,
				stringValue: agentCodeBucket.bucketName,
				description: "S3 bucket for agent code deployment packages",
			});

			// Determine the main agent file for the pattern.
			// Each pattern has a different entry point:
			//   strands-single-agent → basic_agent.py
			//   langgraph-single-agent → langgraph_agent.py
			//   agui-*, claude-* → agent.py
			const mainFiles = fs
				.readdirSync(agentDir)
				.filter((f: string) => f.endsWith(".py") && f !== "__init__.py");
			const agentEntryPoint =
				mainFiles.length === 1
					? mainFiles[0]
					: mainFiles.find(
							(f: string) => f.includes("agent") && f !== "__init__.py",
						) || mainFiles[0];

			agentRuntimeArtifact = agentcore.AgentRuntimeArtifact.fromS3(
				{
					bucketName: agentCodeBucket.bucketName,
					objectKey: "deployment_package.zip",
				},
				agentcore.AgentCoreRuntime.PYTHON_3_12,
				["opentelemetry-instrument", agentEntryPoint],
			);
		} else {
			// DOCKER DEPLOYMENT: Use container-based deployment
			agentRuntimeArtifact = agentcore.AgentRuntimeArtifact.fromAsset(
				path.resolve(__dirname, "..", ".."), // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal
				{
					platform: ecr_assets.Platform.LINUX_ARM64,
					file: `agent/${pattern}/Dockerfile`,
				},
			);
		}

		// Configure network mode based on config.yaml settings.
		// PUBLIC: Runtime is accessible over the public internet (default).
		// VPC: Runtime is deployed into a user-provided VPC for private network isolation.
		//      The user must ensure their VPC has the necessary VPC endpoints for AWS services.
		//      See docs/DEPLOYMENT.md for the full list of required VPC endpoints.
		const networkConfiguration = this.buildNetworkConfiguration(config);

		// Configure JWT authorizer with Cognito
		const authorizerConfiguration =
			agentcore.RuntimeAuthorizerConfiguration.usingJWT(
				`https://cognito-idp.${stack.region}.amazonaws.com/${this.userPoolId}/.well-known/openid-configuration`,
				[this.userPoolClientId],
			);

		// Create AgentCore execution role
		const agentRole = new AgentCoreRole(this, "AgentCoreRole");

		// Create memory resource with short-term memory (conversation history) as default
		// To enable long-term strategies (summaries, preferences, facts), see docs/MEMORY_INTEGRATION.md
		const memory = new cdk.CfnResource(this, "AgentMemory", {
			type: "AWS::BedrockAgentCore::Memory",
			properties: {
				Name: cdk.Names.uniqueResourceName(this, { maxLength: 48 }),
				EventExpiryDuration: 30,
				Description: `Short-term memory for ${config.stack_name_base} agent`,
				MemoryStrategies: [
					{
						// Extracts and stores factual information shared by the user across sessions.
						// Stored under /facts/{actorId} — retrieved on each turn to personalise responses.
						SemanticMemoryStrategy: {
							Name: "FactExtractor",
							Namespaces: ["/facts/{actorId}"],
						},
					},
				],
				MemoryExecutionRoleArn: agentRole.roleArn,
				Tags: {
					Name: `${config.stack_name_base}_Memory`,
					ManagedBy: "CDK",
				},
			},
		});
		const memoryId = memory.getAtt("MemoryId").toString();
		const memoryArn = memory.getAtt("MemoryArn").toString();

		// Store the memory ARN/ID for access from main stack and the History API
		// (the chat history Lambda reads short-term events from this memory).
		this.memoryArn = memoryArn;
		this.memoryId = memoryId;

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
					"bedrock-agentcore:ListMemoryRecords", // list_long_term_memories tool
				],
				resources: [memoryArn],
			}),
		);

		// Add SSM permissions for AgentCore Gateway URL lookup
		agentRole.addToPolicy(
			new iam.PolicyStatement({
				sid: "SSMParameterAccess",
				effect: iam.Effect.ALLOW,
				actions: ["ssm:GetParameter", "ssm:GetParameters"],
				resources: [
					`arn:aws:ssm:${this.region}:${this.account}:parameter/${config.stack_name_base}/*`,
				],
			}),
		);

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
				resources: [
					`arn:aws:bedrock-agentcore:${this.region}:aws:code-interpreter/*`,
				],
			}),
		);

		// Add OAuth2 Credential Provider access for AgentCore Runtime
		// The @requires_access_token decorator performs a two-stage process:
		// 1. GetOauth2CredentialProvider - Looks up provider metadata (ARN, vendor config, grant types)
		// 2. GetResourceOauth2Token - Uses metadata to fetch the actual access token from Token Vault
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
			}),
		);

		// Add Secrets Manager access for OAuth2
		// AgentCore Runtime needs to read two secrets:
		// 1. Machine client secret (created by CDK)
		// 2. Token Vault OAuth2 secret (created by AgentCore Identity)
		agentRole.addToPolicy(
			new iam.PolicyStatement({
				sid: "SecretsManagerOAuth2Access",
				effect: iam.Effect.ALLOW,
				actions: ["secretsmanager:GetSecretValue"],
				resources: [
					`arn:aws:secretsmanager:${this.region}:${this.account}:secret:/${config.stack_name_base}/machine_client_secret*`,
					`arn:aws:secretsmanager:${this.region}:${this.account}:secret:bedrock-agentcore-identity!default/oauth2/${config.stack_name_base}-runtime-gateway-auth*`,
				],
			}),
		);

		// S3 Files (skills) mount access. The AgentCore
		// runtime-filesystem-configurations doc requires three actions, but they
		// split into two statements by whether the s3files:AccessPointArn context
		// key is present at evaluation time:
		//  - ClientMount + ClientWrite run at mount time, where AgentCore supplies
		//    the s3files:AccessPointArn context, so we scope them with that
		//    ArnEquals condition (least privilege to the one access point).
		//  - GetAccessPoint runs at Create/UpdateAgentRuntime validation time
		//    WITHOUT that context key, so a conditioned statement evaluates to an
		//    implicit deny (verified via iam simulate-principal-policy) and the
		//    runtime update fails with "missing s3files:GetAccessPoint". It must be
		//    an unconditioned statement, scoped to the file-system ARN.
		const skillsMountPath = config.backend.skills?.mount_path ?? "/mnt/skills";
		if (this.skillsAccessPointArn && this.skillsFileSystemArn) {
			agentRole.addToPolicy(
				new iam.PolicyStatement({
					sid: "S3FilesSkillsMount",
					effect: iam.Effect.ALLOW,
					actions: ["s3files:ClientMount", "s3files:ClientWrite"],
					resources: [this.skillsFileSystemArn],
					conditions: {
						ArnEquals: {
							"s3files:AccessPointArn": this.skillsAccessPointArn,
						},
					},
				}),
			);
			agentRole.addToPolicy(
				new iam.PolicyStatement({
					sid: "S3FilesSkillsDescribe",
					effect: iam.Effect.ALLOW,
					// AgentCore's CreateAgentRuntime/UpdateAgentRuntime validation calls a
					// series of S3 Files describe APIs (observed: GetAccessPoint then
					// ListMountTargets, and likely GetFileSystem). These run WITHOUT the
					// s3files:AccessPointArn context, and some (e.g. ListMountTargets) do
					// not support resource-level scoping, so Resource must be "*" — this
					// mirrors the AWS managed AmazonS3FilesReadOnlyAccess policy, which
					// also grants s3files:Get*/List* on "*". All actions are non-mutating.
					actions: ["s3files:Get*", "s3files:List*"],
					resources: ["*"],
				}),
			);
		}

		// Environment variables for the runtime
		const envVars: { [key: string]: string } = {
			AWS_REGION: stack.region,
			AWS_DEFAULT_REGION: stack.region,
			MEMORY_ID: memoryId,
			STACK_NAME: config.stack_name_base,
			GATEWAY_CREDENTIAL_PROVIDER_NAME: `${config.stack_name_base}-runtime-gateway-auth`, // Used by @requires_access_token decorator to look up the correct provider
			// Controls whether the agent activates long-term semantic memory retrieval.
			// The memory resource always includes the SemanticMemoryStrategy (no cost to define it),
			// but retrieval is only performed when this is "true". See config.yaml: use_long_term_memory.
			USE_LONG_TERM_MEMORY: config.backend.use_long_term_memory
				? "true"
				: "false",
			// Retrieval tuning for long-term memory. Only used when USE_LONG_TERM_MEMORY is "true".
			// See config.yaml: ltm_top_k and ltm_relevance_score.
			LTM_TOP_K: String(config.backend.ltm_top_k),
			LTM_RELEVANCE_SCORE: String(config.backend.ltm_relevance_score),
			// User-selectable LLMs. MODEL_MAP is the "logical key ->
			// { id, provider }" resolution map (available models only), DEFAULT_MODEL_KEY
			// is the logical key used when the request omits one. Both are derived from
			// the single source of truth in utils/model-registry.ts, so the backend
			// resolver and the frontend picker (aws-exports.json) cannot drift.
			MODEL_MAP: modelMapJson(),
			DEFAULT_MODEL_KEY: defaultModelKey(),
		};

		// Tell the agent where skills are mounted (read by the AgentSkills plugin).
		if (this.skillsAccessPointArn) {
			envVars["SKILLS_MOUNT_PATH"] = skillsMountPath;
		}

		// Add claude-agent-sdk specific environment variable
		if (
			pattern === "claude-agent-sdk-single-agent" ||
			pattern === "claude-agent-sdk-multi-agent"
		) {
			envVars["CLAUDE_CODE_USE_BEDROCK"] = "1";
		}

		// Create the runtime using L2 construct
		// requestHeaderConfiguration allows the agent to read the Authorization header
		// from RequestContext.request_headers, which is needed to securely extract the
		// user ID from the validated JWT token (sub claim) instead of trusting the payload body.
		this.agentRuntime = new agentcore.Runtime(this, "Runtime", {
			runtimeName: `${config.stack_name_base.replace(/-/g, "_")}_${this.agentName.valueAsString}`,
			agentRuntimeArtifact: agentRuntimeArtifact,
			executionRole: agentRole,
			networkConfiguration: networkConfiguration,
			protocolConfiguration: agentcore.ProtocolType.HTTP,
			environmentVariables: envVars,
			authorizerConfiguration: authorizerConfiguration,
			requestHeaderConfiguration: {
				allowlistedHeaders: ["Authorization"],
			},
			lifecycleConfiguration: this.buildLifecycleConfiguration(config),
			description: `${pattern} agent runtime for ${config.stack_name_base}`,
		});

		// AGUI protocol override — CloudFormation doesn't support AGUI enum yet
		// (only MCP | HTTP | A2A). Runtime deploys as HTTP, which also works properly.
		// if (pattern.startsWith("agui-")) {
		//   const cfnRuntime = this.agentRuntime.node.defaultChild as cdk.CfnResource
		//   cfnRuntime.addPropertyOverride("ProtocolConfiguration", "AGUI")
		// }

		// Mount the S3 Files skills access point. The L2 Runtime props still do
		// not surface filesystemConfigurations, so set it on the underlying L1.
		// CfnRuntime exposes filesystemConfigurations as a typed setter, so we
		// assign the strongly-typed FilesystemConfigurationProperty directly
		// rather than injecting raw CloudFormation via addPropertyOverride —
		// property names are checked at compile time.
		if (this.skillsAccessPointArn) {
			const cfnRuntime = this.agentRuntime.node
				.defaultChild as agentcore.CfnRuntime;
			const filesystemConfigurations: agentcore.CfnRuntime.FilesystemConfigurationProperty[] =
				[
					{
						s3FilesAccessPoint: {
							accessPointArn: this.skillsAccessPointArn,
							mountPath: skillsMountPath,
						},
					},
				];
			cfnRuntime.filesystemConfigurations = filesystemConfigurations;
		}

		// Make sure that ZIP is uploaded before Runtime is created
		if (zipPackagerResource) {
			this.agentRuntime.node.addDependency(zipPackagerResource);
		}

		// Store the runtime ARN
		this.runtimeArn = this.agentRuntime.agentRuntimeArn;

		// Outputs
		new cdk.CfnOutput(this, "AgentRuntimeId", {
			description: "ID of the created agent runtime",
			value: this.agentRuntime.agentRuntimeId,
		});

		new cdk.CfnOutput(this, "AgentRuntimeArn", {
			description: "ARN of the created agent runtime",
			value: this.agentRuntime.agentRuntimeArn,
			exportName: `${config.stack_name_base}-AgentRuntimeArn`,
		});

		new cdk.CfnOutput(this, "AgentRoleArn", {
			description: "ARN of the agent execution role",
			value: agentRole.roleArn,
		});

		// Memory ARN output
		new cdk.CfnOutput(this, "MemoryArn", {
			description: "ARN of the agent memory resource",
			value: memoryArn,
		});
	}

	private createLtmMcpServerTarget(config: AppConfig): void {
		// ========================================
		// LTM MCP Server on AgentCore Runtime + Gateway MCP Server Target
		// ========================================
		// Hosts gateway/tools/ltm_mcp_server (a single-tool FastMCP server that
		// lists the caller's long-term memory facts) on a second AgentCore
		// Runtime with protocol MCP and IAM (SigV4) inbound auth, and registers
		// it as an MCP server target of the gateway. This replaces
		// the in-process Strands tool.
		//
		// Actor identity: the Gateway does not forward inbound JWT claims to
		// MCP server targets, so the agent runtime's MCP client attaches the
		// user id as the X-Amzn-Bedrock-AgentCore-Runtime-Custom-Actor-Id
		// header (the only header prefix AgentCore propagates to runtime
		// containers). It must be allowlisted twice on this path: the target's
		// metadataConfiguration.allowedRequestHeaders (Gateway -> MCP server)
		// and the runtime's allowlistedHeaders (service -> container).
		const actorIdHeader = "X-Amzn-Bedrock-AgentCore-Runtime-Custom-Actor-Id";

		const ltmArtifact = agentcore.AgentRuntimeArtifact.fromAsset(
			path.join(__dirname, "../../gateway/tools/ltm_mcp_server"), // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal
			{
				platform: ecr_assets.Platform.LINUX_ARM64,
			},
		);

		const ltmRole = new AgentCoreRole(this, "LtmMcpRuntimeRole");
		// The only data-plane call the server makes: a no-query listing of the
		// actor's /facts namespace.
		ltmRole.addToPolicy(
			new iam.PolicyStatement({
				sid: "LtmMemoryListAccess",
				effect: iam.Effect.ALLOW,
				actions: ["bedrock-agentcore:ListMemoryRecords"],
				resources: [this.memoryArn],
			}),
		);

		// Same network placement as the agent runtime: the shared SG is passed
		// through, so usingVpc reuses it instead of creating a new construct
		// (which would collide with the agent runtime's). The MCP server's only
		// egress is the bedrock-agentcore data plane, reachable through the
		// closed VPC's existing endpoint.
		const ltmNetworkConfiguration =
			this.vpc && this.runtimeSecurityGroup
				? agentcore.RuntimeNetworkConfiguration.usingVpc(this, {
						vpc: this.vpc,
						vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
						securityGroups: [this.runtimeSecurityGroup],
					})
				: agentcore.RuntimeNetworkConfiguration.usingPublicNetwork();

		// No authorizerConfiguration: the runtime defaults to IAM (SigV4)
		// inbound auth, which is what the gateway's outbound
		// GATEWAY_IAM_ROLE credential provider speaks.
		const ltmRuntime = new agentcore.Runtime(this, "LtmMcpRuntime", {
			runtimeName: `${config.stack_name_base.replace(/-/g, "_")}_ltm_mcp`,
			agentRuntimeArtifact: ltmArtifact,
			executionRole: ltmRole,
			networkConfiguration: ltmNetworkConfiguration,
			protocolConfiguration: agentcore.ProtocolType.MCP,
			environmentVariables: {
				AWS_REGION: this.region,
				AWS_DEFAULT_REGION: this.region,
				MEMORY_ID: this.memoryId,
			},
			requestHeaderConfiguration: {
				allowlistedHeaders: [actorIdHeader],
			},
			lifecycleConfiguration: this.buildLifecycleConfiguration(config),
			description: `LTM listing MCP server for ${config.stack_name_base}`,
		});

		// MCP servers hosted on AgentCore Runtime are addressed through the
		// service's invocations URL with the runtime ARN URL-encoded into the
		// path. The ARN is a deploy-time token, so encode its ':' and '/' with
		// Fn.split/Fn.join rather than encodeURIComponent.
		const encodedArn = cdk.Fn.join(
			"%2F",
			cdk.Fn.split(
				"/",
				cdk.Fn.join("%3A", cdk.Fn.split(":", ltmRuntime.agentRuntimeArn)),
			),
		);
		const ltmMcpEndpoint = `https://bedrock-agentcore.${this.region}.amazonaws.com/runtimes/${encodedArn}/invocations?qualifier=DEFAULT`;

		// The gateway signs its outbound calls with its own role, so it needs
		// permission to invoke the hosting runtime. Unlike the managed AWS MCP
		// Server target (which needs no explicit invoke permission), this is a
		// customer runtime and InvokeAgentRuntime IS evaluated.
		this.gatewayRole.addToPolicy(
			new iam.PolicyStatement({
				sid: "InvokeLtmMcpRuntime",
				effect: iam.Effect.ALLOW,
				actions: ["bedrock-agentcore:InvokeAgentRuntime"],
				resources: [
					ltmRuntime.agentRuntimeArn,
					`${ltmRuntime.agentRuntimeArn}/*`,
				],
			}),
		);

		const ltmTarget = new agentcore.CfnGatewayTarget(
			this,
			"LtmMcpServerTarget",
			{
				gatewayIdentifier: this.gateway.attrGatewayIdentifier,
				name: "ltm-mcp",
				description:
					"Long-term memory listing MCP server hosted on AgentCore Runtime (SigV4)",
				targetConfiguration: {
					mcp: {
						mcpServer: {
							endpoint: ltmMcpEndpoint,
						},
					},
				},
				credentialProviderConfigurations: [
					{
						credentialProviderType: "GATEWAY_IAM_ROLE",
						credentialProvider: {
							iamCredentialProvider: {
								// SigV4 scoped to the bedrock-agentcore service —
								// the documented value for MCP servers hosted on
								// AgentCore Runtime (the aws-mcp target's
								// "aws-mcp" scope is specific to that service).
								service: "bedrock-agentcore",
							},
						},
					},
				],
				metadataConfiguration: {
					allowedRequestHeaders: [actorIdHeader],
				},
			},
		);

		// Target creation triggers a tools/list against the MCP server, so the
		// runtime must be READY and the gateway role's DefaultPolicy (with
		// InvokeAgentRuntime) attached first — the same ordering the aws-mcp
		// target needs for its IAM statements.
		ltmTarget.addDependency(this.gateway);
		ltmTarget.node.addDependency(ltmRuntime);
		const ltmGatewayRoleDefaultPolicy = this.gatewayRole.node.findChild(
			"DefaultPolicy",
		).node.defaultChild as cdk.CfnResource;
		ltmTarget.node.addDependency(ltmGatewayRoleDefaultPolicy);

		// 04-ltm-mcp.cedar references ltm-mcp___list_long_term_memories, and
		// CreatePolicy validates statements against the schema generated from
		// the gateway's existing targets — so the policies must be (re)created
		// after this target exists.
		this.gatewayCedarPolicy.node.addDependency(ltmTarget);

		new cdk.CfnOutput(this, "LtmMcpRuntimeArn", {
			description: "ARN of the LTM MCP server runtime",
			value: ltmRuntime.agentRuntimeArn,
		});

		new cdk.CfnOutput(this, "LtmMcpTargetId", {
			description: "AgentCore Gateway Target ID for the LTM MCP server",
			value: ltmTarget.ref,
		});
	}

	private createStrandsMcpServerTarget(config: AppConfig): void {
		// ========================================
		// Strands Agents docs MCP Server on AgentCore Runtime + Gateway Target
		// ========================================
		// Hosts gateway/tools/strands_mcp_server (the upstream open-source
		// strands-agents-mcp-server, wrapped for streamable HTTP) on its own
		// AgentCore Runtime with protocol MCP and IAM (SigV4) inbound auth, and
		// registers it as an MCP server target of the gateway. This
		// gives the agent Strands Agents documentation search/fetch via the
		// gateway, alongside the aws-mcp and ltm-mcp targets.
		//
		// No actor identity is propagated: unlike the LTM target, these tools
		// (search_docs, fetch_doc) are not scoped to a user — they query the
		// public Strands docs site — so neither metadataConfiguration nor a
		// requestHeaderConfiguration custom header is needed.
		const strandsArtifact = agentcore.AgentRuntimeArtifact.fromAsset(
			path.join(__dirname, "../../gateway/tools/strands_mcp_server"), // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal
			{
				platform: ecr_assets.Platform.LINUX_ARM64,
			},
		);

		// Default AgentCoreRole is sufficient: this server makes no AWS
		// data-plane calls (it only reaches strandsagents.com over HTTPS), so
		// no extra addToPolicy is needed.
		const strandsRole = new AgentCoreRole(this, "StrandsMcpRuntimeRole");

		// PUBLIC network, always — even when the rest of the stack runs in the
		// closed VPC. The server's egress is outbound HTTPS to
		// strandsagents.com, which the NAT-less VPC cannot reach, so this
		// runtime is placed on the AgentCore managed public network instead.
		// The VPC and its closed-network design are left untouched.
		const strandsNetworkConfiguration =
			agentcore.RuntimeNetworkConfiguration.usingPublicNetwork();

		// No authorizerConfiguration: the runtime defaults to IAM (SigV4)
		// inbound auth, which is what the gateway's outbound GATEWAY_IAM_ROLE
		// credential provider speaks.
		const strandsRuntime = new agentcore.Runtime(this, "StrandsMcpRuntime", {
			runtimeName: `${config.stack_name_base.replace(/-/g, "_")}_strands_mcp`,
			agentRuntimeArtifact: strandsArtifact,
			executionRole: strandsRole,
			networkConfiguration: strandsNetworkConfiguration,
			protocolConfiguration: agentcore.ProtocolType.MCP,
			environmentVariables: {
				AWS_REGION: this.region,
				AWS_DEFAULT_REGION: this.region,
			},
			lifecycleConfiguration: this.buildLifecycleConfiguration(config),
			description: `Strands Agents docs MCP server for ${config.stack_name_base}`,
		});

		// MCP servers hosted on AgentCore Runtime are addressed through the
		// service's invocations URL with the runtime ARN URL-encoded into the
		// path. The ARN is a deploy-time token, so encode its ':' and '/' with
		// Fn.split/Fn.join rather than encodeURIComponent.
		const encodedArn = cdk.Fn.join(
			"%2F",
			cdk.Fn.split(
				"/",
				cdk.Fn.join("%3A", cdk.Fn.split(":", strandsRuntime.agentRuntimeArn)),
			),
		);
		const strandsMcpEndpoint = `https://bedrock-agentcore.${this.region}.amazonaws.com/runtimes/${encodedArn}/invocations?qualifier=DEFAULT`;

		// The gateway signs its outbound calls with its own role, so it needs
		// permission to invoke the hosting runtime (the same as the ltm-mcp
		// target — a customer runtime where InvokeAgentRuntime IS evaluated,
		// unlike the managed aws-mcp target).
		this.gatewayRole.addToPolicy(
			new iam.PolicyStatement({
				sid: "InvokeStrandsMcpRuntime",
				effect: iam.Effect.ALLOW,
				actions: ["bedrock-agentcore:InvokeAgentRuntime"],
				resources: [
					strandsRuntime.agentRuntimeArn,
					`${strandsRuntime.agentRuntimeArn}/*`,
				],
			}),
		);

		const strandsTarget = new agentcore.CfnGatewayTarget(
			this,
			"StrandsMcpServerTarget",
			{
				gatewayIdentifier: this.gateway.attrGatewayIdentifier,
				name: "strands-mcp",
				description:
					"Strands Agents documentation MCP server hosted on AgentCore Runtime (SigV4)",
				targetConfiguration: {
					mcp: {
						mcpServer: {
							endpoint: strandsMcpEndpoint,
						},
					},
				},
				credentialProviderConfigurations: [
					{
						credentialProviderType: "GATEWAY_IAM_ROLE",
						credentialProvider: {
							iamCredentialProvider: {
								// SigV4 scoped to the bedrock-agentcore service —
								// the documented value for MCP servers hosted on
								// AgentCore Runtime (same as the ltm-mcp target).
								service: "bedrock-agentcore",
							},
						},
					},
				],
			},
		);

		// Target creation triggers a tools/list against the MCP server, so the
		// runtime must be READY and the gateway role's DefaultPolicy (with
		// InvokeAgentRuntime) attached first — the same ordering the ltm-mcp
		// and aws-mcp targets need for their IAM statements.
		strandsTarget.addDependency(this.gateway);
		strandsTarget.node.addDependency(strandsRuntime);
		const strandsGatewayRoleDefaultPolicy = this.gatewayRole.node.findChild(
			"DefaultPolicy",
		).node.defaultChild as cdk.CfnResource;
		strandsTarget.node.addDependency(strandsGatewayRoleDefaultPolicy);

		// 05-strands-mcp.cedar references strands-mcp___search_docs and
		// strands-mcp___fetch_doc, and CreatePolicy validates statements
		// against the schema generated from the gateway's existing targets —
		// so the policies must be (re)created after this target exists.
		this.gatewayCedarPolicy.node.addDependency(strandsTarget);

		new cdk.CfnOutput(this, "StrandsMcpRuntimeArn", {
			description: "ARN of the Strands docs MCP server runtime",
			value: strandsRuntime.agentRuntimeArn,
		});

		new cdk.CfnOutput(this, "StrandsMcpTargetId", {
			description:
				"AgentCore Gateway Target ID for the Strands docs MCP server",
			value: strandsTarget.ref,
		});
	}

	private createWebSearchTarget(config: AppConfig): void {
		// ========================================
		// AgentCore Web Search connector (built-in) Gateway Target
		// ========================================
		// Registers the managed Web Search tool (GA in 2026) as a gateway
		// connector target. The agent then discovers a "WebSearch" tool via
		// tools/list and grounds answers in current web results, with zero
		// data egress — queries are served entirely inside
		// AWS, so no third-party search API (Tavily, Brave) or outbound
		// credential is needed.
		//
		// Unlike the ltm-mcp / strands-mcp targets, this is NOT an MCP server
		// hosted on a Runtime — it is a built-in connector identified by
		// connectorId "web-search". There is no endpoint, Runtime, or network
		// configuration: the bedrock-agentcore service hosts the backend.
		//
		// IMPORTANT — L1 type gap: aws-cdk-lib 2.257.0's
		// CfnGatewayTarget L1 has no `connector` member on its
		// TargetConfiguration.Mcp type (it only knows lambda / mcpServer /
		// openApiSchema / smithyModel / apiGateway). The connector shape DOES
		// exist in the live CloudFormation registry schema for
		// AWS::BedrockAgentCore::GatewayTarget (verified with
		// `aws cloudformation describe-type`: McpTargetConfiguration's oneOf
		// includes Connector). So we satisfy the L1 type with an empty `mcp: {}`
		// placeholder and inject the real Connector block via
		// addPropertyOverride — CloudFormation processes it correctly server
		// side even though the TypeScript types lag the GA.

		// Web Search availability is us-east-1 only (2026-06). The project is
		// pinned to us-east-1, so this is a guard against accidental reuse in
		// another region rather than an expected branch.
		if (this.region !== "us-east-1") {
			throw new Error(
				`AgentCore Web Search is only available in us-east-1 (got ${this.region}). ` +
					"Remove createWebSearchTarget() or wait for regional expansion.",
			);
		}

		// Web Search requires two permissions on the Gateway service role
		// (AWS docs: "Configure the Gateway Service Role"):
		//   - InvokeGateway on the gateway ARN — Web Search invocations are
		//     authorized as InvokeGateway (distinct from the cedar-policy
		//     Lambda's InvokeGateway permission, which is for policy schema
		//     validation; this one is on the gateway execution role).
		//   - InvokeWebSearch on the service-owned tool ARN, checked per
		//     request. The ARN's "account" segment is the literal "aws" (an
		//     AWS-owned resource, not this account).
		this.gatewayRole.addToPolicy(
			new iam.PolicyStatement({
				sid: "InvokeGatewayForWebSearch",
				effect: iam.Effect.ALLOW,
				actions: ["bedrock-agentcore:InvokeGateway"],
				resources: [
					`arn:aws:bedrock-agentcore:${this.region}:${this.account}:gateway/*`,
				],
			}),
		);
		this.gatewayRole.addToPolicy(
			new iam.PolicyStatement({
				sid: "InvokeWebSearchTool",
				effect: iam.Effect.ALLOW,
				actions: ["bedrock-agentcore:InvokeWebSearch"],
				resources: [
					`arn:aws:bedrock-agentcore:${this.region}:aws:tool/web-search.v1`,
				],
			}),
		);

		const webSearchTarget = new agentcore.CfnGatewayTarget(
			this,
			"WebSearchTarget",
			{
				gatewayIdentifier: this.gateway.attrGatewayIdentifier,
				name: "web-search-tool",
				description:
					"AgentCore managed Web Search connector (zero data egress, us-east-1)",
				// Placeholder to satisfy the L1 type; the real Connector block is
				// injected via addPropertyOverride below (the L1 type predates the
				// connector GA — see the note above).
				targetConfiguration: { mcp: {} },
				credentialProviderConfigurations: [
					{
						credentialProviderType: "GATEWAY_IAM_ROLE",
					},
				],
			},
		);

		// Inject the connector configuration as raw CloudFormation. Property
		// names are PascalCase (CloudFormation casing), not the camelCase of the
		// boto3/SDK shape. ParameterValues is left empty: no domain filtering
		// (parameterValues.domainFilter.exclude) is configured for the demo.
		webSearchTarget.addPropertyOverride("TargetConfiguration.Mcp", {
			Connector: {
				Source: { ConnectorId: "web-search" },
				Configurations: [{ Name: "WebSearch", ParameterValues: {} }],
			},
		});

		// Same ordering the other targets need: the gateway must exist and the
		// gateway role's DefaultPolicy (now carrying InvokeGateway /
		// InvokeWebSearch) must be attached before the target is created, since
		// target creation validates the connector against the service.
		webSearchTarget.addDependency(this.gateway);
		const gatewayRoleDefaultPolicy = this.gatewayRole.node.findChild(
			"DefaultPolicy",
		).node.defaultChild as cdk.CfnResource;
		webSearchTarget.node.addDependency(gatewayRoleDefaultPolicy);

		// 06-web-search.cedar references web-search-tool___WebSearch, and
		// CreatePolicy validates statements against the schema generated from
		// the gateway's existing targets — so the policies must be (re)created
		// after this target exists (same reason as the ltm-mcp / strands-mcp
		// targets, validated at deploy time).
		this.gatewayCedarPolicy.node.addDependency(webSearchTarget);

		new cdk.CfnOutput(this, "WebSearchTargetId", {
			description: "AgentCore Gateway Target ID for the Web Search connector",
			value: webSearchTarget.ref,
		});
	}

	private createRuntimeSSMParameters(config: AppConfig): void {
		// Store runtime ARN in SSM for frontend stack
		new ssm.StringParameter(this, "RuntimeArnParam", {
			parameterName: `/${config.stack_name_base}/runtime-arn`,
			stringValue: this.runtimeArn,
		});
	}

	private createCognitoSSMParameters(config: AppConfig): void {
		// Store Cognito configuration in SSM for testing and frontend access
		new ssm.StringParameter(this, "CognitoUserPoolIdParam", {
			parameterName: `/${config.stack_name_base}/cognito-user-pool-id`,
			stringValue: this.userPoolId,
			description: "Cognito User Pool ID",
		});

		new ssm.StringParameter(this, "CognitoUserPoolClientIdParam", {
			parameterName: `/${config.stack_name_base}/cognito-user-pool-client-id`,
			stringValue: this.userPoolClientId,
			description: "Cognito User Pool Client ID",
		});

		new ssm.StringParameter(this, "MachineClientIdParam", {
			parameterName: `/${config.stack_name_base}/machine_client_id`,
			stringValue: this.machineClient.userPoolClientId,
			description: "Machine Client ID for M2M authentication",
		});

		// Use the correct Cognito domain format from the passed domain
		new ssm.StringParameter(this, "CognitoDomainParam", {
			parameterName: `/${config.stack_name_base}/cognito_provider`,
			stringValue: `${this.userPoolDomain.domainName}.auth.${cdk.Aws.REGION}.amazoncognito.com`,
			description: "Cognito domain URL for token endpoint",
		});
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
		});

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
		});

		return feedbackTable;
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
		feedbackTable: dynamodb.Table,
	): void {
		// Create Lambda function for feedback using Python
		// ARM_64 required — matches Powertools ARM64 layer and avoids cross-platform
		const feedbackLambda = new PythonFunction(this, "FeedbackLambda", {
			functionName: `${config.stack_name_base}-feedback`,
			runtime: lambda.Runtime.PYTHON_3_13,
			architecture: lambda.Architecture.ARM_64,
			entry: path.join(__dirname, "..", "lambdas", "feedback"), // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal
			handler: "handler",
			// Closed network: placed in the same isolated VPC as the
			// Runtime so DynamoDB traffic goes through the VPC endpoints, not the
			// public internet. No-op in PUBLIC mode (this.vpc undefined).
			...this.closedNetworkLambdaProps(),
			environment: {
				TABLE_NAME: feedbackTable.tableName,
				CORS_ALLOWED_ORIGINS: `${frontendUrl},http://localhost:3000`,
			},
			timeout: cdk.Duration.seconds(30),
			layers: [
				lambda.LayerVersion.fromLayerVersionArn(
					this,
					"PowertoolsLayer",
					`arn:aws:lambda:${
						cdk.Stack.of(this).region
					}:017000801446:layer:AWSLambdaPowertoolsPythonV3-python313-arm64:18`,
				),
			],
			logGroup: new logs.LogGroup(this, "FeedbackLambdaLogGroup", {
				logGroupName: `/aws/lambda/${config.stack_name_base}-feedback`,
				retention: logs.RetentionDays.ONE_WEEK,
				removalPolicy: cdk.RemovalPolicy.DESTROY,
			}),
		});

		// Grant Lambda permissions to write to DynamoDB
		feedbackTable.grantWriteData(feedbackLambda);

		/*
		 * CORS TODO: Wildcard (*) used because Backend deploys before Frontend in nested stack order.
		 * For Lambda proxy integrations, the Lambda's ALLOWED_ORIGINS env var is the primary CORS control.
		 * API Gateway defaultCorsPreflightOptions below only handles OPTIONS preflight requests.
		 * See detailed explanation and fix options in: infra-cdk/lambdas/feedback/index.py
		 */
		const api = new apigateway.RestApi(this, "FeedbackApi", {
			restApiName: `${config.stack_name_base}-api`,
			description: "API for user feedback and future endpoints",
			defaultCorsPreflightOptions: {
				allowOrigins: [frontendUrl, "http://localhost:3000"],
				allowMethods: ["POST", "OPTIONS"],
				allowHeaders: ["Content-Type", "Authorization"],
			},
			deployOptions: {
				stageName: "prod",
				throttlingRateLimit: 100,
				throttlingBurstLimit: 200,
				cachingEnabled: true,
				cacheDataEncrypted: true,
				cacheClusterEnabled: true,
				cacheClusterSize: "0.5",
				cacheTtl: cdk.Duration.minutes(5),
				loggingLevel: apigateway.MethodLoggingLevel.INFO,
				dataTraceEnabled: true,
				metricsEnabled: true,
				accessLogDestination: new apigateway.LogGroupLogDestination(
					new logs.LogGroup(this, "FeedbackApiAccessLogGroup", {
						logGroupName: `/aws/apigateway/${config.stack_name_base}-api-access`,
						retention: logs.RetentionDays.ONE_WEEK,
						removalPolicy: cdk.RemovalPolicy.DESTROY,
					}),
				),
				accessLogFormat: apigateway.AccessLogFormat.jsonWithStandardFields(),
				tracingEnabled: true,
			},
		});

		// Add request validator for API security
		const requestValidator = new apigateway.RequestValidator(
			this,
			"FeedbackApiRequestValidator",
			{
				restApi: api,
				requestValidatorName: `${config.stack_name_base}-request-validator`,
				validateRequestBody: true,
				validateRequestParameters: true,
			},
		);

		// Create Cognito authorizer
		const authorizer = new apigateway.CognitoUserPoolsAuthorizer(
			this,
			"FeedbackApiAuthorizer",
			{
				cognitoUserPools: [this.userPool],
				identitySource: "method.request.header.Authorization",
				authorizerName: `${config.stack_name_base}-authorizer`,
			},
		);

		// Create /feedback resource and POST method
		const feedbackResource = api.root.addResource("feedback");
		feedbackResource.addMethod(
			"POST",
			new apigateway.LambdaIntegration(feedbackLambda),
			{
				authorizer,
				authorizationType: apigateway.AuthorizationType.COGNITO,
				requestValidator: requestValidator,
			},
		);

		// Store the API URL for access from main stack
		this.feedbackApiUrl = api.url;

		// Store API URL in SSM for frontend
		new ssm.StringParameter(this, "FeedbackApiUrlParam", {
			parameterName: `/${config.stack_name_base}/feedback-api-url`,
			stringValue: api.url,
			description: "Feedback API Gateway URL",
		});
	}

	/**
	 * Creates the Chat History API.
	 *
	 * Two Lambdas behind one RestApi with a Cognito authorizer, mirroring the
	 * Feedback API pattern:
	 *  - History Lambda  (GET /history?sessionId): restores a session's message
	 *    body from AgentCore Memory short-term events (list_events, reversed to
	 *    chronological order). actorId is the validated JWT sub, never the body.
	 *  - Sessions Lambda (GET/POST /sessions): maintains the per-user session
	 *    "table of contents" in DynamoDB and generates blog-style titles via
	 *    Bedrock Haiku (Converse) on first turn.
	 *
	 * In VPC mode both Lambdas sit in the same isolated VPC as the Runtime and
	 * reach AgentCore Memory / Bedrock / DynamoDB through the VPC endpoints, so
	 * the data plane stays in the closed network. Caching is disabled
	 * because history grows during a conversation and must not be stale.
	 */
	private createHistoryApi(config: AppConfig, frontendUrl: string): void {
		// Haiku model for title generation. Inference profile (cross-region)
		// matches the Sonnet model-id convention used by basic_agent.py.
		const haikuModelId = "us.anthropic.claude-haiku-4-5-20251001-v1:0";

		// Per-user "table of contents" of chat sessions. PK partitions by user
		// (Cognito sub); SK is "<createdAt_iso>#<sessionId>" so a Query with
		// ScanIndexForward=false returns newest-first in a single call (no GSI).
		const sessionsTable = new dynamodb.Table(this, "ChatSessionsTable", {
			tableName: `${config.stack_name_base}-chat-sessions`,
			partitionKey: { name: "pk", type: dynamodb.AttributeType.STRING },
			sortKey: { name: "sk", type: dynamodb.AttributeType.STRING },
			billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
			removalPolicy: cdk.RemovalPolicy.DESTROY,
			pointInTimeRecoverySpecification: {
				pointInTimeRecoveryEnabled: true,
			},
			encryption: dynamodb.TableEncryption.AWS_MANAGED,
		});

		const powertoolsLayer = lambda.LayerVersion.fromLayerVersionArn(
			this,
			"HistoryPowertoolsLayer",
			`arn:aws:lambda:${
				cdk.Stack.of(this).region
			}:017000801446:layer:AWSLambdaPowertoolsPythonV3-python313-arm64:18`,
		);

		// History Lambda: restores a session body from AgentCore Memory.
		const historyLambda = new PythonFunction(this, "HistoryLambda", {
			functionName: `${config.stack_name_base}-history`,
			runtime: lambda.Runtime.PYTHON_3_13,
			architecture: lambda.Architecture.ARM_64,
			entry: path.join(__dirname, "..", "lambdas", "history"), // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal
			handler: "handler",
			// Closed network: reaches the AgentCore Memory data plane
			// (list_events) via the bedrock-agentcore VPC endpoint. No-op in PUBLIC mode.
			...this.closedNetworkLambdaProps(),
			environment: {
				MEMORY_ID: this.memoryId,
				CORS_ALLOWED_ORIGINS: `${frontendUrl},http://localhost:3000`,
			},
			timeout: cdk.Duration.seconds(30),
			layers: [powertoolsLayer],
			logGroup: new logs.LogGroup(this, "HistoryLambdaLogGroup", {
				logGroupName: `/aws/lambda/${config.stack_name_base}-history`,
				retention: logs.RetentionDays.ONE_WEEK,
				removalPolicy: cdk.RemovalPolicy.DESTROY,
			}),
		});

		// Sessions Lambda: maintains the DynamoDB index + Haiku title generation.
		const sessionsLambda = new PythonFunction(this, "SessionsLambda", {
			functionName: `${config.stack_name_base}-sessions`,
			runtime: lambda.Runtime.PYTHON_3_13,
			architecture: lambda.Architecture.ARM_64,
			entry: path.join(__dirname, "..", "lambdas", "sessions"), // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal
			handler: "handler",
			// Closed network: DynamoDB via the gateway endpoint and Haiku
			// (cross-region inference profile) via the bedrock-runtime VPC endpoint.
			// No-op in PUBLIC mode.
			...this.closedNetworkLambdaProps(),
			environment: {
				TABLE_NAME: sessionsTable.tableName,
				HAIKU_MODEL_ID: haikuModelId,
				CORS_ALLOWED_ORIGINS: `${frontendUrl},http://localhost:3000`,
			},
			timeout: cdk.Duration.seconds(30),
			layers: [powertoolsLayer],
			logGroup: new logs.LogGroup(this, "SessionsLambdaLogGroup", {
				logGroupName: `/aws/lambda/${config.stack_name_base}-sessions`,
				retention: logs.RetentionDays.ONE_WEEK,
				removalPolicy: cdk.RemovalPolicy.DESTROY,
			}),
		});

		// History Lambda reads short-term events from the agent's memory.
		historyLambda.addToRolePolicy(
			new iam.PolicyStatement({
				sid: "MemoryListEventsAccess",
				effect: iam.Effect.ALLOW,
				actions: [
					"bedrock-agentcore:ListSessions",
					"bedrock-agentcore:ListEvents",
					"bedrock-agentcore:GetEvent",
				],
				resources: [this.memoryArn],
			}),
		);

		// Sessions Lambda reads/writes the index table and invokes Haiku for titles.
		sessionsTable.grantReadWriteData(sessionsLambda);
		sessionsLambda.addToRolePolicy(
			new iam.PolicyStatement({
				sid: "HaikuTitleGeneration",
				effect: iam.Effect.ALLOW,
				actions: ["bedrock:InvokeModel"],
				// Converse via a cross-region inference profile fans out to the
				// regional foundation models, so both ARNs must be permitted.
				resources: [
					`arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0`,
					`arn:aws:bedrock:${this.region}:${this.account}:inference-profile/${haikuModelId}`,
				],
			}),
		);

		const api = new apigateway.RestApi(this, "HistoryApi", {
			restApiName: `${config.stack_name_base}-history-api`,
			description: "Chat history (session list + body restore) API",
			defaultCorsPreflightOptions: {
				allowOrigins: [frontendUrl, "http://localhost:3000"],
				allowMethods: ["GET", "POST", "OPTIONS"],
				allowHeaders: ["Content-Type", "Authorization"],
			},
			deployOptions: {
				stageName: "prod",
				throttlingRateLimit: 100,
				throttlingBurstLimit: 200,
				// Caching disabled: history changes as the conversation proceeds,
				// so a cached list/body would return stale results.
				cachingEnabled: false,
				loggingLevel: apigateway.MethodLoggingLevel.INFO,
				dataTraceEnabled: true,
				metricsEnabled: true,
				accessLogDestination: new apigateway.LogGroupLogDestination(
					new logs.LogGroup(this, "HistoryApiAccessLogGroup", {
						logGroupName: `/aws/apigateway/${config.stack_name_base}-history-api-access`,
						retention: logs.RetentionDays.ONE_WEEK,
						removalPolicy: cdk.RemovalPolicy.DESTROY,
					}),
				),
				accessLogFormat: apigateway.AccessLogFormat.jsonWithStandardFields(),
				tracingEnabled: true,
			},
		});

		const authorizer = new apigateway.CognitoUserPoolsAuthorizer(
			this,
			"HistoryApiAuthorizer",
			{
				cognitoUserPools: [this.userPool],
				identitySource: "method.request.header.Authorization",
				authorizerName: `${config.stack_name_base}-history-authorizer`,
			},
		);

		const cognitoMethodOptions: apigateway.MethodOptions = {
			authorizer,
			authorizationType: apigateway.AuthorizationType.COGNITO,
		};

		// GET /history?sessionId=...
		const historyResource = api.root.addResource("history");
		historyResource.addMethod(
			"GET",
			new apigateway.LambdaIntegration(historyLambda),
			cognitoMethodOptions,
		);

		// GET /sessions  and  POST /sessions
		const sessionsResource = api.root.addResource("sessions");
		sessionsResource.addMethod(
			"GET",
			new apigateway.LambdaIntegration(sessionsLambda),
			cognitoMethodOptions,
		);
		sessionsResource.addMethod(
			"POST",
			new apigateway.LambdaIntegration(sessionsLambda),
			cognitoMethodOptions,
		);

		// Store the API URL for the frontend stack and aws-exports.json.
		this.historyApiUrl = api.url;

		new ssm.StringParameter(this, "HistoryApiUrlParam", {
			parameterName: `/${config.stack_name_base}/history-api-url`,
			stringValue: api.url,
			description: "Chat History API Gateway URL",
		});

		// The parent stack (fast-main-stack.ts) re-surfaces this as a CfnOutput
		// named HistoryApiUrl — NestedStack CfnOutputs do not propagate, and
		// duplicating the exportName here would collide. SSM + the member var
		// (this.historyApiUrl) are the only exposure from the nested stack.
	}

	private createAgentCoreGateway(config: AppConfig): void {
		// Create sample tool Lambda
		const toolLambda = new lambda.Function(this, "SampleToolLambda", {
			runtime: lambda.Runtime.PYTHON_3_13,
			handler: "sample_tool_lambda.handler",
			code: lambda.Code.fromAsset(
				path.join(__dirname, "../../gateway/tools/sample_tool"),
			), // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal
			// Closed network: placed in the same isolated VPC as the
			// other Lambdas so every Lambda in the demo sits in the closed network.
			// The Gateway still invokes it via the Lambda Invoke API (no Gateway-side
			// VPC config needed). No-op in PUBLIC mode (this.vpc undefined).
			...this.closedNetworkLambdaProps(),
			timeout: cdk.Duration.seconds(30),
			logGroup: new logs.LogGroup(this, "SampleToolLambdaLogGroup", {
				logGroupName: `/aws/lambda/${config.stack_name_base}-sample-tool`,
				retention: logs.RetentionDays.ONE_WEEK,
				removalPolicy: cdk.RemovalPolicy.DESTROY,
			}),
		});

		// Create comprehensive IAM role for gateway
		const gatewayRole = new iam.Role(this, "GatewayRole", {
			assumedBy: new iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
			description: "Role for AgentCore Gateway with comprehensive permissions",
		});

		// Lambda invoke permission
		toolLambda.grantInvoke(gatewayRole);

		// Bedrock permissions (region-agnostic)
		gatewayRole.addToPolicy(
			new iam.PolicyStatement({
				effect: iam.Effect.ALLOW,
				actions: [
					"bedrock:InvokeModel",
					"bedrock:InvokeModelWithResponseStream",
				],
				resources: [
					"arn:aws:bedrock:*::foundation-model/*",
					`arn:aws:bedrock:*:${this.account}:inference-profile/*`,
				],
			}),
		);

		// SSM parameter access
		gatewayRole.addToPolicy(
			new iam.PolicyStatement({
				effect: iam.Effect.ALLOW,
				actions: ["ssm:GetParameter", "ssm:GetParameters"],
				resources: [
					`arn:aws:ssm:${this.region}:${this.account}:parameter/${config.stack_name_base}/*`,
				],
			}),
		);

		// Cognito permissions
		gatewayRole.addToPolicy(
			new iam.PolicyStatement({
				effect: iam.Effect.ALLOW,
				actions: [
					"cognito-idp:DescribeUserPoolClient",
					"cognito-idp:InitiateAuth",
				],
				resources: [this.userPool.userPoolArn],
			}),
		);

		// CloudWatch Logs
		gatewayRole.addToPolicy(
			new iam.PolicyStatement({
				effect: iam.Effect.ALLOW,
				actions: [
					"logs:CreateLogGroup",
					"logs:CreateLogStream",
					"logs:PutLogEvents",
				],
				resources: [
					`arn:aws:logs:${this.region}:${this.account}:log-group:/aws/bedrock-agentcore/*`,
				],
			}),
		);

		// Policy Engine access — required for the Gateway to verify and evaluate Cedar policies.
		// AuthorizeAction is needed on both the policy engine (to query policy decisions)
		// and the gateway itself (to apply those decisions to incoming requests).
		// CheckAuthorizePermissions uses a compound resource ARN format
		// (/policy-engines/{id}/target-resource/{gateway-arn}) requiring the /policy-engines/* pattern.
		gatewayRole.addToPolicy(
			new iam.PolicyStatement({
				effect: iam.Effect.ALLOW,
				actions: [
					"bedrock-agentcore:GetPolicyEngine",
					"bedrock-agentcore:AuthorizeAction",
					"bedrock-agentcore:PartiallyAuthorizeActions",
					"bedrock-agentcore:CheckAuthorizePermissions",
				],
				resources: [
					`arn:aws:bedrock-agentcore:${this.region}:${this.account}:policy-engine/*`,
					`arn:aws:bedrock-agentcore:${this.region}:${this.account}:gateway/*`,
					`arn:aws:bedrock-agentcore:${this.region}:${this.account}:/policy-engines/*`,
				],
			}),
		);

		// AWS MCP Server's aws___call_aws and aws___run_script tools execute AWS
		// CLI commands and Python scripts using the Gateway service role's
		// credentials. To make these tools meaningful in the demo (e.g.
		// "list my Lambda functions", "show me the S3 buckets"), attach the
		// AWS managed ReadOnlyAccess policy. This is intentionally a demo-scoped
		// relaxation: read-only operations cannot mutate AWS resources, so a
		// misfire (e.g. a user asking the agent to delete or stop something)
		// is naturally blocked at the AWS API layer.
		// Cedar Policy already restricts these tools to the finance department
		// (see gateway/policies/03-aws-mcp-destructive.cedar), so this is a
		// defence-in-depth: Cedar gates *who* can call the tool, ReadOnlyAccess
		// gates *what* the tool can do. ReadOnlyAccess is a demo convenience and
		// should be tightened to the minimum required actions in a non-demo
		// environment.
		gatewayRole.addManagedPolicy(
			iam.ManagedPolicy.fromAwsManagedPolicyName("ReadOnlyAccess"),
		);

		// Load tool specification from JSON file
		const toolSpecPath = path.join(
			__dirname,
			"../../gateway/tools/sample_tool/tool_spec.json",
		); // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal
		const apiSpec = JSON.parse(
			require("fs").readFileSync(toolSpecPath, "utf8"),
		);

		// Cognito OAuth2 configuration for gateway
		const cognitoIssuer = `https://cognito-idp.${this.region}.amazonaws.com/${this.userPool.userPoolId}`;
		const cognitoDiscoveryUrl = `${cognitoIssuer}/.well-known/openid-configuration`;

		// Create OAuth2 Credential Provider for AgentCore Runtime to authenticate with AgentCore Gateway
		// Uses cr.Provider pattern with explicit Lambda to avoid logging secrets in CloudWatch
		const providerName = `${config.stack_name_base}-runtime-gateway-auth`;

		// Lambda to create/delete OAuth2 provider
		const oauth2ProviderLambda = new lambda.Function(
			this,
			"OAuth2ProviderLambda",
			{
				runtime: lambda.Runtime.PYTHON_3_13,
				handler: "index.handler",
				code: lambda.Code.fromAsset(
					path.join(__dirname, "..", "lambdas", "oauth2-provider"),
				), // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal
				timeout: cdk.Duration.minutes(5),
				logGroup: new logs.LogGroup(this, "OAuth2ProviderLambdaLogGroup", {
					logGroupName: `/aws/lambda/${config.stack_name_base}-oauth2-provider`,
					retention: logs.RetentionDays.ONE_WEEK,
					removalPolicy: cdk.RemovalPolicy.DESTROY,
				}),
			},
		);

		// Grant Lambda permissions to read machine client secret
		this.machineClientSecret.grantRead(oauth2ProviderLambda);

		// Grant Lambda permissions for Bedrock AgentCore operations
		// OAuth2 Credential Provider operations - scoped to all providers in default Token Vault
		// Note: Need both vault-level and nested resource permissions because:
		// - CreateOauth2CredentialProvider checks permission on vault itself (token-vault/default)
		// - Also checks permission on the nested resource path (token-vault/default/oauth2credentialprovider/*)
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
			}),
		);

		// Token Vault operations - scoped to default vault
		// Note: Need both exact match (default) and wildcard (default/*) because:
		// - AWS checks permission on the vault container itself (token-vault/default)
		// - AWS also checks permission on resources inside (token-vault/default/*)
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
			}),
		);

		// Grant Lambda permissions for Token Vault secret management
		// Scoped to OAuth2 secrets in AgentCore Identity default namespace
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
			}),
		);

		// Create Custom Resource Provider
		const oauth2Provider = new cr.Provider(this, "OAuth2ProviderProvider", {
			onEventHandler: oauth2ProviderLambda,
		});

		// Create Custom Resource
		const runtimeCredentialProvider = new cdk.CustomResource(
			this,
			"RuntimeCredentialProvider",
			{
				serviceToken: oauth2Provider.serviceToken,
				properties: {
					ProviderName: providerName,
					ClientSecretArn: this.machineClientSecret.secretArn,
					DiscoveryUrl: cognitoDiscoveryUrl,
					ClientId: this.machineClient.userPoolClientId,
				},
			},
		);

		// Store for use in createAgentCoreRuntime()
		this.runtimeCredentialProvider = runtimeCredentialProvider;

		// Create Gateway using L1 construct (CfnGateway)
		// This replaces the Custom Resource approach with native CloudFormation support
		const gateway = new agentcore.CfnGateway(this, "AgentCoreGateway", {
			name: `${config.stack_name_base}-gateway`,
			roleArn: gatewayRole.roleArn,
			protocolType: "MCP",
			protocolConfiguration: {
				mcp: {
					supportedVersions: ["2025-03-26"],
					// Optional: Enable semantic search for tools
					// searchType: "SEMANTIC",
				},
			},
			authorizerType: "CUSTOM_JWT",
			authorizerConfiguration: {
				customJwtAuthorizer: {
					allowedClients: [this.machineClient.userPoolClientId],
					discoveryUrl: cognitoDiscoveryUrl,
				},
			},
			description: "AgentCore Gateway with MCP protocol and JWT authentication",
		});

		// Create Gateway Target using L1 construct (CfnGatewayTarget)
		const gatewayTarget = new agentcore.CfnGatewayTarget(
			this,
			"GatewayTarget",
			{
				gatewayIdentifier: gateway.attrGatewayIdentifier,
				name: "sample-tool-target",
				description: "Sample tool Lambda target",
				targetConfiguration: {
					mcp: {
						lambda: {
							lambdaArn: toolLambda.functionArn,
							toolSchema: {
								inlinePayload: apiSpec,
							},
						},
					},
				},
				credentialProviderConfigurations: [
					{
						credentialProviderType: "GATEWAY_IAM_ROLE",
					},
				],
			},
		);

		// Ensure proper creation order
		gatewayTarget.addDependency(gateway);
		gateway.node.addDependency(toolLambda);
		gateway.node.addDependency(this.machineClient);
		gateway.node.addDependency(gatewayRole);

		// Shared with createLtmMcpServerTarget, which attaches a
		// further MCP server target to this gateway.
		this.gateway = gateway;
		this.gatewayRole = gatewayRole;

		// ========================================
		// AWS MCP Server (Agent Toolkit for AWS) Target
		// ========================================
		// Register the managed AWS MCP Server (https://aws-mcp.<region>.api.aws/mcp)
		// as a Gateway MCP server target. Tools exposed by AWS MCP Server (e.g.
		// aws___list_regions, aws___read_documentation, aws___call_aws,
		// aws___run_script, aws___retrieve_skill) become callable through the same
		// MCP session as the existing sample-tool-target.
		//
		// Authorization: SigV4 with service name "aws-mcp" (the AWS MCP Server
		// validates SigV4 signatures scoped to the "aws-mcp" service, NOT
		// "execute-api"). Verified empirically against the live AWS MCP Server
		// — signing with "execute-api" returns:
		//   "Credential should be scoped to correct service: 'aws-mcp'"
		// (HTTP 401, JSON-RPC error -32001).
		//
		// No execute-api:Invoke IAM permission is needed on the Gateway role.
		// The endpoint is an AWS-managed URL (not a customer API Gateway), so
		// the bedrock-agentcore service handles the outbound call internally
		// and execute-api:Invoke is not evaluated. Verified empirically by
		// removing the previously-added InvokeAwsMcpServer Statement and
		// confirming tools/list and tools/call(aws s3 ls) still succeed.
		//
		// Cedar action names follow "<TargetName>___<tool_name>" so the policy
		// in gateway/policies/02-aws-mcp-read.cedar and 03-aws-mcp-destructive
		// .cedar permits aws-mcp___aws___* actions for the appropriate
		// departments — read tools allowed for finance/engineering, destructive
		// call_aws / run_script restricted to finance only.
		const awsMcpEndpoint = `https://aws-mcp.${this.region}.api.aws/mcp`;
		const awsMcpTarget = new agentcore.CfnGatewayTarget(
			this,
			"AwsMcpServerTarget",
			{
				gatewayIdentifier: gateway.attrGatewayIdentifier,
				name: "aws-mcp",
				description:
					"AWS MCP Server (Agent Toolkit for AWS) — full AWS knowledge & API tools via SigV4",
				targetConfiguration: {
					mcp: {
						mcpServer: {
							endpoint: awsMcpEndpoint,
						},
					},
				},
				credentialProviderConfigurations: [
					{
						credentialProviderType: "GATEWAY_IAM_ROLE",
						credentialProvider: {
							iamCredentialProvider: {
								// AWS MCP Server requires SigV4 scoped to
								// service name "aws-mcp"; "execute-api"
								// passes tools/list but is rejected on
								// tools/call.
								service: "aws-mcp",
							},
						},
					},
				],
			},
		);

		// Make sure the gateway role's DefaultPolicy (which now includes the new
		// execute-api:Invoke statement) is attached before the target is created;
		// AgentCore Gateway calls tools/list against the MCP server during target
		// creation and that call must be authorized.
		awsMcpTarget.addDependency(gateway);
		const gatewayRoleDefaultPolicy = gatewayRole.node.findChild("DefaultPolicy")
			.node.defaultChild as cdk.CfnResource;
		awsMcpTarget.node.addDependency(gatewayRoleDefaultPolicy);

		// ========================================
		// Cedar Policy Engine + Policy via Custom Resource
		// ========================================
		// AgentCore Policy uses a three-step process:
		//   1. Create a Policy Engine → wait for ACTIVE
		//   2. Create a Cedar Policy inside the engine → wait for ACTIVE
		//   3. Attach the Policy Engine to the Gateway → wait for READY
		//
		// CfnGatewayPolicy is not available as an L1 construct in aws-cdk-lib, so a Custom
		// Resource Lambda is used (same pattern as the OAuth2 Credential Provider).
		//
		// The Gateway's JWT Authorizer maps M2M JWT claims to Cedar principal tags:
		//   JWT claim "department" → principal.getTag("department")
		//   JWT claim "role"       → principal.getTag("role")
		//   JWT claim "user_id"    → principal.getTag("user_id")
		// These are CUSTOM claims injected by the V3 Pre-Token Lambda, not standard
		// JWT claims. You can define custom claim names and match them in Cedar.
		//
		// The Cedar action name format is: "<TargetName>___<tool_name>" (triple underscore).
		// Tool name comes from tool_spec.json: "text_analysis_tool"
		// Target name is "sample-tool-target"
		//
		// THREE POLICY VERSIONS FOR DEMO TESTING:
		// - Version 1: Guest has full access — all departments can use tools
		// - Version 2: Guest denied — only finance/engineering can use tools
		//
		// To switch versions: edit gateway/policies/policy.cedar, then run `cdk deploy`
		//
		// CEDAR POLICY SYNTAX NOTES:
		// - Each create_policy call creates one policy containing one Cedar statement.
		//   You can call create_policy multiple times to add multiple policies to the
		//   same engine. Alternatively, use || or action in [...] to combine rules
		//   within a single statement.
		// - Cedar is deny-by-default: if no permit statement matches a request, it is
		//   automatically denied. An explicit forbid statement is not needed to block
		//   access — simply omit the department from the permit's OR conditions.
		// - This template creates a single policy per deploy. To add multiple policies,
		//   update the Custom Resource Lambda to call create_policy() once per statement.

		const cedarPolicyLambda = new PythonFunction(this, "CedarPolicyLambda", {
			runtime: lambda.Runtime.PYTHON_3_13,
			entry: path.join(__dirname, "..", "lambdas", "cedar-policy"),
			handler: "handler",
			timeout: cdk.Duration.minutes(14),
			logGroup: new logs.LogGroup(this, "CedarPolicyLambdaLogGroup", {
				logGroupName: `/aws/lambda/${config.stack_name_base}-cedar-policy`,
				retention: logs.RetentionDays.ONE_WEEK,
				removalPolicy: cdk.RemovalPolicy.DESTROY,
			}),
		});

		// Grant Lambda permissions for Policy Engine and Policy operations.
		// The IAM actions use the "bedrock-agentcore:" prefix for policy engine
		// and gateway operations.
		cedarPolicyLambda.addToRolePolicy(
			new iam.PolicyStatement({
				actions: [
					"bedrock-agentcore:CreatePolicyEngine",
					"bedrock-agentcore:GetPolicyEngine",
					"bedrock-agentcore:DeletePolicyEngine",
					"bedrock-agentcore:ListPolicyEngines",
					"bedrock-agentcore:CreatePolicy",
					"bedrock-agentcore:GetPolicy",
					"bedrock-agentcore:DeletePolicy",
					"bedrock-agentcore:ListPolicies",
				],
				resources: [
					`arn:aws:bedrock-agentcore:${this.region}:${this.account}:policy-engine/*`,
				],
			}),
		);

		// Grant Lambda permissions to update the Gateway (attach/detach policy engine)
		// and read Gateway configuration for the update_gateway call.
		// iam:PassRole is required because update_gateway re-associates the Gateway's IAM role.
		//
		// ListGatewayTargets / GetGatewayTarget are required because CreatePolicy
		// validates each Cedar statement against the schema generated from the
		// gateway's tools, and that validation enumerates the gateway's targets
		// using the CALLER's credentials (this Lambda role), not the gateway
		// execution role. Without them the policy lands in CREATE_FAILED with
		// statusReason "Insufficient permissions to list gateway targets for gateway
		// with ID <id>". Older AgentCore API versions skipped this check, so
		// stacks created before the change succeeded without them; a
		// fresh deploy now needs them. Verified empirically: the gateway execution
		// role already had these actions yet the policy still failed, and only
		// adding them HERE (the create_policy caller) resolves it.
		//
		// InvokeGateway is required by a LATER AgentCore API change (same class of
		// breakage as ListGatewayTargets above): CreatePolicy/UpdatePolicy validate
		// the Cedar statement's actions against the live Gateway, an operation
		// authorized as InvokeGateway on the gateway ARN — NOT just a runtime
		// permission. Without it the policy transitions to CREATE_FAILED with
		// "Insufficient permissions to call gateway with ID <id>" (per the AgentCore
		// Policy IAM docs). A gateway created before this check existed could
		// succeed without it; a fresh deploy after the API change needs it. The
		// stack's resource-scoped Cedar policies target a specific gateway ARN, so
		// the existing ManageResourceScopedPolicy gate is sufficient (ManageAdminPolicy
		// is only needed for wildcard gateway/* policies, which this stack does not use).
		cedarPolicyLambda.addToRolePolicy(
			new iam.PolicyStatement({
				actions: [
					"bedrock-agentcore:UpdateGateway",
					"bedrock-agentcore:GetGateway",
					"bedrock-agentcore:InvokeGateway",
					"bedrock-agentcore:ManageResourceScopedPolicy",
					"bedrock-agentcore:ListGatewayTargets",
					"bedrock-agentcore:GetGatewayTarget",
				],
				resources: [gateway.attrGatewayArn],
			}),
		);

		cedarPolicyLambda.addToRolePolicy(
			new iam.PolicyStatement({
				actions: ["iam:PassRole"],
				resources: [gatewayRole.roleArn],
			}),
		);

		const cedarPolicyProvider = new cr.Provider(this, "CedarPolicyProvider", {
			onEventHandler: cedarPolicyLambda,
		});

		// Load Cedar policies from gateway/policies/*.cedar.
		// AgentCore's CreatePolicy API accepts exactly one Cedar statement per
		// call, so each file must contain exactly one `permit` (or `forbid`).
		// We read every *.cedar file in the directory (sorted lexicographically),
		// strip comment-only lines, substitute {{GATEWAY_ARN}}, and pass them to
		// the Custom Resource as an array. The Lambda then invokes CreatePolicy
		// once per document inside a single Policy Engine.
		const policiesDir = path.join(__dirname, "../../gateway/policies");
		const policyDocuments = fs
			.readdirSync(policiesDir)
			.filter((f: string) => f.endsWith(".cedar"))
			.sort()
			.map((f: string) =>
				fs
					.readFileSync(path.join(policiesDir, f), "utf-8")
					.split("\n")
					.filter((line: string) => !line.trimStart().startsWith("//"))
					.join("\n")
					.trim()
					.replaceAll("{{GATEWAY_ARN}}", gateway.attrGatewayArn),
			)
			.filter((doc: string) => doc.length > 0);

		const cedarPolicy = new cdk.CustomResource(this, "GatewayPolicy", {
			serviceToken: cedarPolicyProvider.serviceToken,
			properties: {
				GatewayIdentifier: gateway.attrGatewayIdentifier,
				// Pass an array; the Custom Resource Lambda creates one
				// AgentCore Policy per element inside the Policy Engine.
				PolicyDocuments: policyDocuments,
				// Policy name format: {PolicyEngineName}_cp_{timestamp}_{index}
				// The AgentCore API enforces a 48-character limit on policy names.
				PolicyEngineName: `${config.stack_name_base.replace(/-/g, "_")}_policy_engine`,
				Description:
					"Department-based tool access control for AgentCore Policy demo",
			},
		});

		// Policy must be created after the Gateway and its target are ready.
		// createLtmMcpServerTarget adds a further dependency on the ltm-mcp
		// target: CreatePolicy validates each statement against the schema
		// generated from the gateway's tools, so every target whose tools the
		// policies reference must exist first.
		cedarPolicy.node.addDependency(gatewayTarget);
		this.gatewayCedarPolicy = cedarPolicy;

		// Store AgentCore Gateway URL in SSM for AgentCore Runtime access
		new ssm.StringParameter(this, "GatewayUrlParam", {
			parameterName: `/${config.stack_name_base}/gateway_url`,
			stringValue: gateway.attrGatewayUrl,
			description: "AgentCore Gateway URL",
		});

		// Output gateway information
		new cdk.CfnOutput(this, "GatewayId", {
			value: gateway.attrGatewayIdentifier,
			description: "AgentCore Gateway ID",
		});

		new cdk.CfnOutput(this, "GatewayUrl", {
			value: gateway.attrGatewayUrl,
			description: "AgentCore Gateway URL",
		});

		new cdk.CfnOutput(this, "GatewayArn", {
			value: gateway.attrGatewayArn,
			description: "AgentCore Gateway ARN",
		});

		new cdk.CfnOutput(this, "GatewayTargetId", {
			value: gatewayTarget.ref,
			description: "AgentCore Gateway Target ID",
		});

		new cdk.CfnOutput(this, "ToolLambdaArn", {
			description: "ARN of the sample tool Lambda",
			value: toolLambda.functionArn,
		});

		new cdk.CfnOutput(this, "PolicyEngineId", {
			description: "ID of the Policy Engine for Cedar policies",
			value: cedarPolicy.getAttString("PolicyEngineId"),
		});

		// The Custom Resource creates one AgentCore Policy per file in
		// gateway/policies/. The Lambda returns the comma-separated ids in
		// PolicyIds and the count in PolicyCount.
		new cdk.CfnOutput(this, "CedarPolicyIds", {
			description:
				"Comma-separated IDs of the Cedar policies attached to the Policy Engine",
			value: cedarPolicy.getAttString("PolicyIds"),
		});

		new cdk.CfnOutput(this, "CedarPolicyCount", {
			description: "Number of Cedar policies attached to the Policy Engine",
			value: cedarPolicy.getAttString("PolicyCount"),
		});
	}

	private createMachineAuthentication(config: AppConfig): void {
		// Create Resource Server for Machine-to-Machine (M2M) authentication
		// This defines the API scopes that machine clients can request access to
		const resourceServer = new cognito.UserPoolResourceServer(
			this,
			"ResourceServer",
			{
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
			},
		);

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
						}),
					),
					cognito.OAuthScope.resourceServer(
						resourceServer,
						new cognito.ResourceServerScope({
							scopeName: "write",
							scopeDescription: "Write access to gateway",
						}),
					),
				],
			},
		});

		// Machine client must be created after resource server
		this.machineClient.node.addDependency(resourceServer);

		// Store machine client secret in Secrets Manager for testing and external access.
		// This secret is used by test scripts and potentially other external tools.
		this.machineClientSecret = new secretsmanager.Secret(
			this,
			"MachineClientSecret",
			{
				secretName: `/${config.stack_name_base}/machine_client_secret`,
				secretStringValue: cdk.SecretValue.unsafePlainText(
					this.machineClient.userPoolClientSecret.unsafeUnwrap(),
				),
				description: "Machine Client Secret for M2M authentication",
			},
		);
	}

	/**
	 * Builds the lifecycle configuration shared by every AgentCore Runtime in
	 * this stack. A longer idle timeout keeps microVMs warm across
	 * demo-length pauses; re-warming in the closed VPC costs an ENI provision +
	 * image pull (~25s observed), while idle time only bills memory.
	 *
	 * @param config - The application configuration from config.yaml.
	 * @returns The LifecycleConfiguration to pass to each Runtime.
	 */
	private buildLifecycleConfiguration(
		config: AppConfig,
	): agentcore.LifecycleConfiguration {
		const idleSeconds =
			config.backend.runtime_lifecycle?.idle_session_timeout_seconds ??
			DEFAULT_IDLE_RUNTIME_SESSION_TIMEOUT_SECONDS;
		const maxSeconds = config.backend.runtime_lifecycle?.max_lifetime_seconds;
		return {
			idleRuntimeSessionTimeout: cdk.Duration.seconds(idleSeconds),
			...(maxSeconds !== undefined
				? { maxLifetime: cdk.Duration.seconds(maxSeconds) }
				: {}),
		};
	}

	/**
	 * VPC placement props shared by the auxiliary API Lambdas (Feedback /
	 * History / Sessions) so their DynamoDB / Bedrock / Memory traffic stays in
	 * the closed network via the VPC endpoints.
	 *
	 * Spread into a PythonFunction's props. Returns an empty object in PUBLIC
	 * mode (this.vpc undefined), leaving the Lambda non-VPC and unchanged. When a
	 * VPC is present, placement matches the Runtime exactly: the PRIVATE_ISOLATED
	 * tier and the shared self-referencing SG (443 to the interface endpoints).
	 */
	private closedNetworkLambdaProps(): Pick<
		lambda.FunctionProps,
		"vpc" | "vpcSubnets" | "securityGroups"
	> {
		if (!this.vpc) {
			return {};
		}
		return {
			vpc: this.vpc,
			vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
			securityGroups: this.runtimeSecurityGroup
				? [this.runtimeSecurityGroup]
				: undefined,
		};
	}

	/**
	 * Builds the RuntimeNetworkConfiguration based on the config.yaml settings.
	 *
	 * When network_mode is "VPC":
	 *  - vpc_management "CDK" (default): use the VPC + shared SG created by
	 *    VpcStack and passed in via props (this.vpc / this.runtimeSecurityGroup),
	 *    placing the runtime ENIs in the private-with-egress subnets.
	 *  - vpc_management "EXISTING": import the user's VPC, subnets, and optional
	 *    security groups from config.backend.vpc via ec2.Vpc.fromLookup.
	 *
	 * When network_mode is "PUBLIC" (default), returns a public network configuration.
	 *
	 * @param config - The application configuration from config.yaml.
	 * @returns A RuntimeNetworkConfiguration for the AgentCore Runtime.
	 */
	private buildNetworkConfiguration(
		config: AppConfig,
	): agentcore.RuntimeNetworkConfiguration {
		if (config.backend.network_mode !== "VPC") {
			// Default: public network mode
			return agentcore.RuntimeNetworkConfiguration.usingPublicNetwork();
		}

		// vpc_management: CDK — use the VPC created by VpcStack (passed via props).
		if (this.vpc) {
			const vpcConfigProps: agentcore.VpcConfigProps = {
				vpc: this.vpc,
				// Closed network: the runtime sits in isolated subnets (no NAT).
				// Must match VpcStack's PRIVATE_ISOLATED private tier.
				vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
				securityGroups: this.runtimeSecurityGroup
					? [this.runtimeSecurityGroup]
					: undefined,
			};
			return agentcore.RuntimeNetworkConfiguration.usingVpc(
				this,
				vpcConfigProps,
			);
		}

		// vpc_management: EXISTING — import the user's VPC/subnets via fromLookup.
		const vpcConfig = config.backend.vpc;
		// vpc config is validated in ConfigManager, but guard here for type safety
		if (!vpcConfig) {
			throw new Error(
				"backend.vpc configuration is required when network_mode is 'VPC' and vpc_management is 'EXISTING'.",
			);
		}

		// Import the user's existing VPC by ID.
		// This performs a context lookup at synth time to resolve VPC attributes.
		const vpc = ec2.Vpc.fromLookup(this, "ImportedVpc", {
			vpcId: vpcConfig.vpc_id,
		});

		// Import the user-specified subnets by their IDs.
		// These subnets must exist within the VPC specified above.
		const subnets: ec2.ISubnet[] = vpcConfig.subnet_ids.map(
			(subnetId: string, index: number) =>
				ec2.Subnet.fromSubnetId(this, `ImportedSubnet${index}`, subnetId),
		);

		// Build the VPC config props for the AgentCore L2 construct.
		// Security groups are optional — if not provided, the construct creates a default one.
		const securityGroups =
			vpcConfig.security_group_ids && vpcConfig.security_group_ids.length > 0
				? vpcConfig.security_group_ids.map((sgId: string, index: number) =>
						ec2.SecurityGroup.fromSecurityGroupId(
							this,
							`ImportedSG${index}`,
							sgId,
						),
					)
				: undefined;

		const vpcConfigProps: agentcore.VpcConfigProps = {
			vpc: vpc,
			vpcSubnets: {
				subnets: subnets,
			},
			securityGroups: securityGroups,
		};

		return agentcore.RuntimeNetworkConfiguration.usingVpc(this, vpcConfigProps);
	}

	/**
	 * Recursively read directory contents and encode as base64.
	 *
	 * @param dirPath - Directory to read.
	 * @param prefix - Prefix for file paths in output.
	 * @param output - Output object to populate.
	 */
	private readDirRecursive(
		dirPath: string,
		prefix: string,
		output: Record<string, string>,
	): void {
		for (const entry of fs.readdirSync(dirPath, { withFileTypes: true })) {
			const fullPath = path.join(dirPath, entry.name); // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal
			const relativePath = path.join(prefix, entry.name); // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal

			if (entry.isDirectory()) {
				// Skip __pycache__ directories
				if (entry.name !== "__pycache__") {
					this.readDirRecursive(fullPath, relativePath, output);
				}
			} else if (entry.isFile()) {
				const content = fs.readFileSync(fullPath);
				output[relativePath] = content.toString("base64");
			}
		}
	}

	/**
	 * Create a hash of content for change detection.
	 *
	 * @param content - Content to hash.
	 * @returns Hash string.
	 */
	private hashContent(content: string): string {
		const crypto = require("crypto");
		return crypto
			.createHash("sha256")
			.update(content)
			.digest("hex")
			.slice(0, 16);
	}
}
