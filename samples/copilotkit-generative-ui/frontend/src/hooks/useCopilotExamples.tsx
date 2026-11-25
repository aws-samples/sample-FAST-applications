import { z } from "zod"
import { useFrontendTool, useHumanInTheLoop, useDefaultRenderTool } from "@copilotkit/react-core/v2"
import { ToolReasoning } from "@/components/generative-ui/ToolReasoning"
import { MeetingTimePicker } from "@/components/generative-ui/MeetingTimePicker"
import { useTheme } from "@/hooks/useTheme"

export const useCopilotExamples = () => {
  const { theme, setTheme } = useTheme()

  // Frontend tool: toggle light/dark mode
  useFrontendTool(
    {
      name: "toggleTheme",
      description: "Frontend tool for toggling the theme of the app.",
      parameters: z.object({}),
      handler: async () => {
        setTheme(theme === "dark" ? "light" : "dark")
      },
    },
    [theme, setTheme]
  )

  // Generative UI (charts, tables, metric cards, forms, ...) needs no hook here:
  // the component catalog in `@/lib/generative-ui` is registered once on
  // CopilotKitProvider, and the agent renders from it with the `render_ui` tool.

  // Default renderer for all backend tool calls
  useDefaultRenderTool({
    render: ({ name, status, parameters }) => (
      <ToolReasoning name={name} status={status} args={parameters} />
    ),
  })

  // Human-in-the-loop: meeting scheduler
  useHumanInTheLoop({
    name: "scheduleTime",
    description: "Use human-in-the-loop to schedule a meeting with the user.",
    parameters: z.object({
      reasonForScheduling: z.string().describe("Reason for scheduling, very brief - 5 words."),
      meetingDuration: z.number().describe("Duration of the meeting in minutes"),
    }),
    render: ({ respond, status, args }) => (
      <MeetingTimePicker status={status} respond={respond} {...args} />
    ),
  })
}
