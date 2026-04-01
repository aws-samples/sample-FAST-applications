import * as cdk from "aws-cdk-lib"
import * as s3 from "aws-cdk-lib/aws-s3"
import * as iam from "aws-cdk-lib/aws-iam"
import * as bedrock from "aws-cdk-lib/aws-bedrock"
import * as s3vectors from "aws-cdk-lib/aws-s3vectors"
import * as ssm from "aws-cdk-lib/aws-ssm"
import { Construct } from "constructs"

export interface KnowledgeBaseStackProps extends cdk.StackProps {
  config: {
    stack_name_base: string
  }
}

/**
 * Standalone CDK stack that creates a Bedrock Knowledge Base backed by S3 Vectors.
 *
 * Deployed separately from the main stack. The main stack references outputs
 * via cdk.Fn.importValue().
 *
 * Resources created:
 * - S3 data bucket for source documents (.docx, .pdf, .txt, etc.)
 * - S3 Vector Bucket + Index for vector embeddings
 * - Bedrock Knowledge Base with S3 Vectors storage
 * - S3 Data Source for Bedrock-managed ingestion
 * - SSM Parameter for KB ID
 * - CfnOutputs with exportNames for cross-stack references
 */
export class KnowledgeBaseStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: KnowledgeBaseStackProps) {
    super(scope, id, props)

    const stackNameBase = props.config.stack_name_base
    const vectorBucketName = `kb-vectors-${stackNameBase}-${this.account.slice(-6)}`
    const indexName = `kb-idx-${stackNameBase}`

    // S3 bucket for knowledge base data source (documents)
    const dataBucket = new s3.Bucket(this, "KnowledgeBaseDataBucket", {
      versioned: true,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      cors: [
        {
          allowedMethods: [s3.HttpMethods.PUT, s3.HttpMethods.POST],
          allowedOrigins: ["*"],
          allowedHeaders: ["*"],
          maxAge: 3000,
        },
      ],
    })

    // S3 Vector Bucket for storing vector embeddings
    const vectorBucket = new s3vectors.CfnVectorBucket(this, "VectorBucket", {
      vectorBucketName: vectorBucketName,
      encryptionConfiguration: {
        sseType: "AES256",
      },
    })

    // S3 Vector Index (1024 dim for Titan Text Embeddings V2)
    const vectorIndex = new s3vectors.CfnIndex(this, "VectorIndex", {
      vectorBucketName: vectorBucket.vectorBucketName!,
      indexName: indexName,
      dimension: 1024,
      distanceMetric: "cosine",
      dataType: "float32",
      metadataConfiguration: {
        nonFilterableMetadataKeys: ["AMAZON_BEDROCK_TEXT_CHUNK"],
      },
    })
    vectorIndex.addDependency(vectorBucket)

    // IAM role for Bedrock Knowledge Base
    const knowledgeBaseRole = new iam.Role(this, "KnowledgeBaseRole", {
      assumedBy: new iam.ServicePrincipal("bedrock.amazonaws.com"),
      inlinePolicies: {
        S3VectorsAccess: new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({
              effect: iam.Effect.ALLOW,
              actions: [
                "s3vectors:PutVectors",
                "s3vectors:GetVectors",
                "s3vectors:DeleteVectors",
                "s3vectors:QueryVectors",
                "s3vectors:GetIndex",
              ],
              resources: [
                `arn:aws:s3vectors:${this.region}:${this.account}:bucket/${vectorBucketName}/index/${indexName}`,
              ],
            }),
          ],
        }),
        BedrockAccess: new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({
              effect: iam.Effect.ALLOW,
              actions: ["bedrock:ListCustomModels", "bedrock:InvokeModel"],
              resources: [`arn:aws:bedrock:${this.region}::foundation-model/*`],
            }),
          ],
        }),
        S3Access: new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({
              effect: iam.Effect.ALLOW,
              actions: ["s3:GetObject", "s3:ListBucket"],
              resources: [dataBucket.bucketArn, `${dataBucket.bucketArn}/*`],
            }),
          ],
        }),
      },
    })

    // Bedrock Knowledge Base with S3 Vectors storage
    const knowledgeBase = new bedrock.CfnKnowledgeBase(this, "KnowledgeBase", {
      name: `${stackNameBase}-kb`,
      description: "Knowledge base for restaurant information retrieval",
      roleArn: knowledgeBaseRole.roleArn,
      knowledgeBaseConfiguration: {
        type: "VECTOR",
        vectorKnowledgeBaseConfiguration: {
          embeddingModelArn: `arn:aws:bedrock:${this.region}::foundation-model/amazon.titan-embed-text-v2:0`,
        },
      },
      storageConfiguration: {
        type: "S3_VECTORS",
        s3VectorsConfiguration: {
          vectorBucketArn: vectorBucket.attrVectorBucketArn,
          indexName: indexName,
        },
      },
    })
    knowledgeBase.node.addDependency(vectorIndex)
    knowledgeBase.node.addDependency(knowledgeBaseRole)

    // S3 data source for Bedrock-managed ingestion (triggered via start_ingestion_job)
    const dataSource = new bedrock.CfnDataSource(this, "S3DataSource", {
      knowledgeBaseId: knowledgeBase.attrKnowledgeBaseId,
      name: "S3DataSource",
      description: "S3 data source for document ingestion",
      dataSourceConfiguration: {
        type: "S3",
        s3Configuration: {
          bucketArn: dataBucket.bucketArn,
        },
      },
    })

    // Store Knowledge Base ID in SSM for agent access
    new ssm.StringParameter(this, "KnowledgeBaseIdParam", {
      parameterName: `/${stackNameBase}-kb/knowledge-base-id`,
      stringValue: knowledgeBase.attrKnowledgeBaseId,
      description: "Bedrock Knowledge Base ID for restaurant information",
    })

    // Outputs with exportNames for cross-stack references
    new cdk.CfnOutput(this, "DataBucketName", {
      value: dataBucket.bucketName,
      description: "S3 bucket for knowledge base data",
      exportName: `${stackNameBase}-kb-DataBucketName`,
    })

    new cdk.CfnOutput(this, "KnowledgeBaseId", {
      value: knowledgeBase.attrKnowledgeBaseId,
      description: "Bedrock Knowledge Base ID",
      exportName: `${stackNameBase}-kb-KnowledgeBaseId`,
    })

    new cdk.CfnOutput(this, "DataSourceId", {
      value: dataSource.attrDataSourceId,
      description: "Bedrock Data Source ID",
      exportName: `${stackNameBase}-kb-DataSourceId`,
    })
  }
}
