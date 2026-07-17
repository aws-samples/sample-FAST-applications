#!/usr/bin/env node
import * as cdk from "aws-cdk-lib";
import { FastMainStack } from "../lib/fast-main-stack";
import { ConfigManager } from "../lib/utils/config-manager";

// Load configuration using ConfigManager. The config file defaults to
// config.yaml but can be overridden with the CONFIG_FILE env var so a second
// environment (e.g. dev) can be deployed from its own file without editing the
// production config: `CONFIG_FILE=config.dev.yaml npx cdk deploy --all`.
const configManager = new ConfigManager(
	process.env.CONFIG_FILE ?? "config.yaml",
);

// Initial props consist of configuration parameters
const props = configManager.getProps();

const app = new cdk.App();

const env = {
	account: process.env.CDK_DEFAULT_ACCOUNT,
	region: process.env.CDK_DEFAULT_REGION,
};

// Deploy the Amplify-based main stack. When network_mode is "VPC" with
// vpc_management "CDK", FastMainStack also creates the VPC as a nested stack.
// OpenAI (GPT-5.x) needs no extra stacks: the models are served from the
// in-region bedrock-mantle endpoint, which VpcStack exposes as a regular
// interface endpoint (the former us-east-2 peering stacks gated on
// OPENAI_MANTLE are gone).
new FastMainStack(app, props.stack_name_base, {
	config: props,
	env,
});

app.synth();
