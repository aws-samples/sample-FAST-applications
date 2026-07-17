// Define message types
export type MessageRole = "user" | "assistant"

export type ToolCallStatus = "streaming" | "executing" | "complete"

export interface ToolCall {
  toolUseId: string
  name: string
  input: string
  result?: string
  status: ToolCallStatus
}

export type MessageSegment =
  | { type: "text"; content: string }
  | { type: "tool"; toolCall: ToolCall }

export interface Message {
  role: MessageRole
  content: string
  timestamp: string
  segments?: MessageSegment[]
}

// Chat session summary for the sidebar "table of contents".
// The conversation body is NOT stored here — it lives in AgentCore Memory and
// is fetched on demand via historyService. Only the lightweight index
// (id / title / createdAt) is kept, sourced from DynamoDB via sessionService.
export interface ChatSession {
  id: string
  name: string
  createdAt: string
}

// A user-selectable chat model, as published to the frontend via
// aws-exports.json. The physical Bedrock id is intentionally NOT here — the
// frontend only ever sends the stable logical `key`, and the backend resolves
// it to the physical model. Every model in the list is selectable and works.
export interface SelectableModel {
  key: string
  label: string
  // True only on the backend's default model, so the picker can pre-select the
  // same model the backend uses when no key is sent (keeps displayed and actual
  // defaults in agreement).
  default?: boolean
}
