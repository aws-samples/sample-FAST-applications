import * as fs from "fs";
import * as path from "path";
import * as yaml from "yaml";

const MAX_STACK_NAME_BASE_LENGTH = 35;

export type DeploymentType = "docker" | "zip";

/**
 * Network mode for the AgentCore Runtime.
 * - PUBLIC: Runtime is accessible over the public internet (default).
 * - VPC: Runtime is deployed into a user-provided VPC for private network isolation.
 */
export type NetworkMode = "PUBLIC" | "VPC";

/**
 * VPC configuration for deploying the AgentCore Runtime into an existing VPC.
 * Required when network_mode is "VPC" and vpc_management is "EXISTING".
 */
export interface VpcConfig {
	/** The ID of the existing VPC to deploy into (e.g. "vpc-0abc1234def56789a"). */
	vpc_id: string;
	/** List of subnet IDs within the VPC where the runtime will be placed. */
	subnet_ids: string[];
	/** Optional list of security group IDs. If omitted, a default security group is created. */
	security_group_ids?: string[];
}

/**
 * How the VPC used by the runtime is managed.
 * - CDK: this app creates the VPC + endpoints + NAT (VpcStack). Default.
 * - EXISTING: reuse a user-provided VPC via backend.vpc (fromLookup).
 */
export type VpcManagement = "CDK" | "EXISTING";

/**
 * Skills configuration.
 * When enabled, the vendored AWS skills are synced to S3, mounted into the
 * runtime via S3 Files at mount_path, and exposed through the Strands
 * AgentSkills plugin.
 */
export interface SkillsConfig {
	/** Whether to provision S3 Files and mount the skills into the runtime. Defaults to false. */
	enabled?: boolean;
	/** Single-level mount path (/mnt/[a-zA-Z0-9._-]+). Defaults to "/mnt/skills". */
	mount_path?: string;
}

/**
 * Runtime lifecycle settings. Both values are seconds in the
 * service-accepted range 60-28800.
 */
export interface RuntimeLifecycleConfig {
	/** Idle time after which a runtime session's microVM is terminated. Defaults to DEFAULT_IDLE_RUNTIME_SESSION_TIMEOUT_SECONDS. */
	idle_session_timeout_seconds?: number;
	/** Maximum microVM lifetime. When omitted the service default (28800s) applies. */
	max_lifetime_seconds?: number;
}

/**
 * Service-accepted bounds for both lifecycle timeouts (seconds).
 */
export const LIFECYCLE_TIMEOUT_MIN_SECONDS = 60;
export const LIFECYCLE_TIMEOUT_MAX_SECONDS = 28800;

/**
 * Default idleRuntimeSessionTimeout applied to every runtime.
 * Deliberately longer than the 900s service default: demo conversations pause
 * for longer than 15 minutes, and each re-warm in the closed VPC costs an ENI
 * provision + image pull (~25s observed). Idle compute is memory-only billing,
 * so the extra hour is cheap.
 */
export const DEFAULT_IDLE_RUNTIME_SESSION_TIMEOUT_SECONDS = 3600;

/**
 * Default IPv4 CIDR for the CDK-managed runtime VPC (network_mode: VPC,
 * vpc_management: CDK). Used when backend.vpc_cidr is not set, so existing
 * deployments that never specified a CIDR keep 10.20.0.0/16 and see no VPC diff.
 */
export const DEFAULT_RUNTIME_VPC_CIDR = "10.20.0.0/16";

/**
 * Default Availability Zone *names* for the CDK-managed runtime VPC. These map to
 * AgentCore-supported AZ *ids* (us-east-1: use1-az1/az2/az4) in the account this
 * template was validated against: us-east-1b -> use1-az1, us-east-1d -> use1-az4.
 * The name -> id mapping is account-specific, so a different account MUST
 * override backend.availability_zones with names that resolve to the supported
 * ids. Used when backend.availability_zones is not set.
 */
