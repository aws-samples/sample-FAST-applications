"use client"

import { useEffect, useRef, useState } from "react"
import { ChatHeader } from "./ChatHeader"
import { ChatInput } from "./ChatInput"
import { ChatMessages } from "./ChatMessages"
import { Message, MessageSegment, ToolCall } from "./types"

import { useGlobal } from "@/app/context/GlobalContext"
import { AgentCoreClient } from "@/lib/agentcore-client"
import type { AgentPattern } from "@/lib/agentcore-client"
import { submitFeedback } from "@/services/feedbackService"
import {
  listSessions,
  getSession,
  saveSession,
  deleteSession,
  type SessionSummary,
} from "@/services/sessionService"
import { useAuth } from "react-oidc-context"
import { useDefaultTool, useToolRenderer } from "@/hooks/useToolRenderer"

// Safely pull a field out of a tool result that may be a JSON string.
function parseToolField(result: unknown, field: string): unknown {
  if (typeof result !== "string") return null
  try {
    const parsed = JSON.parse(result)
    return parsed?.[field] ?? null
  } catch {
    return null
  }
}

// Compact page label, e.g. [5] -> "p.5", [5,6,7,9] -> "p.5–7, 9".
function formatPages(pages: number[]): string {
  const sorted = [...new Set(pages)].sort((a, b) => a - b)
  if (!sorted.length) return ""
  const ranges: string[] = []
  let start = sorted[0]
  let end = sorted[0]
  for (let i = 1; i < sorted.length; i++) {
    if (sorted[i] === end + 1) {
      end = sorted[i]
    } else {
      ranges.push(start === end ? `${start}` : `${start}\u2013${end}`)
      start = end = sorted[i]
    }
  }
  ranges.push(start === end ? `${start}` : `${start}\u2013${end}`)
  return `p.${ranges.join(", ")}`
}

// Models the UI offers. Must match the backend allowlist (patterns/utils/models.py).
const MODEL_OPTIONS: { id: string; label: string }[] = [
  { id: "us.anthropic.claude-sonnet-5", label: "Claude Sonnet 5" },
  { id: "openai.gpt-5.5", label: "GPT-5.5" },
]
import { ToolCallDisplay } from "./ToolCallDisplay"
import { ChatSidebar } from "./ChatSidebar"
import { SidebarProvider, SidebarInset } from "@/components/ui/sidebar"

