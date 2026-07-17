/**
 * Session Service
 *
 * Maintains the per-user chat session "table of contents" (the sidebar list)
 * via the History API. The conversation body itself lives in AgentCore Memory
 * and is fetched separately by historyService. The backend derives the user
 * (actorId) from the validated JWT, so the client only sends the id token.
 */

import { ChatSession } from "@/components/chat/types"

// Load API URL from aws-exports.json (mirrors feedbackService).
let HISTORY_API_URL = ""

async function loadApiUrl(): Promise<string> {
  if (HISTORY_API_URL) {
    return HISTORY_API_URL
  }

  const response = await fetch("/aws-exports.json")
  const config = await response.json()
  if (!config.historyApiUrl) {
    throw new Error("History API URL not configured")
  }
  // aws-exports stores the API stage base URL with a trailing slash.
  HISTORY_API_URL = config.historyApiUrl.replace(/\/$/, "")
  return HISTORY_API_URL
}

interface SessionSummaryResponse {
  id: string
  name: string
  createdAt: string
}

/**
 * List the current user's chat sessions, newest first.
 *
 * @param idToken - Cognito ID token for the API Gateway Cognito authorizer
 */
export async function listSessions(idToken: string): Promise<ChatSession[]> {
  const base = await loadApiUrl()

  const response = await fetch(`${base}/sessions`, {
    method: "GET",
    headers: {
      Authorization: `Bearer ${idToken}`,
    },
  })

  if (!response.ok) {
    throw new Error(`Failed to list sessions: HTTP ${response.status}`)
  }

  const data: { sessions: SessionSummaryResponse[] } = await response.json()
  return data.sessions.map(s => ({
    id: s.id,
    name: s.name,
    createdAt: s.createdAt,
  }))
}

export interface CreateSessionPayload {
  sessionId: string
  firstUserMessage: string
}

export interface CreateSessionResponse {
  sessionId: string
  title: string
  createdAt: string
}

/**
 * Create (or fetch, if it already exists) the index entry for a session and
 * generate its blog-style title from the first user message. Idempotent on the
 * backend, so a double-fire is safe. Intended to be called fire-and-forget when
 * the first message is sent, so the title never depends on the agent's response.
 *
 * @param payload - sessionId plus the first user message used for the title
 * @param idToken - Cognito ID token for the API Gateway Cognito authorizer
 */
export async function createSession(
  payload: CreateSessionPayload,
  idToken: string
): Promise<CreateSessionResponse> {
  const base = await loadApiUrl()

  const response = await fetch(`${base}/sessions`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${idToken}`,
    },
    body: JSON.stringify(payload),
  })

  if (!response.ok) {
    throw new Error(`Failed to create session: HTTP ${response.status}`)
  }

  return response.json()
}
