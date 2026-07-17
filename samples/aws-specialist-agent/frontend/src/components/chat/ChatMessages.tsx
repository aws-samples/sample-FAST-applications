import { useEffect, useRef } from "react"
import { Message } from "./types"
import { ChatMessage } from "./ChatMessage"

interface ChatMessagesProps {
  messages: Message[]
  sessionId: string
  onFeedbackSubmit: (
    messageContent: string,
    feedbackType: "positive" | "negative",
    comment: string
  ) => Promise<void>
}

// How close to the bottom (px) still counts as "following" the conversation.
const STICK_THRESHOLD = 80

export function ChatMessages({ messages, sessionId, onFeedbackSubmit }: ChatMessagesProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  // Auto-scroll only while the user is at the bottom. Set false once they
  // scroll up to read history (so streaming tokens don't yank them back down),
  // and true again when they return to the bottom. A ref avoids re-renders.
  const stickToBottom = useRef(true)

  const handleScroll = () => {
    const el = containerRef.current
    if (!el) return
    stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < STICK_THRESHOLD
  }

  useEffect(() => {
    if (stickToBottom.current) {
      containerRef.current?.scrollTo({
        top: containerRef.current.scrollHeight,
      })
    }
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
    </div>
  )
}
