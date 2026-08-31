"use client"

import { useState } from "react"
import { Sparkles, Loader2, CheckCircle2, ChevronRight, ChevronDown } from "lucide-react"
import { getToolRenderer } from "@/hooks/useToolRenderer"
import type { ToolCall } from "./types"

interface ToolCallGroupProps {
  toolCalls: ToolCall[]
}

/**
 * Collapses a run of internal tool calls (doc_search, metadata_search, etc.)
 * into a single "Analyzing" block, collapsed by default. Expanding shows each
 * individual tool call with its normal (default) renderer, so no detail is lost
 * — the default view just stays clean when the agent takes several back-to-back
 * steps. Tools with a dedicated renderer (citations, suggestions) are rendered
 * separately and never grouped.
 */
export function ToolCallGroup({ toolCalls }: ToolCallGroupProps) {
  const [expanded, setExpanded] = useState(false)

  const active = toolCalls.some(t => t.status === "streaming" || t.status === "executing")
  const count = toolCalls.length

  return (
    <div className="my-1 text-sm">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-1.5 px-2 py-1 rounded hover:bg-gray-200/50 transition-colors w-full text-left"
      >
        {expanded ? (
          <ChevronDown size={12} className="text-gray-400" />
        ) : (
          <ChevronRight size={12} className="text-gray-400" />
        )}
        <Sparkles size={12} className="text-gray-400" />
        <span className="text-gray-600">{active ? "Analyzing" : "Analysis"}</span>
        <span className="text-xs text-gray-400">
          {count} step{count === 1 ? "" : "s"}
        </span>
        {active ? (
          <Loader2 size={12} className="animate-spin text-amber-500 ml-auto" />
        ) : (
          <CheckCircle2 size={12} className="text-green-500 ml-auto" />
        )}
      </button>

      {expanded && (
        <div className="ml-4 mt-1 border-l-2 border-gray-200 pl-2 space-y-1">
          {toolCalls.map(tc => {
            const render = getToolRenderer(tc.name)
            if (!render) return null
            return (
              <div key={tc.toolUseId}>
                {render({
                  name: tc.name,
                  args: tc.input,
                  status: tc.status,
                  result: tc.result,
                })}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
