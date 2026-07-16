"use client"

import { useState } from "react"
import type { ReactNode } from "react"
import { ThumbsUp, ThumbsDown } from "lucide-react"
import { Message, ToolCall } from "./types"
import { FeedbackDialog } from "./FeedbackDialog"
import { getToolRenderer, hasToolRenderer } from "@/hooks/useToolRenderer"
import { MarkdownRenderer } from "./MarkdownRenderer"
import { ToolCallGroup } from "./ToolCallGroup"

interface ChatMessageProps {
  message: Message
  sessionId: string
  onFeedbackSubmit: (feedbackType: "positive" | "negative", comment: string) => Promise<void>
}

export function ChatMessage({
  message,
  sessionId: _sessionId,
  onFeedbackSubmit,
}: ChatMessageProps) {
  const [isDialogOpen, setIsDialogOpen] = useState(false)
  const [selectedFeedbackType, setSelectedFeedbackType] = useState<"positive" | "negative">(
    "positive"
  )
  const [feedbackSubmitted, setFeedbackSubmitted] = useState(false)

  const formatTime = (timestamp: string) => {
    return new Date(timestamp).toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
    })
  }

  const handleFeedbackClick = (type: "positive" | "negative") => {
    setSelectedFeedbackType(type)
    setIsDialogOpen(true)
  }

  const handleFeedbackSubmit = async (comment: string) => {
    await onFeedbackSubmit(selectedFeedbackType, comment)
    setFeedbackSubmitted(true)
  }

  const renderAssistantContent = () => {
    // Render segments in order (interleaved text + tools). Consecutive internal
    // tool calls (those without a dedicated renderer — doc_search,
    // metadata_search) are collapsed into a single "Analyzing" group; text and
    // tools with a dedicated renderer (citations, suggestions) break the run and
    // render inline.
    if (message.segments && message.segments.length > 0) {
      const out: ReactNode[] = []
      let group: ToolCall[] = []

      const flushGroup = (key: string) => {
        if (group.length === 0) return
        const items = group
        group = []
        out.push(<ToolCallGroup key={`group-${key}`} toolCalls={items} />)
      }

      message.segments.forEach((seg, i) => {
        if (seg.type === "text") {
          flushGroup(String(i))
          out.push(<MarkdownRenderer key={i} content={seg.content} />)
          return
        }

        const tc = seg.toolCall
        // Internal tool (uses the default renderer) — group it.
        if (!hasToolRenderer(tc.name)) {
          group.push(tc)
          return
        }

        // Dedicated renderer (citations, suggestions): flush the group, render inline.
        flushGroup(String(i))
        const render = getToolRenderer(tc.name)
        if (!render) return
        out.push(
          <div key={tc.toolUseId} className="my-1">
            {render({
              name: tc.name,
              args: tc.input,
              status: tc.status,
              result: tc.result,
            })}
          </div>
        )
      })

      flushGroup("end")
      return out
    }
    // Fallback: just render content as markdown
    return <MarkdownRenderer content={message.content} />
  }

  return (
    <div className={`flex flex-col ${message.role === "user" ? "items-end" : "items-start"}`}>
      <div
        className={`max-w-[80%] break-words ${
          message.role === "user"
            ? "p-3 rounded-lg bg-gray-800 text-white rounded-br-none whitespace-pre-wrap"
            : "text-gray-800"
        }`}
      >
        {message.role === "assistant" ? renderAssistantContent() : message.content}
      </div>

      {/* Timestamp and Feedback buttons for assistant messages */}
      <div className="flex items-center gap-2 mt-1 px-1">
        <div className="text-xs text-gray-500">{formatTime(message.timestamp)}</div>

        {/* Show feedback buttons only for assistant messages with content */}
        {message.role === "assistant" && message.content && (
          <div className="flex items-center gap-1 ml-2">
            <button
              onClick={() => handleFeedbackClick("positive")}
              disabled={feedbackSubmitted}
              className="p-1 text-gray-400 hover:text-green-600 hover:bg-green-50 rounded transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              aria-label="Positive feedback"
              title="Good response"
            >
              <ThumbsUp size={14} />
            </button>
            <button
              onClick={() => handleFeedbackClick("negative")}
              disabled={feedbackSubmitted}
              className="p-1 text-gray-400 hover:text-red-600 hover:bg-red-50 rounded transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              aria-label="Negative feedback"
              title="Bad response"
            >
              <ThumbsDown size={14} />
            </button>
            {feedbackSubmitted && (
              <span className="text-xs text-gray-500 ml-1">Thanks for your feedback!</span>
            )}
          </div>
        )}
      </div>

      {/* Feedback Dialog */}
      <FeedbackDialog
        isOpen={isDialogOpen}
        onClose={() => setIsDialogOpen(false)}
        onSubmit={handleFeedbackSubmit}
        feedbackType={selectedFeedbackType}
      />
    </div>
  )
}
