import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as iam from "aws-cdk-lib/aws-iam";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as s3deploy from "aws-cdk-lib/aws-s3-deployment";
import * as s3files from "aws-cdk-lib/aws-s3files";
import { execFileSync } from "child_process";
import { Construct } from "constructs";
import * as path from "path";
import { AppConfig } from "./utils/config-manager";

export interface SkillsStorageStackProps extends cdk.NestedStackProps {
	config: AppConfig;
	/** Private subnets (one mount target per AZ) — must match the runtime's AZs. */
	privateSubnets: ec2.ISubnet[];
	/** Shared SG (TCP 2049 self-ref) for the mount targets. */
	securityGroup: ec2.ISecurityGroup;
}

/**
 * S3 Files storage for the Skills mount.
 *
 * Creates an S3 bucket holding the vendored skills, an S3 Files file system
 * synced to that bucket, one mount target per private subnet/AZ, and an access
 * point with a fixed POSIX identity. The runtime mounts the access point at
 * /mnt/skills (wired in BackendStack via the L1 escape hatch) and the Strands
 * AgentSkills plugin reads every skill from that single path.
 *
 * Uses the typed L1 constructs from aws-cdk-lib/aws-s3files (CfnFileSystem etc.,
 * present in 2.257) rather than raw CfnResource. The access point ARN is taken
 * from attrAccessPointArn rather than string-built.
 */
export class SkillsStorageStack extends cdk.NestedStack {
	/** ARN of the S3 Files access point, consumed by the runtime mount config. */
	public readonly accessPointArn: string;
	/** file-system ARN (for scoping the runtime's s3files: IAM statement). */
	public readonly fileSystemArn: string;

