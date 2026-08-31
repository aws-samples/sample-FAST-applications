import * as cdk from "aws-cdk-lib"
import * as cognito from "aws-cdk-lib/aws-cognito"
import * as ssm from "aws-cdk-lib/aws-ssm"
import * as dynamodb from "aws-cdk-lib/aws-dynamodb"
import * as apigateway from "aws-cdk-lib/aws-apigateway"
import * as logs from "aws-cdk-lib/aws-logs"
import { PythonFunction } from "@aws-cdk/aws-lambda-python-alpha"
import * as lambda from "aws-cdk-lib/aws-lambda"
import { Construct } from "constructs"
import { AppConfig } from "../utils/config-manager"
import * as path from "path"

export interface ApiConstructProps {
  config: AppConfig
  /** User pool used by the Cognito authorizer protecting the API. */
  userPool: cognito.IUserPool
  frontendUrl: string
}

/**
 * Generic REST API (API Gateway) with a shared Cognito authorizer and request validator.
 * Anything that needs to be reached over HTTP through API Gateway is added here as a
 * route. The feedback endpoint below is an EXAMPLE route demonstrating the best-practice
 * API Gateway + Lambda + DynamoDB pattern; add or replace routes the same way.
 *
 * The REST API and authorizer are exposed so additional routes can be attached:
 *   const r = api.restApi.root.addResource("my-route")
 *   r.addMethod("POST", integration, { authorizer: api.authorizer, ... })
 */
export class ApiConstruct extends Construct {
  /** Base URL of the API Gateway stage. */
  public readonly apiUrl: string
  /** The REST API, exposed so additional routes can be attached. */
  public readonly restApi: apigateway.RestApi
  /** Shared Cognito authorizer for protecting routes. */
  public readonly authorizer: apigateway.CognitoUserPoolsAuthorizer
  /** Shared request validator (body + params). */
  public readonly requestValidator: apigateway.RequestValidator

  constructor(scope: Construct, id: string, props: ApiConstructProps) {
    super(scope, id)

    const { config, userPool, frontendUrl } = props

    // ---- Generic API Gateway core (reusable for any route) ----
    this.restApi = new apigateway.RestApi(this, "RestApi", {
      restApiName: `${config.stack_name_base}-api`,
      description: "Application REST API (feedback endpoint is an example route)",
      defaultCorsPreflightOptions: {
        allowOrigins: [frontendUrl, "http://localhost:3000"],
        allowMethods: ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allowHeaders: ["Content-Type", "Authorization"],
      },
      deployOptions: {
        stageName: "prod",
        throttlingRateLimit: 100,
        throttlingBurstLimit: 200,
        // Caching is OFF: the sessions API must read the latest state (a cached
        // GET /sessions would serve a stale list right after a save or delete).
        cachingEnabled: false,
        cacheClusterEnabled: false,
        loggingLevel: apigateway.MethodLoggingLevel.INFO,
        dataTraceEnabled: true,
        metricsEnabled: true,
        accessLogDestination: new apigateway.LogGroupLogDestination(
          new logs.LogGroup(this, "ApiAccessLogGroup", {
            logGroupName: `/aws/apigateway/${config.stack_name_base}-api-access`,
            retention: logs.RetentionDays.ONE_WEEK,
            removalPolicy: cdk.RemovalPolicy.DESTROY,
          })
        ),
        accessLogFormat: apigateway.AccessLogFormat.jsonWithStandardFields(),
        tracingEnabled: true,
      },
    })

    this.requestValidator = new apigateway.RequestValidator(this, "RequestValidator", {
      restApi: this.restApi,
      requestValidatorName: `${config.stack_name_base}-request-validator`,
      validateRequestBody: true,
      validateRequestParameters: true,
    })

    // Cognito authorizer (IdP-specific; for a different IdP use a JWT/Lambda authorizer).
    this.authorizer = new apigateway.CognitoUserPoolsAuthorizer(this, "ApiAuthorizer", {
      cognitoUserPools: [userPool],
      identitySource: "method.request.header.Authorization",
      authorizerName: `${config.stack_name_base}-authorizer`,
    })

    this.apiUrl = this.restApi.url

    // ---- EXAMPLE route: POST /feedback ----
    this.addFeedbackExample(config, frontendUrl)

    // ---- Session history: GET/PUT/DELETE /sessions ----
    this.addSessions(config, frontendUrl)

    // Stored under the historical name for frontend compatibility.
    new ssm.StringParameter(this, "ApiUrlParam", {
      parameterName: `/${config.stack_name_base}/feedback-api-url`,
      stringValue: this.restApi.url,
      description: "Application API Gateway base URL",
    })

    // Sessions share the same API base URL; published separately for clarity.
    new ssm.StringParameter(this, "SessionsApiUrlParam", {
      parameterName: `/${config.stack_name_base}/sessions-api-url`,
      stringValue: this.restApi.url,
      description: "Sessions API Gateway base URL",
    })
  }

