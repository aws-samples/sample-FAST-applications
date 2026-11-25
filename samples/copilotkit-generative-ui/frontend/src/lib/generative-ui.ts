import { createGenerativeUI, standardUI } from "@copilotkit/react-core/v2/generative-ui"
import { MetricCard, MetricCardPropsSchema } from "@/components/generative-ui/MetricCard"

/**
 * The generative UI catalog: every component the agent may render.
 *
 * `standardUI` is CopilotKit's default component catalog (layout, text, inputs,
 * tables, charts, media, ...). `components` adds this app's own components on
 * top of it. The agent composes them freely with the `render_ui` tool, and the
 * CopilotKit runtime validates each operation against this catalog.
 *
 * Bump the version in `id` when a component's props change incompatibly.
 */
export const generativeUI = createGenerativeUI({
  id: "fast/copilotkit-generative-ui@1",
  // Map needs the optional `maplibre-gl` peer dependency, which this sample does not install.
  extends: [standardUI.omit(["Map"])],
  components: {
    MetricCard: {
      component: MetricCard,
      description:
        "A KPI tile: one headline number with its label and an optional change against the previous period. " +
        "Put several in a Grid or Row above a Chart or Table.",
      props: MetricCardPropsSchema,
    },
  },
})
