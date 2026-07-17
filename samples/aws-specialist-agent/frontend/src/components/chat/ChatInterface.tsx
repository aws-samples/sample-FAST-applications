"use client"

import { useEffect, useRef, useState } from "react"
import { ChatHeader } from "./ChatHeader"
import { ChatInput } from "./ChatInput"
import { ChatMessages } from "./ChatMessages"
import { ChatSidebar } from "./ChatSidebar"
import { ChatSession, Message, MessageSegment, SelectableModel, ToolCall } from "./types"

import { useGlobal } from "@/app/context/GlobalContext"
import { AgentCoreClient } from "@/lib/agentcore-client"
import type { AgentPattern } from "@/lib/agentcore-client"
import { submitFeedback } from "@/services/feedbackService"
import { getHistory } from "@/services/historyService"
import { listSessions, createSession } from "@/services/sessionService"
import { SidebarProvider, SidebarInset, SidebarTrigger } from "@/components/ui/sidebar"
import { Loader2Icon } from "lucide-react"
import { useAuth } from "react-oidc-context"
import { useDefaultTool } from "@/hooks/useToolRenderer"
import { ToolCallDisplay } from "./ToolCallDisplay"

export default function ChatInterface() {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [client, setClient] = useState<AgentCoreClient | null>(null)
  const [sessionId, setSessionId] = useState<string>(() => crypto.randomUUID())
  // Sidebar "table of contents", sourced from DynamoDB via the History API.
  const [sessions, setSessions] = useState<ChatSession[]>([])
  // Set when a selected session's Memory events have aged out (status "expired").
  const [historyNotice, setHistoryNotice] = useState<string | null>(null)
  // True while restoring a past conversation's body. Kept separate from the
  // global isLoading (agent "Thinking...") so loading history does not look
  // like the agent is reasoning.
  const [isRestoringHistory, setIsRestoringHistory] = useState(false)
  // Selectable models published via aws-exports.json. Empty until
  // the config loads; the picker hides itself while empty.
  const [models, setModels] = useState<SelectableModel[]>([])

  const { isLoading, setIsLoading, selectedModelKey, setSelectedModelKey } = useGlobal()
  const auth = useAuth()

  // Register default tool renderer (wildcard "*")
  useDefaultTool(({ name, args, status, result }) => (
    <ToolCallDisplay name={name} args={args} status={status} result={result} />
  ))

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
          pattern: (config.agentPattern || "strands-single-agent") as AgentPattern,
        })

        setClient(agentClient)

        // Selectable models. Optional in aws-exports.json so the
        // app still loads against an older stack without the picker.
        const availableModels: SelectableModel[] = Array.isArray(config.models) ? config.models : []
        setModels(availableModels)
      } catch (err) {
        const errorMessage = err instanceof Error ? err.message : "Unknown error"
        setError(`Configuration error: ${errorMessage}`)
        console.error("Failed to load agent configuration:", err)
      }
    }

    loadConfig()
  }, [])

  // Seed / repair the selected model once the model list is known.
  // Runs when models load or the selection changes. If no valid model is
  // selected (nothing persisted, or the persisted key is no longer offered),
  // pick the backend default (so the picker agrees with the server default),
  // falling back to the first model.
  useEffect(() => {
    if (models.length === 0) return
    const isValid = selectedModelKey !== null && models.some(m => m.key === selectedModelKey)
    if (isValid) return
    const seed = models.find(m => m.default) ?? models[0]
    if (seed) {
      setSelectedModelKey(seed.key)
    }
  }, [models, selectedModelKey, setSelectedModelKey])

  // Speculative pre-warm: start the session's microVM as soon as
  // a sessionId exists, so the VPC cold start (~6s ENI provision, up to ~25s
  // with image pull) runs while the user types instead of in front of their
  // first message. Every way a sessionId comes to be — initial page load,
  // New Chat, selecting a past conversation — flows through this one effect.
  // Fire-and-forget: a failed warmup only means the first real invoke pays
  // the cold start, exactly as before.
  const warmedSessionIds = useRef(new Set<string>())
  useEffect(() => {
    const accessToken = auth.user?.access_token
    if (!client || !accessToken || warmedSessionIds.current.has(sessionId)) return
    warmedSessionIds.current.add(sessionId)
    client.warmup(sessionId, accessToken).catch(err => {
      console.debug("Session pre-warm failed (non-fatal):", err)
    })
  }, [client, sessionId, auth.user?.access_token])

  // Load the session list (sidebar) once the user is authenticated. The list
  // lives in DynamoDB, scoped server-side to the user's JWT sub.
  useEffect(() => {
    const idToken = auth.user?.id_token
    if (!idToken) return

    listSessions(idToken)
      .then(setSessions)
      .catch(err => console.error("Failed to load chat sessions:", err))
  }, [auth.user?.id_token])

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

    // First turn of a new session: register it in the sidebar and generate a
    // blog-style title NOW, from the user's question alone — before the agent
    // responds. The title never depends on the streamed answer, so it is immune
    // to a long or slow or failed response. (The earlier bug: the full answer
    // body was sent as firstAssistantMessage after the turn finished; on a long
    // answer it exceeded the backend's length cap, the POST was rejected with
    // 400, and the title stayed "Untitled".) The row is added optimistically;
    // the POST is fire-and-forget so it never blocks the chat, and the backend
    // is idempotent against a re-fire on the same sessionId.
    const idToken = auth.user?.id_token
    if (idToken && !sessions.some(s => s.id === sessionId)) {
      const capturedSessionId = sessionId
      setSessions(prev => [
        {
          id: capturedSessionId,
          name: "Untitled",
          createdAt: new Date().toISOString(),
        },
        ...prev,
      ])
      createSession({ sessionId: capturedSessionId, firstUserMessage: userMessage }, idToken)
        .then(created => {
          setSessions(prev =>
            prev.map(s =>
              s.id === capturedSessionId
                ? { ...s, name: created.title, createdAt: created.createdAt }
                : s
            )
          )
        })
        .catch(err => console.error("Failed to create session title:", err))
    }

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
      // The model key is passed too; it is untrusted client input
      // validated server-side against an allowlist (it is not identity).
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
              // Some providers (e.g. OpenAI on Bedrock) emit the full tool input
              // in one event without a preceding empty-input start, so there may
              // be no toolCallMap entry yet. Create one on first sight so the
              // tool card still renders.
              let tc = toolCallMap.get(event.toolUseId)
              // OpenAI on Bedrock (Responses API) REUSES tool call ids
              // across event-loop cycles (call_0, call_1, ... restart every
              // cycle), unlike Claude's globally unique toolu_* ids. An entry
              // that already received its result — or whose name differs — is
              // a previous cycle's tool, not this one: rebind the id to a fresh
              // card instead of appending to the finished card (which made the
              // second cycle's tool invisible and corrupted the first's input).
              if (tc && (tc.status === "complete" || (event.name && tc.name !== event.name))) {
                tc = undefined
              }
              if (!tc) {
                tc = {
                  toolUseId: event.toolUseId,
                  name: event.name ?? "",
                  input: "",
                  status: "streaming",
                }
                toolCallMap.set(event.toolUseId, tc)
                segments.push({ type: "tool", toolCall: tc })
              }
              tc.input += event.input
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
                // Fallback: synthesize tool cards for toolUse blocks whose
                // streaming events were missed. Dedup by toolUseId keeps this a
                // no-op when streaming already created the card — but ids are
                // NOT trusted across cycles: OpenAI on Bedrock reuses call ids
                // (call_0, call_1, ...) every event-loop cycle, so an existing
                // entry that is already complete or has a different tool name
                // belongs to a previous cycle and must not block this one.
                if (Array.isArray(event.content)) {
                  for (const block of event.content) {
                    const toolUse = (
                      block as {
                        toolUse?: {
                          toolUseId: string
                          name: string
                          input?: unknown
                        }
                      }
                    ).toolUse
                    if (!toolUse) continue
                    const existing = toolCallMap.get(toolUse.toolUseId)
                    const isCurrent =
                      existing && existing.name === toolUse.name && existing.status !== "complete"
                    if (!isCurrent) {
                      const tc: ToolCall = {
                        toolUseId: toolUse.toolUseId,
                        name: toolUse.name,
                        input:
                          typeof toolUse.input === "string"
                            ? toolUse.input
                            : JSON.stringify(toolUse.input ?? {}),
                        status: "executing",
                      }
                      toolCallMap.set(toolUse.toolUseId, tc)
                      segments.push({ type: "tool", toolCall: tc })
                    }
                  }
                }
                for (const tc of toolCallMap.values()) {
                  if (tc.status === "streaming") tc.status = "executing"
                }
                updateMessage()
              }
              break
            }
            case "result": {
              // The agent's final result: the answer text is complete. Release
              // the input here instead of waiting for the HTTP stream to close —
              // after the last delta the backend still runs post-turn work
              // (memory writes, usage trailers; longest on the OpenAI/Bedrock
              // path), during which nothing is streamed but the connection
              // stays open, leaving the Thinking spinner stuck on a finished
              // answer. The finally block below stays as the error-path and
              // missing-result safety net.
              for (const tc of toolCallMap.values()) {
                if (tc.status === "streaming" || tc.status === "executing") {
                  tc.status = "complete"
                }
              }
              updateMessage()
              setIsLoading(false)
              break
            }
          }
        },
        selectedModelKey ?? undefined
      )
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
  // A new UUID is required so the backend treats this as a distinct conversation
  // context. The session is NOT added to the sidebar until its first turn
  // completes (see sendMessage), so empty sessions never clutter the list.
  const startNewChat = () => {
    setMessages([])
    setInput("")
    setError(null)
    setHistoryNotice(null)
    setSessionId(crypto.randomUUID())
  }

  // Select a past session from the sidebar: switch the active sessionId and
  // restore its message body from AgentCore Memory (via the History API).
  // The agent's own context is restored automatically server-side on the next
  // turn (the session manager rehydrates from the same sessionId), so we only
  // need to repopulate the UI here. An aged-out session ("expired") shows a
  // notice but keeps the input enabled so the user can continue.
  const handleSessionSelect = async (session: ChatSession) => {
    if (session.id === sessionId) return

    setSessionId(session.id)
    setInput("")
    setError(null)
    setHistoryNotice(null)
    setMessages([])

    const idToken = auth.user?.id_token
    if (!idToken) return

    setIsRestoringHistory(true)
    try {
      const result = await getHistory(session.id, idToken)
      if (result.status === "expired") {
        setHistoryNotice(
          "This conversation is older than the 30-day retention period, so its messages can no longer be restored. You can keep chatting to resume with a fresh context."
        )
      } else {
        setMessages(result.messages)
      }
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : "Unknown error"
      console.error("Error restoring history:", err)
      setError(`Failed to restore conversation: ${errorMessage}`)
    } finally {
      setIsRestoringHistory(false)
    }
  }

  // Check if this is the initial state (no messages)
  const isInitialState = messages.length === 0

  return (
    <SidebarProvider>
      <ChatSidebar
        sessions={sessions}
        currentSessionId={sessionId}
        onSessionSelect={handleSessionSelect}
        onNewChat={startNewChat}
      />
      <SidebarInset className="flex flex-col h-screen w-full">
        {/* Fixed header */}
        <div className="flex-none">
          <div className="flex items-center gap-2 px-2 pt-2">
            <SidebarTrigger />
          </div>
          <ChatHeader />
          {error && (
            <div className="bg-red-50 border-l-4 border-red-500 p-4 mx-4 mt-2">
              <p className="text-sm text-red-700">{error}</p>
            </div>
          )}
          {historyNotice && (
            <div className="bg-amber-50 border-l-4 border-amber-500 p-4 mx-4 mt-2">
              <p className="text-sm text-amber-700">⏳ {historyNotice}</p>
            </div>
          )}
          {isRestoringHistory && (
            <div className="flex items-center justify-center gap-2 p-2 text-sm text-gray-500">
              <Loader2Icon className="h-4 w-4 animate-spin" />
              Loading conversation history...
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
            <div className="text-center mb-6">
              <h2 className="text-2xl font-bold text-gray-800">
                Welcome to AgentCore AWS Specialist Agent
              </h2>
              <p className="text-gray-600 mt-2">Ask me anything to get started</p>
            </div>

            {/* Centered input */}
            <div className="px-4 mb-16 max-w-4xl mx-auto w-full">
              <ChatInput
                input={input}
                setInput={setInput}
                handleSubmit={handleSubmit}
                isLoading={isLoading}
                models={models}
                selectedModelKey={selectedModelKey}
                onModelChange={setSelectedModelKey}
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
                  models={models}
                  selectedModelKey={selectedModelKey}
                  onModelChange={setSelectedModelKey}
                />
              </div>
            </div>
          </>
        )}
      </SidebarInset>
    </SidebarProvider>
  )
}
