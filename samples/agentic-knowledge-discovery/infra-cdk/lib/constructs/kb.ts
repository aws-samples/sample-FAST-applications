import * as cdk from "aws-cdk-lib"
import * as s3 from "aws-cdk-lib/aws-s3"
import * as s3deploy from "aws-cdk-lib/aws-s3-deployment"
import * as iam from "aws-cdk-lib/aws-iam"
import * as cr from "aws-cdk-lib/custom-resources"
import { bedrock } from "@cdklabs/generative-ai-cdk-constructs"
import { Construct } from "constructs"
import { AppConfig } from "../utils/config-manager"
import * as path from "path"
import * as crypto from "crypto"

export interface KbConstructProps {
  config: AppConfig
}

/**
 * A Bedrock Knowledge Base over OpenSearch Serverless (created automatically by
 * the construct) holding the sample's unstructured documents. The documents and
 * their .metadata.json sidecars are uploaded from data/documents and ingested at
 * deploy via a StartIngestionJob custom resource. The metadata keys match the
 * Postgres columns, so doc_search and structured_search filter consistently and
 * records.doc_id links a metadata row to its document.
 */
export class KbConstruct extends Construct {
  public readonly knowledgeBase: bedrock.VectorKnowledgeBase
  public readonly knowledgeBaseId: string
  /** Bucket holding the source documents (for presigned citation URLs). */
  public readonly documentsBucket: s3.Bucket

  constructor(scope: Construct, id: string, props: KbConstructProps) {
    super(scope, id)

    // Bedrock Data Automation parses multimodal (scanned) PDFs and writes the
    // extracted images to this supplemental storage bucket, which the KB then
    // indexes alongside the text.
    const supplementalBucket = new s3.Bucket(this, "SupplementalBucket", {
      bucketName: `${props.config.stack_name_base}-kb-multimodal-${cdk.Stack.of(this).account}`,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
    })

    // Amazon Nova Multimodal Embeddings maps text and document images into one
    // semantic space, which pairs with the Bedrock Data Automation multimodal
    // parsing below.
    // Use the 3072-dim variant: the construct sizes the vector index from
    // vectorDimensions but only forwards an output-dimension override to Titan
    // v2, so Nova emits its native 3072-dim vectors. Matching the index to 3072
    // keeps them consistent.
    const embeddingsModel = bedrock.BedrockFoundationModel.AMAZON_NOVA2_MULTIMODAL_V1_3072
    // Hierarchical chunking: embed small child chunks for precise matching but
    // return the larger parent chunk for context. Better than fixed-size for
    // long, structured report-style documents. (Bedrock's default strategy is
    // fixed-size 300/20%, not hierarchical.)
    const chunking = { overlapTokens: 60, maxParentTokenSize: 1500, maxChildTokenSize: 300 }
    const parsingId = "bda-multimodal"

    // Changing the embedding model, chunking, or parsing forces Bedrock to
    // replace the knowledge base, and Bedrock replaces via create-before-delete
    // with a required unique name. A stable name would collide with the outgoing
    // KB, so derive the name from a fingerprint of that config: any such change
    // yields a new name and the replacement succeeds cleanly.
    const kbFingerprint = crypto
      .createHash("sha256")
      .update([embeddingsModel.modelId, JSON.stringify(chunking), parsingId].join("|"))
      .digest("hex")
      .slice(0, 8)

    // NOTE: the construct's OpenSearch vector index cannot change its dimension
    // in place, so switching embedding dimensions requires recreating the KB
    // subtree. Bumping this construct id forces a clean create of a fresh KB,
    // collection, index, and data source (and deletes the old ones).
    this.knowledgeBase = new bedrock.VectorKnowledgeBase(this, "KnowledgeBaseNova", {
      name: `${props.config.stack_name_base}-kb-${kbFingerprint}`,
      embeddingsModel,
      instruction:
        "Use this knowledge base to answer questions from the text of the sample documents. " +
        "Each document has metadata (doc_id, domain, doc_type, num_pages, title) that can be " +
        "used to filter results.",
      supplementalDataStorageLocations: [
        // Must be a bucket root — Bedrock rejects a sub-folder in this URI.
        bedrock.SupplementalDataStorageLocation.s3({
          uri: `s3://${supplementalBucket.bucketName}`,
        }),
      ],
    })
    this.knowledgeBaseId = this.knowledgeBase.knowledgeBaseId

    // The KB role needs to write the extracted multimodal images.
    supplementalBucket.grantReadWrite(this.knowledgeBase.role)

    // Documents bucket, seeded from data/documents (text + .metadata.json sidecars).
    const docsBucket = new s3.Bucket(this, "DocumentsBucket", {
      bucketName: `${props.config.stack_name_base}-kb-docs-${cdk.Stack.of(this).account}`,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
    })
    this.documentsBucket = docsBucket

    const deployment = new s3deploy.BucketDeployment(this, "DeployDocuments", {
      sources: [
        s3deploy.Source.asset(path.join(__dirname, "../../../data/documents")), // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal
      ],
      destinationBucket: docsBucket,
      destinationKeyPrefix: "documents/",
      // The sample PDFs total tens of MiB. The default 128 MB deployment Lambda
      // is network-throttled and cannot finish the upload in one invocation, so
      // give it full CPU/network and enough scratch space to unzip the asset.
      memoryLimit: 1024,
      ephemeralStorageSize: cdk.Size.mebibytes(1024),
    })

    const dataSource = this.knowledgeBase.addS3DataSource({
      bucket: docsBucket,
      dataSourceName: "documents-kb",
      chunkingStrategy: bedrock.ChunkingStrategy.hierarchical(chunking),
      // The sample PDFs are scanned/image-based (text + tables + charts), so use
      // Bedrock Data Automation for multimodal parsing. BDA is the recommended
      // parser for scanned/complex documents and falls back to the default text
      // parser on any per-file failure, so every document is still ingested.
      parsingStrategy: bedrock.ParsingStrategy.bedrockDataAutomation(),
    })

    // Ingestion is not automatic — start a sync once the docs are uploaded and
    // whenever they change (PhysicalResourceId keyed on the deployment hash).
    const ingest = new cr.AwsCustomResource(this, "StartIngestion", {
      onCreate: {
        service: "bedrock-agent",
        action: "startIngestionJob",
        parameters: {
          knowledgeBaseId: this.knowledgeBaseId,
          dataSourceId: dataSource.dataSourceId,
        },
        physicalResourceId: cr.PhysicalResourceId.of(`ingest-${docsBucket.bucketName}`),
      },
      onUpdate: {
        service: "bedrock-agent",
        action: "startIngestionJob",
        parameters: {
          knowledgeBaseId: this.knowledgeBaseId,
          dataSourceId: dataSource.dataSourceId,
        },
        physicalResourceId: cr.PhysicalResourceId.of(`ingest-${docsBucket.bucketName}`),
      },
      policy: cr.AwsCustomResourcePolicy.fromStatements([
        new iam.PolicyStatement({
          effect: iam.Effect.ALLOW,
          actions: ["bedrock:StartIngestionJob"],
          resources: [this.knowledgeBase.knowledgeBaseArn],
        }),
      ]),
      installLatestAwsSdk: false,
    })
    ingest.node.addDependency(deployment)
    ingest.node.addDependency(dataSource)

    new cdk.CfnOutput(this, "KnowledgeBaseId", {
      value: this.knowledgeBaseId,
      description: "Bedrock Knowledge Base id (doc_search)",
    })
  }
}
