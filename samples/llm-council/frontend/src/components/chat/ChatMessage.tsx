"use client"

import { useState } from "react"
import { ThumbsUp, ThumbsDown } from "lucide-react"
import { Message } from "./types"
import { FeedbackDialog } from "./FeedbackDialog"
import { CouncilView } from "./CouncilView"

interface ChatMessageProps {
  message: Message
  onFeedbackSubmit: (feedbackType: "positive" | "negative", comment: string) => Promise<void>
}

export function ChatMessage({ message, onFeedbackSubmit }: ChatMessageProps) {
  const [isDialogOpen, setIsDialogOpen] = useState(false)
  const [selectedFeedbackType, setSelectedFeedbackType] = useState<"positive" | "negative">(
    "positive"
  )
  const [feedbackSubmitted, setFeedbackSubmitted] = useState(false)

  const formatTime = (timestamp: string) => {
    return new Date(timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
  }

  const handleFeedbackClick = (type: "positive" | "negative") => {
    setSelectedFeedbackType(type)
    setIsDialogOpen(true)
  }

  const handleFeedbackSubmit = async (comment: string) => {
    await onFeedbackSubmit(selectedFeedbackType, comment)
    setFeedbackSubmitted(true)
  }

  // Check if this is a council response
  const hasCouncilData = message.councilData && (
    message.councilData.stage1 || 
    message.councilData.stage2 || 
    message.councilData.stage3 ||
    message.councilData.statusMessage
  )

  return (
    <div className={`flex flex-col ${message.role === "user" ? "items-end" : "items-start"}`}>
      {/* Regular message content */}
      {message.content && !hasCouncilData && (
        <div
          className={`max-w-[80%] p-3 rounded-lg whitespace-pre-wrap break-words ${
            message.role === "user"
              ? "bg-gray-800 text-white rounded-br-none"
              : "bg-gray-100 text-gray-800 rounded-bl-none"
          }`}
        >
          {message.content}
        </div>
      )}

      {/* Council deliberation view */}
      {hasCouncilData && message.councilData && (
        <div className="max-w-[90%] w-full">
          <CouncilView councilData={message.councilData} />
        </div>
      )}

      {/* Timestamp and Feedback buttons for assistant messages */}
      <div className="flex items-center gap-2 mt-1 px-1">
        <div className="text-xs text-gray-500">{formatTime(message.timestamp)}</div>

        {/* Show feedback buttons only for assistant messages with content */}
        {message.role === "assistant" && (message.content || hasCouncilData) && (
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
