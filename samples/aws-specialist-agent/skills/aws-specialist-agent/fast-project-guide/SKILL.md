---
name: fast-project-guide
description: Explains the overall structure, architecture, authentication flow, AWS services used, configuration values, design decisions, and implementation code of this demo application itself (FAST - Fullstack AgentCore Solution Template). Always use this skill when a question is about this demo itself, such as "how does this demo work", "what AWS services does it use", "why was it designed this way", "how do Gateway / Memory / Skills / Cedar / model selection work", or "show me the actual code or configuration".
version: 1
---

# FAST Project Guide

This skill is the reference manual for the demo application that is currently running. Depending on the type of question, read the files under references/ with `file_read` and answer based on their content (all paths are absolute paths under the same directory as this SKILL.md).

## Routing table

| Question type                                                        | File to read                                                   |
| -------------------------------------------------------------------- | -------------------------------------------------------------- |
| Big picture, what it can do, purpose of the project                  | references/overview.md                                         |
| Architecture, authentication/authorization flow, AWS services used   | references/architecture.md                                     |
| Features shown in the demo (memory, skills, MCP, ABAC, model select) | references/demo-features.md                                    |
| Configuration values, config.yaml, model registry, environment vars  | references/configuration.md                                    |
| Official feature docs (Gateway / Memory / Cedar / Streaming, etc.)   | the relevant document under references/repo/docs/              |
| Implementation details, actual code, actual configuration files      | references/code-map.md → the relevant file in references/repo/ |

## Answering principles

- Do not answer from guesswork; always read the relevant file with `file_read` before answering.
- When asked about implementation or configuration values, use the reference documents to narrow down where to look, then read the actual code under references/repo/ to confirm before answering (repo/ is a mirror of the source code that is actually deployed).
- When asked "why was it designed this way", read the "Design notes" section of each `*.md` under references/repo/docs/ (DEPLOYMENT.md, GATEWAY.md, MEMORY_INTEGRATION.md, etc.).
- For general AWS questions that are not specific to this demo, switch away from this skill to the aws\_\_\_ tools or other skills.
