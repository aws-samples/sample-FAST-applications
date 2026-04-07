# CopilotKit Features

This sample demonstrates four CopilotKit features on top of AgentCore. Each is a thin layer on the standard CopilotKit API — full documentation is on the CopilotKit docs site.

## Generative UI

The agent renders React components directly in the chat by calling frontend tools. Components are registered in the browser with `useComponent` and invoked by the agent via the AG-UI tools field.

```typescript
useComponent({
  name: "barChart",
  render: ({ data, title }) => <BarChart data={data} title={title} />,
});
```

→ [CopilotKit Generative UI docs](https://docs.copilotkit.ai/langgraph/generative-ui/your-components)

## Shared State

The agent and the UI share structured state bidirectionally. The agent writes to state via tools; the frontend reads and writes via `useAgent`.

```typescript
const { agent } = useAgent();
const todos = agent.state?.todos ?? [];
agent.setState({ todos: updatedTodos });
```

→ [CopilotKit Shared State docs](https://docs.copilotkit.ai/langgraph/shared-state/in-app-agent-read)

## Human-in-the-loop

The agent can pause mid-run and render an interactive component in the chat, waiting for user input before continuing.

```typescript
useHumanInTheLoop({
  name: "scheduleTime",
  render: ({ respond, args }) => <TimePicker onSelect={respond} />,
});
```

→ [CopilotKit Human-in-the-loop docs](https://docs.copilotkit.ai/langgraph/human-in-the-loop/interrupt-flow)

## Tool rendering

By default, all agent tool calls are rendered inline with `useDefaultRenderTool` — showing the tool name, status, and arguments as the agent runs.

```typescript
useDefaultRenderTool({
  render: ({ name, status, parameters }) => (
    <ToolReasoning name={name} status={status} args={parameters} />
  ),
});
```

→ [CopilotKit Tool Rendering docs](https://docs.copilotkit.ai/langgraph/generative-ui/tool-rendering)