import * as cdk from "aws-cdk-lib";
import * as cognito from "aws-cdk-lib/aws-cognito";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as iam from "aws-cdk-lib/aws-iam";
import * as logs from "aws-cdk-lib/aws-logs";
import * as path from "path";
import { Construct } from "constructs";
import { AppConfig } from "./utils/config-manager";

export interface CognitoStackProps extends cdk.NestedStackProps {
	config: AppConfig;
	callbackUrls?: string[];
}

export class CognitoStack extends cdk.NestedStack {
	public userPoolId: string;
	public userPoolClientId: string;
	public userPoolDomain: cognito.UserPoolDomain;

	constructor(scope: Construct, id: string, props: CognitoStackProps) {
		super(scope, id, props);

		this.createCognitoUserPool(props.config, props.callbackUrls);
	}

	private createCognitoUserPool(
		config: AppConfig,
		callbackUrls?: string[],
	): void {
		// Use provided callback URLs or defaults
		const defaultCallbackUrls = [
			"http://localhost:3000",
			"https://localhost:3000",
		];
		const finalCallbackUrls = callbackUrls || defaultCallbackUrls;

		const userPool = new cognito.UserPool(this, "UserPool", {
			userPoolName: `${config.stack_name_base}-user-pool`,
			selfSignUpEnabled: false,
			signInAliases: {
				email: true,
			},
			autoVerify: {
				email: true,
			},
			standardAttributes: {
				email: {
					required: true,
					mutable: false,
				},
			},
			passwordPolicy: {
				minLength: 8,
				requireLowercase: true,
				requireUppercase: true,
				requireDigits: true,
				requireSymbols: true,
			},
			accountRecovery: cognito.AccountRecovery.EMAIL_ONLY,
			removalPolicy: cdk.RemovalPolicy.DESTROY,
			// Essentials tier is required for V3 Pre-Token Generation Lambda triggers.
			// V3 triggers fire on Client Credentials (M2M) grants, enabling user identity
			// propagation into M2M tokens for AgentCore Policy enforcement.
			featurePlan: cognito.FeaturePlan.ESSENTIALS,
			userInvitation: {
				emailSubject: `Welcome to ${config.stack_name_base}!`,
				emailBody: `<p>Hello {username},</p>
<p>Welcome to ${config.stack_name_base}! Your username is <strong>{username}</strong> and your temporary password is: <strong>{####}</strong></p>
<p>Please use this temporary password to log in and set your permanent password.</p>
<p>The CloudFront URL to your application is stored as an output in the "${config.stack_name_base}" stack, and will be printed to your terminal once the deployment process completes.</p>
<p>Thanks,</p>
<p>Fullstack AgentCore Solution Template Team</p>`,
			},
		});

		const userPoolClient = new cognito.UserPoolClient(this, "UserPoolClient", {
			userPool: userPool,
			userPoolClientName: `${config.stack_name_base}-client`,
			generateSecret: false,
			authFlows: {
				userPassword: true,
				userSrp: true,
			},
			oAuth: {
				flows: {
					authorizationCodeGrant: true,
				},
				scopes: [
					cognito.OAuthScope.OPENID,
					cognito.OAuthScope.EMAIL,
					cognito.OAuthScope.PROFILE,
				],
				// Support both localhost development and production URLs
				callbackUrls: finalCallbackUrls,
				logoutUrls: finalCallbackUrls,
			},
			preventUserExistenceErrors: true,
		});

		// Create domain without managedLoginVersion initially to avoid race condition
		// with CfnManagedLoginBranding. The domain is updated to v2 after branding is created
		// via L1 escape hatch below. This resolves "Internal error from downstream service"
		// that occurs with newer CDK versions when ESSENTIALS tier + NEWER_MANAGED_LOGIN +
		// CfnManagedLoginBranding are created simultaneously.
		this.userPoolDomain = new cognito.UserPoolDomain(this, "UserPoolDomain", {
			userPool: userPool,
			cognitoDomain: {
				domainPrefix: `${config.stack_name_base.toLowerCase()}-${cdk.Aws.ACCOUNT_ID}-${
					cdk.Aws.REGION
				}`,
			},
		});

		// Create managed login branding with Cognito's default styles
		const managedLoginBranding = new cognito.CfnManagedLoginBranding(
			this,
			"ManagedLoginBranding",
			{
				userPoolId: userPool.userPoolId,
				clientId: userPoolClient.userPoolClientId,
				useCognitoProvidedValues: true,
			},
		);

		managedLoginBranding.node.addDependency(this.userPoolDomain);

		// Update domain to use managed login v2 after branding resource is defined.
		// Uses L1 escape hatch to set ManagedLoginVersion on the CloudFormation resource.
		const cfnDomain = this.userPoolDomain.node
			.defaultChild as cognito.CfnUserPoolDomain;
		cfnDomain.managedLoginVersion = 2;

		// ========================================
		// V3 Pre-Token Generation Lambda
		// ========================================
		// This Lambda fires on M2M token generation (Client Credentials flow) and injects
		// custom claims (user_id, department, role) into the M2M access token.
		// These are application-defined claims, not standard JWT/OIDC claims.
		// The claims are read from clientMetadata, which carries the user's JWT
		// `sub` and `cognito:groups` (propagated by the runtime via the Token
		// Vault custom_parameters). The group name becomes the `department` claim.
		//
		// The runtime reads the groups straight from the user's validated access
		// token, so this Lambda needs NO cognito-idp:AdminListGroupsForUser
		// permission — which also avoids the UserPool <-> Lambda circular
		// dependency that scoping such a policy to the pool ARN would create. Add
		// a user to the "finance"/"engineering" group to grant that department;
		// users in no group are "guest". See infra-cdk/lambdas/pretoken-v3/index.py.
		const preTokenLambda = new lambda.Function(this, "PreTokenLambda", {
			functionName: `${config.stack_name_base}-pretoken-v3`,
			runtime: lambda.Runtime.PYTHON_3_13,
			handler: "index.lambda_handler",
			code: lambda.Code.fromAsset(
				path.join(__dirname, "..", "lambdas", "pretoken-v3"),
			), // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal
			timeout: cdk.Duration.seconds(30),
			description: "V3 Pre-Token Lambda for M2M user identity propagation",
			logGroup: new logs.LogGroup(this, "PreTokenLambdaLogGroup", {
				logGroupName: `/aws/lambda/${config.stack_name_base}-pretoken-v3`,
				retention: logs.RetentionDays.ONE_WEEK,
				removalPolicy: cdk.RemovalPolicy.DESTROY,
			}),
		});

		// Grant Cognito permission to invoke the Pre-Token Lambda
		preTokenLambda.addPermission("CognitoInvoke", {
			principal: new iam.ServicePrincipal("cognito-idp.amazonaws.com"),
			sourceArn: userPool.userPoolArn,
		});

		// Cognito groups whose names map to Cedar `department` values. Users are
		// added to these to receive per-user authorization. "guest" is NOT a
		// group: it is the fallback department the Pre-Token Lambda assigns to a
		// user who belongs to no recognised group, and Cedar's deny-by-default
		// then rejects every Gateway tool call for them — so only the privileged
		// groups need to exist.
		const groups: Record<string, cognito.CfnUserPoolGroup> = {};
		for (const groupName of ["finance", "engineering"]) {
			groups[groupName] = new cognito.CfnUserPoolGroup(
				this,
				`Group-${groupName}`,
				{
					userPoolId: userPool.userPoolId,
					groupName,
					description: `Cedar department=${groupName}`,
				},
			);
		}

		// Attach V3 Lambda using L1 escape hatch.
		// The CDK L2 UserPool.addTrigger() only supports V1_0 and V2_0,
		// so addPropertyOverride is used to set V3_0 on the CloudFormation template directly.
		const cfnUserPool = userPool.node.defaultChild as cognito.CfnUserPool;
		cfnUserPool.addPropertyOverride("LambdaConfig.PreTokenGenerationConfig", {
			LambdaArn: preTokenLambda.functionArn,
			LambdaVersion: "V3_0",
		});

		// Store the IDs for export
		this.userPoolId = userPool.userPoolId;
		this.userPoolClientId = userPoolClient.userPoolClientId;

		// Create admin user if email is provided in config
		if (config.admin_user_email) {
			const adminUser = new cognito.CfnUserPoolUser(this, "AdminUser", {
				userPoolId: userPool.userPoolId,
				username: config.admin_user_email,
				userAttributes: [
					{
						name: "email",
						value: config.admin_user_email,
					},
				],
				desiredDeliveryMediums: ["EMAIL"],
			});

			// Put the admin user in the finance group. department/role authorization
			// is driven by Cognito group membership (the email address plays no
			// part), and a user in no group is classified "guest", which Cedar
			// denies for every Gateway tool — without this attachment the admin
			// created above could sign in but not call a single tool. finance maps
			// to role=admin in the Pre-Token Lambda and is the only department the
			// Cedar policies allow to use the destructive AWS MCP tools
			// (aws___call_aws / aws___run_script).
			const adminMembership = new cognito.CfnUserPoolUserToGroupAttachment(
				this,
				"AdminUserFinanceMembership",
				{
					userPoolId: userPool.userPoolId,
					username: config.admin_user_email,
					groupName: "finance",
				},
			);
			adminMembership.addDependency(adminUser);
			adminMembership.addDependency(groups["finance"]);

			// Output admin user creation status
			new cdk.CfnOutput(this, "AdminUserCreated", {
				description:
					"Admin user created (finance group member) and credentials emailed",
				value: `Admin user created: ${config.admin_user_email}`,
			});
		}

		new cdk.CfnOutput(this, "PreTokenLambdaArn", {
			description: "ARN of the V3 Pre-Token Generation Lambda",
			value: preTokenLambda.functionArn,
		});
	}
}
