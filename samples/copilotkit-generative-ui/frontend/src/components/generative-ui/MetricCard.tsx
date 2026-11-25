import { z } from "zod"

/**
 * Props the agent can set on a MetricCard. The schema is sent to the agent as
 * part of the generative UI catalog, so the descriptions double as its docs.
 */
export const MetricCardPropsSchema = z.object({
  label: z.string().describe("What the number measures, e.g. 'Total revenue'"),
  value: z.string().describe("The formatted value, e.g. '$1.2M' or '42%'"),
  change: z
    .string()
    .optional()
    .describe("Change against the previous period, e.g. '+12%' or '-3.4%'"),
  trend: z
    .enum(["up", "down", "flat"])
    .optional()
    .describe("Direction of the change. Colors the change green, red or gray."),
})

type MetricCardProps = z.infer<typeof MetricCardPropsSchema>

const TREND_CLASS: Record<NonNullable<MetricCardProps["trend"]>, string> = {
  up: "text-emerald-600 dark:text-emerald-400",
  down: "text-red-600 dark:text-red-400",
  flat: "text-gray-500 dark:text-zinc-400",
}

export function MetricCard({ label, value, change, trend = "flat" }: MetricCardProps) {
  return (
    <div className="rounded-xl border dark:border-zinc-700 p-4 bg-[var(--background)]">
      <p className="text-sm text-gray-600 dark:text-zinc-400">{label}</p>
      <p className="mt-1 text-2xl font-bold dark:text-white">{value}</p>
      {change && <p className={`mt-1 text-sm font-medium ${TREND_CLASS[trend]}`}>{change}</p>}
    </div>
  )
}
