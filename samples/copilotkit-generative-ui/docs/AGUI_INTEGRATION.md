# AG-UI Integration with CopilotKit

This guide explains how this sample connects the browser to AgentCore using the AG-UI protocol and CopilotKit, and why the integration requires a server-side bridge rather than a direct browser connection.

## Why a bridge is needed

AgentCore Runtime requires requests to be authenticated with SigV4 or OAuth 2.0. Browsers cannot produce these signatures, so a direct connection from the frontend to AgentCore is not possible.

This sample solves this with a lightweight **CopilotKit Runtime Lambda** that sits between the browser and AgentCore:

```
Browser → CopilotKit Runtime Lambda (Node.js) → AgentCore Runtime → Agent Container
```

The Lambda accepts standard HTTP requests from the browser (authenticated with a Cognito Bearer token), then forwards them to AgentCore using proper AWS authentication. AgentCore validates the JWT, isolates the session, and proxies the request to the agent container.

## What CopilotKit Runtime provides

The Lambda doesn't just relay traffic. It provides the full CopilotKit middleware layer — generative UI, shared state, human-in-the-loop, and message routing. This is the same runtime you'd use with any AG-UI agent.

See [COPILOTKIT_FEATURES.md](COPILOTKIT_FEATURES.md) for details on each feature.

## AgentCoreRunner

When CopilotKit reconnects to an existing thread (e.g. on page refresh), it calls `connect()` to replay the conversation history. AgentCore's memory layer replays history as a `MESSAGES_SNAPSHOT` event, but two issues arise that require a custom runner:

1. **Missing tool-call results** — AgentCore omits `TOOL_CALL_RESULT` events from replayed history. CopilotKit needs these to reconcile its internal message state. The runner synthesises empty results for every past tool call before emitting the snapshot.

2. **Unknown threads** — CopilotKit may call `connect()` before any `run()` has happened (e.g. on first page load). Without handling this, the base runner errors. The `AgentCoreRunner` returns an empty snapshot instead.

The `AgentCoreRunner` class in `infra-cdk/lambdas/copilotkit-runtime/src/runtime.ts` extends `InMemoryAgentRunner` to handle both cases.

## Auth flow

```
1. User signs in via Cognito Hosted UI → receives OIDC access token
2. Frontend passes token as Authorization: Bearer <token> to CopilotKit Lambda
3. Lambda forwards the token to AgentCore Runtime
4. AgentCore validates the JWT and makes the sub claim available to the agent
5. Agent extracts user identity from the JWT sub claim (not the request payload)
   — this prevents impersonation via prompt injection
```
