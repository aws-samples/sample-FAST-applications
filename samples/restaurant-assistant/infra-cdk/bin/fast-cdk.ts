#!/usr/bin/env node
import * as cdk from "aws-cdk-lib"
import { FastMainStack } from "../lib/fast-main-stack"
import { KnowledgeBaseStack } from "../lib/knowledge-base-stack"
import { ConfigManager } from "../lib/utils/config-manager"

// Load configuration using ConfigManager
const configManager = new ConfigManager("config.yaml")

// Initial props consist of configuration parameters
const props = configManager.getProps()

const app = new cdk.App()

const env = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region: process.env.CDK_DEFAULT_REGION,
}

// Deploy Knowledge Base as a separate top-level stack (must be deployed first)
const knowledgeBaseStack = new KnowledgeBaseStack(app, `${props.stack_name_base}-kb`, {
  config: props,
  env,
})

// Deploy the main stack (depends on KB stack outputs via Fn.importValue)
const mainStack = new FastMainStack(app, props.stack_name_base, {
  config: props,
  env,
})
mainStack.addDependency(knowledgeBaseStack)

app.synth()
