# Project Overview: Fullstack AgentCore Solution Template (FAST)

This demo is an AgentCore AWS Specialist Agent application built on top of AWS Labs' "Fullstack Solution Template for Agentcore" (known as FAST), with additional features layered on for demo showcases. The app name shown in the UI is **"AgentCore AWS Specialist Agent"** (the display name in the browser tab and chat header). In the codebase, the underlying foundation is still referred to as FAST.

## What it can do

From a browser chat UI, you can converse with a Strands Agent running on Amazon Bedrock AgentCore Runtime. The Agent provides the following:

- **AWS expertise**: Answers using the AWS MCP Server (documentation search, regional availability, AWS API execution) and 43 AWS skills (SKILL.md-based playbooks)
- **Web search**: Retrieves information newer than the training data, with citations, using AgentCore Web Search (a managed connector that reached GA in 2026)
- **Memory**: Short-term memory (within-session conversation) and long-term memory (cross-session, per-user fact recall) via AgentCore Memory
- **Code execution**: Sandboxed Python execution via AgentCore Code Interpreter
- **Access control**: Per-tool ABAC via Cedar policies using an attribute (department) derived from Cognito groups
- **Model selection**: Switch between Claude (Fable 5 / Opus 4.8 / Sonnet 5 / Sonnet 4.6 / Haiku 4.5) and the OpenAI GPT family, per turn, from the UI

## What FAST, the base, is

FAST is a starter template for standing up an AgentCore full-stack application in a few days. Its design principles are a "secure baseline (Cognito + Gateway + Cedar work by default)" and "vibe-codability" (concentrating best practices in documentation rather than code, so AI coding assistants can read and extend them). Upstream FAST is Agent SDK agnostic and includes multiple patterns (Strands / LangGraph / Claude Agent SDK), but this demo uses only strands-single-agent, so the other patterns have been removed.

## Extensions in this demo (differences from FAST)

Relative to vanilla FAST, the following were added in phases (see the "Design notes" section of each file under repo/docs/ for the design rationale).

1. Enabling long-term memory (LTM)
2. Adding Gateway targets for the AWS MCP Server / Strands documentation MCP / LTM meta-recall MCP
3. Adding a Gateway target for the AgentCore Web Search connector
4. Moving to VPC mode and mounting Skills via S3 Files (/mnt/skills)
5. A conversation history sidebar (ChatGPT-style UI)
6. A model selector (Claude + OpenAI via Bedrock)
7. VPC cold start mitigation (extended lifecycle + speculative pre-warm from the front end)
8. This fast-project-guide skill itself (the demo's self-documentation)

## Files to read next

- Architecture and authentication flow → architecture.md
- Details of demo features → demo-features.md
- Configuration values → configuration.md
- Actual code → via code-map.md to repo/