export const DEFAULT_RUNTIME_AZS = ["us-east-1b", "us-east-1d"];

export interface AppConfig {
	stack_name_base: string;
	admin_user_email?: string | null;
	backend: {
		pattern: string;
		deployment_type: DeploymentType;
		/** Network mode for the AgentCore Runtime. Defaults to "PUBLIC". */
		network_mode: NetworkMode;
		/** How the VPC is managed when network_mode is "VPC". Defaults to "CDK". */
		vpc_management: VpcManagement;
		/** VPC configuration. Required when network_mode is "VPC" and vpc_management is "EXISTING". */
		vpc?: VpcConfig;
		/**
		 * IPv4 CIDR for the CDK-managed runtime VPC (vpc_management: CDK). Defaults
		 * to DEFAULT_RUNTIME_VPC_CIDR. Set this to a non-overlapping range when
		 * deploying a second environment into the same account/region, or when the
		 * default range collides with existing networks in a third-party account.
		 * Changing the CIDR of an already-deployed VPC replaces it (and everything
		 * in it), so only set this on a fresh deployment.
		 */
		vpc_cidr?: string;
		/**
		 * Availability Zone names for the CDK-managed runtime VPC. Defaults to
		 * DEFAULT_RUNTIME_AZS. AgentCore Runtime VPC mode only supports specific AZ
		 * *ids* per region (us-east-1: use1-az1/az2/az4); because the AZ name -> id
		 * mapping is account-specific, a third-party account must set names that
		 * resolve to supported ids. Exactly two names are required.
		 */
		availability_zones?: string[];
		/** Skills (S3 Files mount) configuration. Disabled by default. */
		skills?: SkillsConfig;
		/**
		 * Runtime session lifecycle overrides. Applied to all
		 * AgentCore Runtimes in the stack. When omitted, idle_session_timeout_seconds
		 * defaults to DEFAULT_IDLE_RUNTIME_SESSION_TIMEOUT_SECONDS and
		 * max_lifetime_seconds is left to the service default.
		 */
		runtime_lifecycle?: RuntimeLifecycleConfig;
		/**
		 * Enable long-term memory (SemanticMemoryStrategy) for the agent.
		 * When true, the agent extracts and retrieves facts across sessions.
		 * This incurs additional costs: $0.75/1,000 records stored + $0.50/1,000 retrievals.
		 * Defaults to false.
		 */
		use_long_term_memory: boolean;
		/**
		 * Number of facts to retrieve per turn when long-term memory is enabled.
		 * Maps to the top_k parameter of RetrievalConfig. Defaults to 10.
		 */
		ltm_top_k: number;
		/**
		 * Minimum similarity threshold for long-term memory retrieval.
		 * Maps to the relevance_score parameter of RetrievalConfig. Defaults to 0.3.
		 */
		ltm_relevance_score: number;
	};
}

export class ConfigManager {
	private config: AppConfig;

	constructor(configFile: string) {
		this.config = this._loadConfig(configFile);
	}

