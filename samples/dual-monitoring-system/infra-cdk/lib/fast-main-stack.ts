import * as cdk from "aws-cdk-lib"
import { Construct } from "constructs"
import { AppConfig } from "./utils/config-manager"

// Import nested stacks
import { BackendStack } from "./backend-stack"
import { AmplifyHostingStack } from "./amplify-hosting-stack"
import { CognitoStack } from "./cognito-stack"
import { EvaluationStack } from "./evaluation-stack"

export interface FastAmplifyStackProps extends cdk.StackProps {
  config: AppConfig
}

export class FastMainStack extends cdk.Stack {
  public readonly amplifyHostingStack: AmplifyHostingStack
  public readonly backendStack: BackendStack
  public readonly cognitoStack: CognitoStack
  public readonly evaluationStack: EvaluationStack

  constructor(scope: Construct, id: string, props: FastAmplifyStackProps) {
    const description = "Dual Monitoring - Main Stack (v0.3.0) (uksb-v6dos0t5g8)"
    super(scope, id, { ...props, description })

    // Step 1: Create the Amplify stack to get the predictable domain
    this.amplifyHostingStack = new AmplifyHostingStack(this, `${id}-amplify`, {
      config: props.config,
    })

    // Step 2: Create Cognito stack
    this.cognitoStack = new CognitoStack(this, `${id}-cognito`, {
      config: props.config,
      callbackUrls: ["http://localhost:3000", this.amplifyHostingStack.amplifyUrl],
    })

    // Step 3: Create evaluation stack (Lambda only, no API routes)
    this.evaluationStack = new EvaluationStack(this, `${id}-evaluation`, {
      config: props.config,
      frontendUrl: this.amplifyHostingStack.amplifyUrl,
    })

    // Step 4: Create backend stack with evaluation lambda reference
    // Backend stack will create API Gateway and add routes for both feedback and evaluation
    this.backendStack = new BackendStack(this, `${id}-backend`, {
      config: props.config,
      userPoolId: this.cognitoStack.userPoolId,
      userPoolClientId: this.cognitoStack.userPoolClientId,
      userPoolDomain: this.cognitoStack.userPoolDomain,
      frontendUrl: this.amplifyHostingStack.amplifyUrl,
      evaluationLambda: this.evaluationStack.evaluationLambda,
    })

    // Outputs
    new cdk.CfnOutput(this, "AmplifyAppId", {
      value: this.amplifyHostingStack.amplifyApp.appId,
      description: "Amplify App ID - use this for manual deployment",
      exportName: `${props.config.stack_name_base}-AmplifyAppId`,
    })

    new cdk.CfnOutput(this, "CognitoUserPoolId", {
      value: this.cognitoStack.userPoolId,
      description: "Cognito User Pool ID",
      exportName: `${props.config.stack_name_base}-CognitoUserPoolId`,
    })

    new cdk.CfnOutput(this, "CognitoClientId", {
      value: this.cognitoStack.userPoolClientId,
      description: "Cognito User Pool Client ID",
      exportName: `${props.config.stack_name_base}-CognitoClientId`,
    })

    new cdk.CfnOutput(this, "CognitoDomain", {
      value: `${this.cognitoStack.userPoolDomain.domainName}.auth.${cdk.Aws.REGION}.amazoncognito.com`,
      description: "Cognito Domain for OAuth",
      exportName: `${props.config.stack_name_base}-CognitoDomain`,
    })

    new cdk.CfnOutput(this, "RuntimeArn", {
      value: this.backendStack.runtimeArn,
      description: "AgentCore Runtime ARN",
      exportName: `${props.config.stack_name_base}-RuntimeArn`,
    })

    new cdk.CfnOutput(this, "MemoryArn", {
      value: this.backendStack.memoryArn,
      description: "AgentCore Memory ARN",
      exportName: `${props.config.stack_name_base}-MemoryArn`,
    })

    new cdk.CfnOutput(this, "FeedbackApiUrl", {
      value: this.backendStack.feedbackApiUrl,
      description: "Shared API Gateway URL (Feedback + Evaluation)",
      exportName: `${props.config.stack_name_base}-FeedbackApiUrl`,
    })

    new cdk.CfnOutput(this, "AmplifyConsoleUrl", {
      value: `https://console.aws.amazon.com/amplify/apps/${this.amplifyHostingStack.amplifyApp.appId}`,
      description: "Amplify Console URL for monitoring deployments",
    })

    new cdk.CfnOutput(this, "AmplifyUrl", {
      value: this.amplifyHostingStack.amplifyUrl,
      description: "Amplify Frontend URL (available after deployment)",
    })

    new cdk.CfnOutput(this, "StagingBucketName", {
      value: this.amplifyHostingStack.stagingBucket.bucketName,
      description: "S3 bucket for Amplify deployment staging",
      exportName: `${props.config.stack_name_base}-StagingBucket`,
    })

    new cdk.CfnOutput(this, "EvaluationApiUrl", {
      value: this.backendStack.feedbackApiUrl,
      description: "Evaluation API Gateway URL (shared with Feedback API)",
      exportName: `${props.config.stack_name_base}-EvaluationApiUrl`,
    })

    new cdk.CfnOutput(this, "EvaluationLambdaArn", {
      value: this.evaluationStack.evaluationLambda.functionArn,
      description: "Evaluation Lambda function ARN",
      exportName: `${props.config.stack_name_base}-EvaluationLambdaArn`,
    })

    new cdk.CfnOutput(this, "DevOpsIncidentApiUrl", {
      value: this.backendStack.devopsIncidentApiUrl,
      description: "DevOps Agent incident API URL",
      exportName: `${props.config.stack_name_base}-DevOpsIncidentApiUrl`,
    })
  }
}