	constructor(scope: Construct, id: string, props: SkillsStorageStackProps) {
		const description =
			"Fullstack AgentCore Solution Template - Skills Storage (S3 Files)";
		super(scope, id, { ...props, description });

		// 1. Bucket holding the vendored skills. S3 Files requires versioning + SSE.
		//    We use SSE-S3 (default) so the FS service role needs no KMS grant.
		const skillsBucket = new s3.Bucket(this, "SkillsBucket", {
			versioned: true,
			encryption: s3.BucketEncryption.S3_MANAGED,
			blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
			enforceSSL: true,
			removalPolicy: cdk.RemovalPolicy.DESTROY,
			autoDeleteObjects: true,
		});

		// 2. Upload the skills (flat <skill-name>/ layout) under skills/.
		//    The S3 Files root directory is set to /skills so the access point root
		//    maps to these objects; the runtime then sees them flat at /mnt/skills.
		//    Two sources merge into the same prefix: the vendored AWS skills
		//    (skills/agent-toolkit-for-aws/) and the generated fast-project-guide
		//    self-description skill (skills/aws-specialist-agent/build/).
		//    The latter is rebuilt at synth time so a generation
		//    failure fails the synth instead of shipping a stale guide.
		const repoRoot = path.join(__dirname, "..", "..");
		const vendoredSkillsDir = path.join(
			repoRoot,
			"skills",
			"agent-toolkit-for-aws",
		);
		const projectGuideDir = path.join(
			repoRoot,
			"skills",
			"aws-specialist-agent",
			"build",
		);
		execFileSync(
			"python3",
			[path.join(repoRoot, "scripts", "build-project-guide.py")],
			{ stdio: "inherit" },
		);
		const deploySkills = new s3deploy.BucketDeployment(this, "DeploySkills", {
			sources: [
				s3deploy.Source.asset(vendoredSkillsDir), // vendored AWS skills
				s3deploy.Source.asset(projectGuideDir), // generated fast-project-guide
			],
			destinationBucket: skillsBucket,
			destinationKeyPrefix: "skills",
			prune: true,
		});

		// 3. IAM role S3 Files assumes to sync with the bucket. Trust + inline policy
		//    follow the official S3 Files prerequisites (S3 + EventBridge; KMS omitted
		//    because the bucket uses SSE-S3, not SSE-KMS).
		const fsRole = new iam.Role(this, "S3FilesServiceRole", {
			assumedBy: new iam.ServicePrincipal("elasticfilesystem.amazonaws.com", {
				conditions: {
					StringEquals: { "aws:SourceAccount": this.account },
					ArnLike: {
						"aws:SourceArn": `arn:aws:s3files:${this.region}:${this.account}:file-system/*`,
					},
				},
			}),
		});
		fsRole.addToPolicy(
			new iam.PolicyStatement({
				sid: "S3BucketPermissions",
				actions: ["s3:ListBucket", "s3:ListBucketVersions"],
				resources: [skillsBucket.bucketArn],
				conditions: { StringEquals: { "aws:ResourceAccount": this.account } },
			}),
		);
		fsRole.addToPolicy(
			new iam.PolicyStatement({
				sid: "S3ObjectPermissions",
				actions: [
					"s3:AbortMultipartUpload",
					"s3:DeleteObject*",
					"s3:GetObject*",
					"s3:List*",
					"s3:PutObject*",
				],
				resources: [skillsBucket.arnForObjects("*")],
				conditions: { StringEquals: { "aws:ResourceAccount": this.account } },
			}),
		);
		fsRole.addToPolicy(
			new iam.PolicyStatement({
				sid: "EventBridgeManage",
				actions: [
					"events:DeleteRule",
					"events:DisableRule",
					"events:EnableRule",
					"events:PutRule",
					"events:PutTargets",
					"events:RemoveTargets",
				],
				resources: ["arn:aws:events:*:*:rule/DO-NOT-DELETE-S3-Files*"],
				conditions: {
					StringEquals: {
						"events:ManagedBy": "elasticfilesystem.amazonaws.com",
					},
				},
			}),
		);
		fsRole.addToPolicy(
			new iam.PolicyStatement({
				sid: "EventBridgeRead",
				actions: [
					"events:DescribeRule",
					"events:ListRuleNamesByTarget",
					"events:ListRules",
					"events:ListTargetsByRule",
				],
				resources: ["arn:aws:events:*:*:rule/*"],
			}),
		);

		// 4. S3 Files file system synced to the bucket. Bucket takes an ARN.
		//    Depend on deploySkills so the skills are uploaded before the file
		//    system starts its initial sync — otherwise the first session can
		//    mount an empty/partial /mnt/skills. (Bucket-side writes still
		//    materialize into NFS under S3 eventual consistency, so the first
		//    invocation should also tolerate a transient empty listing.)
		const fileSystem = new s3files.CfnFileSystem(this, "FileSystem", {
			bucket: skillsBucket.bucketArn,
			roleArn: fsRole.roleArn,
			acceptBucketWarning: true,
		});
		fileSystem.node.addDependency(fsRole);
		fileSystem.node.addDependency(deploySkills);
		this.fileSystemArn = fileSystem.attrFileSystemArn;

		// 5. One mount target per private subnet (AgentCore mounts per-AZ; the subnets
		//    must be the same AZs the runtime ENIs use). SG allows 2049 self-ref.
		const mountTargets = props.privateSubnets.map(
			(subnet, i) =>
				new s3files.CfnMountTarget(this, `MountTarget${i}`, {
					fileSystemId: fileSystem.attrFileSystemId,
					subnetId: subnet.subnetId,
					securityGroups: [props.securityGroup.securityGroupId],
				}),
		);

		// 6. Access point with a fixed POSIX identity (1000:1000 to match the
		//    Dockerfile's bedrock_agentcore user) rooted at /skills.
		const accessPoint = new s3files.CfnAccessPoint(this, "AccessPoint", {
			fileSystemId: fileSystem.attrFileSystemId,
			posixUser: { uid: "1000", gid: "1000" },
			rootDirectory: {
				path: "/skills",
				creationPermissions: {
					ownerUid: "1000",
					ownerGid: "1000",
					permissions: "0755",
				},
			},
		});
		mountTargets.forEach((mt) => accessPoint.addDependency(mt));
		this.accessPointArn = accessPoint.attrAccessPointArn;

		cdk.Tags.of(this).add("Project", "FAST");
		cdk.Tags.of(this).add("Purpose", "demo");
		cdk.Tags.of(this).add("ManagedBy", "CDK");
	}
}
