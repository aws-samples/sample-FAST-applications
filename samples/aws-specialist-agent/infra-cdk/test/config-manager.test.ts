// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import {
	ConfigManager,
	DEFAULT_IDLE_RUNTIME_SESSION_TIMEOUT_SECONDS,
	DEFAULT_RUNTIME_AZS,
	DEFAULT_RUNTIME_VPC_CIDR,
} from "../lib/utils/config-manager";

/**
 * Write a temp config file and return its path. ConfigManager reads from disk, so
 * each test materializes a YAML file in a fresh temp dir.
 */
function writeConfig(contents: string): string {
	const dir = fs.mkdtempSync(path.join(os.tmpdir(), "fast-config-"));
	const file = path.join(dir, "config.yaml");
	fs.writeFileSync(file, contents);
	return file;
}

const BASE = `stack_name_base: TEST-stack
backend:
  pattern: strands-single-agent
  network_mode: VPC
  vpc_management: CDK
`;

describe("ConfigManager network overrides", () => {
	test("omitting CIDR/AZ keys leaves them undefined (callers apply DEFAULT_* fallback)", () => {
		const cfg = new ConfigManager(writeConfig(BASE)).getProps();
		// The config manager itself does not inject defaults; the stacks do via
		// `?? DEFAULT_*`. Asserting undefined here guarantees the fallback path
		// (and thus no VPC diff on existing deployments) stays intact.
		expect(cfg.backend.vpc_cidr).toBeUndefined();
		expect(cfg.backend.availability_zones).toBeUndefined();
	});

	test("the DEFAULT_* constants match the original hardcoded values", () => {
		// Guards against an accidental change to the defaults, which would silently
		// replace already-deployed VPCs.
		expect(DEFAULT_RUNTIME_VPC_CIDR).toBe("10.20.0.0/16");
		expect(DEFAULT_RUNTIME_AZS).toEqual(["us-east-1b", "us-east-1d"]);
	});

	test("valid overrides are parsed through", () => {
		const cfg = new ConfigManager(
			writeConfig(
				`${BASE}  vpc_cidr: 10.30.0.0/16
  availability_zones:
    - us-east-1a
    - us-east-1c
`,
			),
		).getProps();
		expect(cfg.backend.vpc_cidr).toBe("10.30.0.0/16");
		expect(cfg.backend.availability_zones).toEqual([
			"us-east-1a",
			"us-east-1c",
		]);
	});

	test.each([
		["not-a-cidr"],
		["10.30.0.0"],
		["10.30.0.0/33"],
		["999.0.0.0/16"],
	])("rejects invalid vpc_cidr %s", (bad) => {
		expect(
			() => new ConfigManager(writeConfig(`${BASE}  vpc_cidr: ${bad}\n`)),
		).toThrow(/Invalid backend.vpc_cidr/);
	});

	test("rejects availability_zones with the wrong length", () => {
		expect(
			() =>
				new ConfigManager(
					writeConfig(`${BASE}  availability_zones:\n    - us-east-1b\n`),
				),
		).toThrow(/exactly two AZ names/);
	});
});

describe("ConfigManager runtime lifecycle", () => {
	test("omitting runtime_lifecycle leaves it undefined (stack applies the default idle timeout)", () => {
		const cfg = new ConfigManager(writeConfig(BASE)).getProps();
		expect(cfg.backend.runtime_lifecycle).toBeUndefined();
	});

	test("the default idle timeout is one hour", () => {
		// Guards against an accidental change: shortening it silently reintroduces
		// mid-conversation cold starts (the problem this lifecycle setting exists to fix).
		expect(DEFAULT_IDLE_RUNTIME_SESSION_TIMEOUT_SECONDS).toBe(3600);
	});

	test("valid overrides are parsed through", () => {
		const cfg = new ConfigManager(
			writeConfig(
				`${BASE}  runtime_lifecycle:
    idle_session_timeout_seconds: 1800
    max_lifetime_seconds: 14400
`,
			),
		).getProps();
		expect(cfg.backend.runtime_lifecycle).toEqual({
			idle_session_timeout_seconds: 1800,
			max_lifetime_seconds: 14400,
		});
	});

	test.each([
		["idle_session_timeout_seconds", 59],
		["idle_session_timeout_seconds", 28801],
		["max_lifetime_seconds", 0],
		["max_lifetime_seconds", 30000],
	])("rejects out-of-range %s = %d", (key, bad) => {
		expect(
			() =>
				new ConfigManager(
					writeConfig(`${BASE}  runtime_lifecycle:\n    ${key}: ${bad}\n`),
				),
		).toThrow(/runtime_lifecycle/);
	});

	test("rejects idle timeout exceeding max lifetime", () => {
		expect(
			() =>
				new ConfigManager(
					writeConfig(
						`${BASE}  runtime_lifecycle:
    idle_session_timeout_seconds: 7200
    max_lifetime_seconds: 3600
`,
					),
				),
		).toThrow(/must not exceed/);
	});

	test("rejects a max lifetime below the default idle timeout when idle is omitted", () => {
		// The stack would fall back to the 3600s default idle, which the service
		// rejects against a smaller maxLifetime — catch it at config load instead.
		expect(
			() =>
				new ConfigManager(
					writeConfig(
						`${BASE}  runtime_lifecycle:\n    max_lifetime_seconds: 1800\n`,
					),
				),
		).toThrow(/must not exceed/);
	});
});