	private _loadConfig(configFile: string): AppConfig {
		let configPath: string;

		// Uses the specified configFile if the file exists
		// otherwise fallsback to existing behavior where the configFile should be
		// named config.yaml and be in the infra-cdk directory. Throws an error if the
		// configFile does not exist and is not the default "config.yaml"
		if (fs.existsSync(configFile)) {
			configPath = configFile;
		} else {
			if (path.basename(configFile) !== "config.yaml") {
				throw new Error(`Configuration file '${configFile}' not found.`);
			}
			const defaultConfigPath = path.join(__dirname, "..", "..", configFile); // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal
			configPath = defaultConfigPath;
		}
		if (!fs.existsSync(configPath)) {
			throw new Error(
				`Configuration file ${configPath} does not exist. Please create config.yaml file.`,
			);
		}

		try {
			const fileContent = fs.readFileSync(configPath, "utf8");
			const parsedConfig = yaml.parse(fileContent) as AppConfig;

			const deploymentType = parsedConfig.backend?.deployment_type || "docker";
			if (deploymentType !== "docker" && deploymentType !== "zip") {
				throw new Error(
					`Invalid deployment_type '${deploymentType}' in ${configPath}. Must be 'docker' or 'zip'.`,
				);
			}

			const stackNameBase = parsedConfig.stack_name_base;
			if (!stackNameBase) {
				throw new Error(`stack_name_base is required in ${configPath}`);
			}
			if (stackNameBase.length > MAX_STACK_NAME_BASE_LENGTH) {
				throw new Error(
					`stack_name_base '${stackNameBase}' is too long (${stackNameBase.length} chars). ` +
						`Maximum length is ${MAX_STACK_NAME_BASE_LENGTH} characters due to AWS AgentCore runtime naming constraints.`,
				);
			}

			// Validate network_mode if provided
			const networkMode = parsedConfig.backend?.network_mode || "PUBLIC";
			if (networkMode !== "PUBLIC" && networkMode !== "VPC") {
				throw new Error(
					`Invalid network_mode '${networkMode}' in ${configPath}. Must be 'PUBLIC' or 'VPC'.`,
				);
			}

			// Validate vpc_management (defaults to CDK).
			const vpcManagement = parsedConfig.backend?.vpc_management || "CDK";
			if (vpcManagement !== "CDK" && vpcManagement !== "EXISTING") {
				throw new Error(
					`Invalid vpc_management '${vpcManagement}' in ${configPath}. Must be 'CDK' or 'EXISTING'.`,
				);
			}

			// Validate VPC configuration when network_mode is VPC.
			// With vpc_management: CDK the VpcStack creates the VPC, so backend.vpc is not required.
			// With vpc_management: EXISTING the user must supply backend.vpc (fromLookup path).
			const vpcConfig = parsedConfig.backend?.vpc;
			if (networkMode === "VPC" && vpcManagement === "EXISTING") {
				if (!vpcConfig) {
					throw new Error(
						`backend.vpc configuration is required in ${configPath} when network_mode is 'VPC' and vpc_management is 'EXISTING'.`,
					);
				}
				if (!vpcConfig.vpc_id) {
					throw new Error(
						`backend.vpc.vpc_id is required in ${configPath} when vpc_management is 'EXISTING'.`,
					);
				}
				if (!vpcConfig.subnet_ids || vpcConfig.subnet_ids.length === 0) {
					throw new Error(
						`backend.vpc.subnet_ids must contain at least one subnet ID in ${configPath} when vpc_management is 'EXISTING'.`,
					);
				}
			}

			// Validate the CDK-managed network overrides (CIDR + AZs). These are
			// optional; when omitted the VpcStack falls back to the DEFAULT_*
			// constants so existing deployments are unaffected.
			const vpcCidr = parsedConfig.backend?.vpc_cidr;
			if (vpcCidr !== undefined && !ConfigManager.isValidIpv4Cidr(vpcCidr)) {
				throw new Error(
					`Invalid backend.vpc_cidr '${vpcCidr}' in ${configPath}. Must be an IPv4 CIDR like '10.30.0.0/16'.`,
				);
			}
			const availabilityZones = parsedConfig.backend?.availability_zones;
			if (availabilityZones !== undefined) {
				if (
					!Array.isArray(availabilityZones) ||
					availabilityZones.length !== 2 ||
					availabilityZones.some(
						(az) => typeof az !== "string" || az.trim().length === 0,
					)
				) {
					throw new Error(
						`backend.availability_zones in ${configPath} must be a list of exactly two AZ names (e.g. ['us-east-1b', 'us-east-1d']).`,
					);
				}
			}

			// Validate the runtime lifecycle overrides. Like the CIDR
			// check this only catches typos before synth; the L2 construct
			// re-validates the same bounds.
			const runtimeLifecycle = parsedConfig.backend?.runtime_lifecycle;
			if (runtimeLifecycle !== undefined) {
				const inBounds = (v: number): boolean =>
					Number.isInteger(v) &&
					v >= LIFECYCLE_TIMEOUT_MIN_SECONDS &&
					v <= LIFECYCLE_TIMEOUT_MAX_SECONDS;
				const idle = runtimeLifecycle.idle_session_timeout_seconds;
				const max = runtimeLifecycle.max_lifetime_seconds;
				if (idle !== undefined && !inBounds(idle)) {
					throw new Error(
						`Invalid backend.runtime_lifecycle.idle_session_timeout_seconds '${idle}' in ${configPath}. Must be an integer between ${LIFECYCLE_TIMEOUT_MIN_SECONDS} and ${LIFECYCLE_TIMEOUT_MAX_SECONDS}.`,
					);
				}
				if (max !== undefined && !inBounds(max)) {
					throw new Error(
						`Invalid backend.runtime_lifecycle.max_lifetime_seconds '${max}' in ${configPath}. Must be an integer between ${LIFECYCLE_TIMEOUT_MIN_SECONDS} and ${LIFECYCLE_TIMEOUT_MAX_SECONDS}.`,
					);
				}
				const effectiveIdle =
					idle ?? DEFAULT_IDLE_RUNTIME_SESSION_TIMEOUT_SECONDS;
				if (max !== undefined && effectiveIdle > max) {
					throw new Error(
						`backend.runtime_lifecycle in ${configPath}: idle_session_timeout_seconds (${effectiveIdle}) must not exceed max_lifetime_seconds (${max}).`,
					);
				}
			}

			return {
				stack_name_base: stackNameBase,
				admin_user_email: parsedConfig.admin_user_email || null,
				backend: {
					pattern: parsedConfig.backend?.pattern || "strands-single-agent",
					deployment_type: deploymentType,
					network_mode: networkMode,
					vpc_management: vpcManagement,
					vpc: vpcConfig,
					vpc_cidr: vpcCidr,
					availability_zones: availabilityZones,
					skills: parsedConfig.backend?.skills,
					runtime_lifecycle: runtimeLifecycle,
					use_long_term_memory:
						parsedConfig.backend?.use_long_term_memory === true,
					ltm_top_k: parsedConfig.backend?.ltm_top_k ?? 10,
					ltm_relevance_score: parsedConfig.backend?.ltm_relevance_score ?? 0.3,
				},
			};
		} catch (error) {
			throw new Error(
				`Failed to parse configuration file ${configPath}: ${error}`,
			);
		}
	}

	/**
	 * Lightweight IPv4 CIDR validation (e.g. "10.30.0.0/16"). Checks four octets
	 * in 0-255 and a prefix length in 0-32. Not a full network-math validation —
	 * just enough to catch typos in config.yaml before synth.
	 */
	private static isValidIpv4Cidr(value: string): boolean {
		const match =
			/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\/(\d{1,2})$/.exec(value);
		if (!match) {
			return false;
		}
		const octets = [match[1], match[2], match[3], match[4]].map(Number);
		if (octets.some((o) => o < 0 || o > 255)) {
			return false;
		}
		const prefix = Number(match[5]);
		return prefix >= 0 && prefix <= 32;
	}

	public getProps(): AppConfig {
		return this.config;
	}

	public get(key: string, defaultValue?: any): any {
		const keys = key.split(".");
		let value: any = this.config;

		for (const k of keys) {
			if (typeof value === "object" && value !== null && k in value) {
				// nosemgrep: javascript.lang.security.audit.prototype-pollution.prototype-pollution-loop.prototype-pollution-loop — iterates over a trusted local YAML config object, not user-controlled input
				value = value[k];
			} else {
				return defaultValue;
			}
		}

		return value;
	}
}
