import { RefObject, useEffect, useRef } from "react"
import { Message } from "./types"
import { ChatMessage } from "./ChatMessage"

interface ChatMessagesProps {
  messages: Message[]
  messagesEndRef: RefObject<HTMLDivElement | null>
  sessionId: string
  onFeedbackSubmit: (
    messageContent: string,
    feedbackType: "positive" | "negative",
    comment: string
  ) => Promise<void>
}

export function ChatMessages({
  messages,
  messagesEndRef,
  sessionId,
  onFeedbackSubmit,
}: ChatMessagesProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  // Only keep the view pinned to the bottom if the user is already there.
  // If they scrolled up to read, we leave them alone — no yanking.
  const stickToBottom = useRef(true)

  const handleScroll = () => {
    const el = containerRef.current
    if (!el) return
    stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
  }

  useEffect(() => {
    if (!stickToBottom.current) return
    const el = containerRef.current
    // Instant positioning (no smooth animation) so streaming updates don't cause
    // the view to jump around.
    if (el) el.scrollTop = el.scrollHeight
  }, [messages])

  return (
    <div
      ref={containerRef}
      onScroll={handleScroll}
      className={`h-full p-4 space-y-4 w-full ${
        messages.length > 0 ? "overflow-y-auto" : "overflow-hidden"
      }`}
    >
      {messages.length === 0 ? (
        <div className="flex items-center justify-center h-full text-gray-400">
          Start a new conversation
        </div>
      ) : (
        messages.map((message, index) => (
          <ChatMessage
            key={index}
            message={message}
            sessionId={sessionId}
            onFeedbackSubmit={async (feedbackType, comment) => {
              await onFeedbackSubmit(message.content, feedbackType, comment)
            }}
          />
        ))
      )}
      <div ref={messagesEndRef} />
    </div>
  )
}