  /**
   * EXAMPLE: a DynamoDB-backed feedback endpoint (POST /feedback) showing how to add a
   * route with the shared authorizer and validator. Remove or replace for your own API.
   * Implementation: infra-cdk/lambdas/feedback/index.py
   */
  private addFeedbackExample(config: AppConfig, frontendUrl: string): void {
    const feedbackTable = new dynamodb.Table(this, "FeedbackTable", {
      tableName: `${config.stack_name_base}-feedback`,
      partitionKey: { name: "feedbackId", type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
    })

    feedbackTable.addGlobalSecondaryIndex({
      indexName: "feedbackType-timestamp-index",
      partitionKey: { name: "feedbackType", type: dynamodb.AttributeType.STRING },
      sortKey: { name: "timestamp", type: dynamodb.AttributeType.NUMBER },
      projectionType: dynamodb.ProjectionType.ALL,
    })

    const feedbackLambda = new PythonFunction(this, "FeedbackLambda", {
      functionName: `${config.stack_name_base}-feedback`,
      runtime: lambda.Runtime.PYTHON_3_13,
      architecture: lambda.Architecture.ARM_64,
      entry: path.join(__dirname, "../../lambdas/feedback"), // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal
      handler: "handler",
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
          }:017000801446:layer:AWSLambdaPowertoolsPythonV3-python313-arm64:18`
        ),
      ],
      logGroup: new logs.LogGroup(this, "FeedbackLambdaLogGroup", {
        logGroupName: `/aws/lambda/${config.stack_name_base}-feedback`,
        retention: logs.RetentionDays.ONE_WEEK,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      }),
    })

    feedbackTable.grantWriteData(feedbackLambda)

    const feedbackResource = this.restApi.root.addResource("feedback")
    feedbackResource.addMethod("POST", new apigateway.LambdaIntegration(feedbackLambda), {
      authorizer: this.authorizer,
      authorizationType: apigateway.AuthorizationType.COGNITO,
      requestValidator: this.requestValidator,
    })
  }

  /**
   * Session history: a single DynamoDB table plus a Lambda that persists and
   * serves the frontend transcript, so the sidebar can list, resume, and delete
   * past conversations. The caller is taken from the validated JWT (never the
   * path), and reads are strongly consistent so a save or delete shows up
   * immediately. Implementation: infra-cdk/lambdas/sessions/index.py
   *
   * Table item shapes (pk / sk):
   * - USER#<userId>  / SESS#<sessionId>  -> { title, updatedAt }  (sidebar index)
   * - SESSION#<id>   / MSG#<index>       -> { userId, data }      (one message)
   *
   * Routes: GET /sessions, GET/PUT/DELETE /sessions/{sessionId}
   */
  private addSessions(config: AppConfig, frontendUrl: string): void {
    // One table for a user's session index rows (USER#) and the per-session
    // messages (SESSION#), so the sidebar lists with a strongly-consistent query
    // on the base table (no eventually-consistent GSI).
    const sessionsTable = new dynamodb.Table(this, "SessionsTable", {
      tableName: `${config.stack_name_base}-sessions`,
      partitionKey: { name: "pk", type: dynamodb.AttributeType.STRING },
      sortKey: { name: "sk", type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
    })

    const sessionsLambda = new PythonFunction(this, "SessionsLambda", {
      functionName: `${config.stack_name_base}-sessions`,
      runtime: lambda.Runtime.PYTHON_3_13,
      architecture: lambda.Architecture.ARM_64,
      entry: path.join(__dirname, "../../lambdas/sessions"), // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal
      handler: "handler",
      environment: {
        TABLE_NAME: sessionsTable.tableName,
        CORS_ALLOWED_ORIGINS: `${frontendUrl},http://localhost:3000`,
      },
      timeout: cdk.Duration.seconds(30),
      layers: [
        lambda.LayerVersion.fromLayerVersionArn(
          this,
          "SessionsPowertoolsLayer",
          `arn:aws:lambda:${
            cdk.Stack.of(this).region
          }:017000801446:layer:AWSLambdaPowertoolsPythonV3-python313-arm64:18`
        ),
      ],
      logGroup: new logs.LogGroup(this, "SessionsLambdaLogGroup", {
        logGroupName: `/aws/lambda/${config.stack_name_base}-sessions`,
        retention: logs.RetentionDays.ONE_WEEK,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      }),
    })

    sessionsTable.grantReadWriteData(sessionsLambda)

    const integration = new apigateway.LambdaIntegration(sessionsLambda)
    const authOptions = {
      authorizer: this.authorizer,
      authorizationType: apigateway.AuthorizationType.COGNITO,
    }

    const sessions = this.restApi.root.addResource("sessions")
    sessions.addMethod("GET", integration, authOptions)
    const sessionById = sessions.addResource("{sessionId}")
    sessionById.addMethod("GET", integration, authOptions)
    sessionById.addMethod("PUT", integration, authOptions)
    sessionById.addMethod("DELETE", integration, authOptions)
  }
}