export default function ChatInterface() {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [client, setClient] = useState<AgentCoreClient | null>(null)
  const [sessionId, setSessionId] = useState<string>(() => crypto.randomUUID())
  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const [modelId, setModelId] = useState<string>(MODEL_OPTIONS[0].id)

  const { isLoading, setIsLoading } = useGlobal()
  const auth = useAuth()

  // Ref for message container to enable auto-scrolling
  const messagesEndRef = useRef<HTMLDivElement>(null)
  // Lets tool renderers (e.g. suggestion chips) call the latest sendMessage.
  const sendMessageRef = useRef<(q: string) => void>(() => {})

  // Register default tool renderer (wildcard "*")
  useDefaultTool(({ name, args, status, result }) => (
    <ToolCallDisplay name={name} args={args} status={status} result={result} />
  ))

  // Follow-up suggestions -> clickable chips.
  useToolRenderer("suggest_questions", ({ result }) => {
    const questions = parseToolField(result, "questions") as string[] | null
    if (!questions?.length) return null
    return (
      <div className="mt-2 flex flex-wrap gap-2">
        {questions.map(q => (
          <button
            key={q}
            type="button"
            onClick={() => sendMessageRef.current(q)}
            className="rounded-full border border-gray-200 bg-white px-3 py-1 text-xs text-gray-700 hover:border-gray-300 hover:bg-gray-50"
          >
            {q}
          </button>
        ))}
      </div>
    )
  })

  // Citations -> clickable source links. Each source links to the first cited
  // page of the document (#page=N); the label shows the full page range.
  useToolRenderer("cite_sources", ({ result }) => {
    const sources = parseToolField(result, "sources") as
      { doc_id: string; pages?: number[]; url?: string }[] | null
    if (!sources?.length) return null
    return (
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <span className="text-xs text-gray-400">Sources:</span>
        {sources.map(s => {
          const pages = s.pages ?? []
          const label = pages.length ? `${s.doc_id} (${formatPages(pages)})` : s.doc_id
          const href = s.url && pages.length ? `${s.url}#page=${pages[0]}` : s.url
          return href ? (
            <a
              key={s.doc_id}
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="rounded border border-gray-200 bg-gray-50 px-2 py-0.5 text-xs text-blue-700 underline hover:bg-gray-100"
            >
              {label}
            </a>
          ) : (
            <span
              key={s.doc_id}
              className="rounded border border-gray-200 bg-gray-50 px-2 py-0.5 text-xs text-gray-600"
            >
              {label}
            </span>
          )
        })}
      </div>
    )
  })

  // Load agent configuration and create client on mount
  useEffect(() => {
    async function loadConfig() {
      try {
        const response = await fetch("/aws-exports.json")
        if (!response.ok) {
          throw new Error("Failed to load configuration")
        }
        const config = await response.json()

        if (!config.agentRuntimeArn) {
          throw new Error("Agent Runtime ARN not found in configuration")
        }

        const agentClient = new AgentCoreClient({
          runtimeArn: config.agentRuntimeArn,
          region: config.awsRegion || "us-east-1",
          pattern: (config.agentPattern || "strands-agent") as AgentPattern,
        })

        setClient(agentClient)
      } catch (err) {
        const errorMessage = err instanceof Error ? err.message : "Unknown error"
        setError(`Configuration error: ${errorMessage}`)
        console.error("Failed to load agent configuration:", err)
      }
    }

    loadConfig()
  }, [])

  const sendMessage = async (userMessage: string) => {
    if (!userMessage.trim() || !client) return

    // Clear any previous errors
    setError(null)

    // Add user message to chat
    const newUserMessage: Message = {
      role: "user",
      content: userMessage,
      timestamp: new Date().toISOString(),
    }

    setMessages(prev => [...prev, newUserMessage])
    setInput("")
    setIsLoading(true)

    // Create placeholder for assistant response
    const assistantResponse: Message = {
      role: "assistant",
      content: "",
      timestamp: new Date().toISOString(),
    }

    setMessages(prev => [...prev, assistantResponse])

    try {
      // Get auth token from react-oidc-context
      const accessToken = auth.user?.access_token

      if (!accessToken) {
        throw new Error("Authentication required. Please log in again.")
      }

      const segments: MessageSegment[] = []
      const toolCallMap = new Map<string, ToolCall>()

      const updateMessage = () => {
        // Build content from text segments for backward compat
        const content = segments
          .filter((s): s is Extract<MessageSegment, { type: "text" }> => s.type === "text")
          .map(s => s.content)
          .join("")

        setMessages(prev => {
          const updated = [...prev]
          updated[updated.length - 1] = {
            ...updated[updated.length - 1],
            content,
            segments: [...segments],
          }
          return updated
        })
      }

      // User identity is extracted server-side from the validated JWT token,
      // not passed as a parameter — prevents impersonation via prompt injection.
      await client.invoke(
        userMessage,
        sessionId,
        accessToken,
        event => {
          switch (event.type) {
            case "text": {
              // If text arrives after a tool segment, mark all pending tools as complete
              const prev = segments[segments.length - 1]
              if (prev && prev.type === "tool") {
                for (const tc of toolCallMap.values()) {
                  if (tc.status === "streaming" || tc.status === "executing") {
                    tc.status = "complete"
                  }
                }
              }
              // Append to last text segment, or create new one
              const last = segments[segments.length - 1]
              if (last && last.type === "text") {
                last.content += event.content
              } else {
                segments.push({ type: "text", content: event.content })
              }
              updateMessage()
              break
            }
            case "tool_use_start": {
              const tc: ToolCall = {
                toolUseId: event.toolUseId,
                name: event.name,
                input: "",
                status: "streaming",
              }
              toolCallMap.set(event.toolUseId, tc)
              segments.push({ type: "tool", toolCall: tc })
              updateMessage()
              break
            }
            case "tool_use_delta": {
              const tc = toolCallMap.get(event.toolUseId)
              if (tc) {
                tc.input += event.input
              }
              updateMessage()
              break
            }
            case "tool_result": {
              const tc = toolCallMap.get(event.toolUseId)
              if (tc) {
                tc.result = event.result
                tc.status = "complete"
              }
              updateMessage()
              break
            }
            case "message": {
              if (event.role === "assistant") {
                for (const tc of toolCallMap.values()) {
                  if (tc.status === "streaming") tc.status = "executing"
                }
                updateMessage()
              }
              break
            }
          }
        },
        { modelId }
      )

      // Persist the finished turn so the conversation resumes with the exact
      // view. Uses the ID token for the Cognito-authorized sessions API.
      const idToken = auth.user?.id_token
      if (idToken) {
        const assistantContent = segments
          .filter((s): s is Extract<MessageSegment, { type: "text" }> => s.type === "text")
          .map(s => s.content)
          .join("")
        const finalAssistant: Message = {
          role: "assistant",
          content: assistantContent,
          timestamp: assistantResponse.timestamp,
          segments: [...segments],
        }
        const transcript = [...messages, newUserMessage, finalAssistant]
        const title = (messages[0]?.content || userMessage).slice(0, 80)
        try {
          await saveSession(sessionId, title, transcript, idToken)
          // Re-list from DynamoDB (strongly consistent) so the sidebar reflects
          // the real saved state, not a placeholder.
          await refreshSessions()
        } catch (err) {
          console.error("Failed to save session:", err)
        }
      }
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : "Unknown error"
      setError(`Failed to get response: ${errorMessage}`)
      console.error("Error invoking AgentCore:", err)

      // Update the assistant message with error
      setMessages(prev => {
        const updated = [...prev]
        updated[updated.length - 1] = {
          ...updated[updated.length - 1],
          content:
            "I apologize, but I encountered an error processing your request. Please try again.",
        }
        return updated
      })
    } finally {
      setIsLoading(false)
    }
  }

  // Keep the ref pointing at the current sendMessage so tool renderers (e.g.
  // suggestion chips) can trigger a new turn.
  sendMessageRef.current = sendMessage

  // Handle form submission
  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()

    sendMessage(input)
  }

  // Handle feedback submission
  const handleFeedbackSubmit = async (
    messageContent: string,
    feedbackType: "positive" | "negative",
    comment: string
  ) => {
    try {
      // Use ID token for API Gateway Cognito authorizer (not access token)
      const idToken = auth.user?.id_token

      if (!idToken) {
        throw new Error("Authentication required. Please log in again.")
      }

      await submitFeedback(
        {
          sessionId,
          message: messageContent,
          feedbackType,
          comment: comment || undefined,
        },
        idToken
      )

      console.log("Feedback submitted successfully")
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : "Unknown error"
      console.error("Error submitting feedback:", err)
      setError(`Failed to submit feedback: ${errorMessage}`)
    }
  }

  // Start a new chat by clearing messages and generating a fresh session ID.
  // A new UUID is required so the backend treats this as a distinct conversation context.
  const startNewChat = () => {
    setMessages([])
    setInput("")
    setError(null)
    setSessionId(crypto.randomUUID())
  }

  // Load the user's past sessions for the sidebar.
  const refreshSessions = async () => {
    const idToken = auth.user?.id_token
    if (!idToken) return
    try {
      setSessions(await listSessions(idToken))
    } catch (err) {
      console.error("Failed to load sessions:", err)
    }
  }

  // Load sessions once authenticated.
  useEffect(() => {
    if (auth.user?.id_token) {
      refreshSessions()
    }
  }, [auth.user?.id_token])

  // Resume a past session: reuse its id and load its history. AgentCore Memory
  // reloads prior context on the next turn, so no message replay is needed.
  const handleSessionSelect = async (selectedId: string) => {
    const idToken = auth.user?.id_token
    if (!idToken || selectedId === sessionId) return
    setError(null)
    try {
      const history = await getSession(selectedId, idToken)
      setMessages(history)
      setSessionId(selectedId)
    } catch (err) {
      console.error("Failed to resume session:", err)
      setError("Could not load that conversation.")
    }
  }

  const handleSessionDelete = async (deleteId: string) => {
    const idToken = auth.user?.id_token
    if (!idToken) return
    try {
      await deleteSession(deleteId, idToken)
      if (deleteId === sessionId) startNewChat()
      // Re-list from DynamoDB (strongly consistent) so the removal is real.
      await refreshSessions()
    } catch (err) {
      console.error("Failed to delete session:", err)
    }
  }

  // Check if this is the initial state (no messages)
  const isInitialState = messages.length === 0

  // Starter prompts shown on the empty state. They exercise both tools:
  // metadata_search (SQL over document metadata) and doc_search (knowledge base).
  const SUGGESTIONS = [
    "What domains and document types are available?",
    "Which document has the most pages?",
    "Summarize the NIST AI Risk Management Framework",
    "What does the Annual Energy Outlook project for energy use?",
  ]

  return (
    <SidebarProvider>
      <ChatSidebar
        sessions={sessions}
        currentSessionId={sessionId}
        onSessionSelect={handleSessionSelect}
        onSessionDelete={handleSessionDelete}
        onNewChat={startNewChat}
      />
      <SidebarInset>
        <div className="flex flex-col h-screen w-full">
          {/* Fixed header */}
          <div className="flex-none">
            <ChatHeader models={MODEL_OPTIONS} modelId={modelId} onModelChange={setModelId} />
            {error && (
              <div className="bg-red-50 border-l-4 border-red-500 p-4 mx-4 mt-2">
                <p className="text-sm text-red-700">{error}</p>
              </div>
            )}
          </div>

          {/* Conditional layout based on whether there are messages */}
          {isInitialState ? (
            // Initial state - input in the middle
            <>
              {/* Empty space above */}
              <div className="grow" />

              {/* Centered welcome message */}
              <div className="mx-auto mb-6 w-full max-w-2xl px-4 text-center">
                <h2 className="text-2xl font-bold text-gray-800">
                  Search documents and their metadata
                </h2>
                <p className="mx-auto mt-2 max-w-lg text-gray-600">
                  Ask in plain language. I combine structured metadata (SQL) with document search to
                  answer.
                </p>
                <div className="mx-auto mt-5 grid max-w-xl grid-cols-1 gap-3 text-left sm:grid-cols-2">
                  {SUGGESTIONS.map(suggestion => (
                    <button
                      key={suggestion}
                      type="button"
                      onClick={() => sendMessage(suggestion)}
                      disabled={isLoading}
                      className="rounded-lg border border-gray-200 bg-white px-4 py-3 text-sm text-gray-700 transition-colors hover:border-gray-300 hover:bg-gray-50 disabled:opacity-50"
                    >
                      {suggestion}
                    </button>
                  ))}
                </div>
              </div>

              {/* Centered input */}
              <div className="px-4 mb-16 max-w-4xl mx-auto w-full">
                <ChatInput
                  input={input}
                  setInput={setInput}
                  handleSubmit={handleSubmit}
                  isLoading={isLoading}
                />
              </div>

              {/* Empty space below */}
              <div className="grow" />
            </>
          ) : (
            // Chat in progress - normal layout
            <>
              {/* Scrollable message area */}
              <div className="grow overflow-hidden">
                <div className="max-w-4xl mx-auto w-full h-full">
                  <ChatMessages
                    messages={messages}
                    messagesEndRef={messagesEndRef}
                    sessionId={sessionId}
                    onFeedbackSubmit={handleFeedbackSubmit}
                  />
                </div>
              </div>

              {/* Fixed input area at bottom */}
              <div className="flex-none">
                <div className="max-w-4xl mx-auto w-full">
                  <ChatInput
                    input={input}
                    setInput={setInput}
                    handleSubmit={handleSubmit}
                    isLoading={isLoading}
                  />
                </div>
              </div>
            </>
          )}
        </div>
      </SidebarInset>
    </SidebarProvider>
  )
}
