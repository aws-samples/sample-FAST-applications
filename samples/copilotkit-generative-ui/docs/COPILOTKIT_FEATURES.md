# CopilotKit Features

This sample demonstrates these CopilotKit features on top of AgentCore. Each is a thin layer on the standard CopilotKit API — full documentation is on the CopilotKit docs site.

## Generative UI

The agent composes UI inline in the chat from a **component catalog**. The catalog is declared once in the browser and given to the provider. It starts from `standardUI`, CopilotKit's default catalog (layout, text, inputs, `Table`, `Chart`, media and more), and adds the app's own components on top.

```typescript
// frontend/src/lib/generative-ui.ts
import { createGenerativeUI, standardUI } from "@copilotkit/react-core/v2/generative-ui"

export const generativeUI = createGenerativeUI({
  id: "fast/copilotkit-generative-ui@1",
  extends: [standardUI.omit(["Map"])],
  components: {
    MetricCard: {
      component: MetricCard,
      description: "A KPI tile: one headline number with its label and an optional change.",
      props: MetricCardPropsSchema, // a zod object schema
    },
  },
})
```

```tsx
<CopilotKitProvider runtimeUrl={runtimeUrl} generativeUI={{ ui: generativeUI }}>
```

How a render reaches the screen:

1. The provider sends the catalog with every run.
2. The CopilotKit Runtime gives the agent a `render_ui` tool and a guide to the catalog (as AG-UI context).
3. The agent calls `render_ui` with operations that create and update a surface. The runtime validates each one against the catalog and streams it to the browser, so the UI builds up while the model is still writing.

Nothing is registered per component on the agent side: both agents pick up `render_ui` like any other frontend tool. To add a component, add it to `components` with a `description` and a zod `props` schema. To restyle a default component, pass `overrides: { Card: { component: MyCard } }`. `Map` is left out because it needs the optional `maplibre-gl` package.

> The component catalog API is marked experimental in CopilotKit 1.89. Hook-based generative UI (`useComponent`, which binds one tool to one component) still works and can be used alongside it.

→ [CopilotKit Generative UI docs](https://docs.copilotkit.ai/generative-ui/components)

### MCP Apps

The runtime also connects to an MCP server that serves interactive UI ([Excalidraw](https://mcp.excalidraw.com) by default, override with `MCP_SERVER_URL`). It is configured with the `mcpApps` option of `CopilotRuntime` and rendered by CopilotKit without frontend code.

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