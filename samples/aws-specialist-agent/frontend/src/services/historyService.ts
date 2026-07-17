/**
 * History Service
 *
 * Restores a past conversation's message body from AgentCore Memory via the
 * History API (Lambda). The backend derives the user (actorId) from the
 * validated JWT, so the client only sends the id token and a sessionId.
 *
 * A session whose Memory events have aged out (expiry 30d / empty-session 1d
 * deletion) returns status "expired" so the UI can show a calm notice rather
 * than an error.
 */

import { Message } from "@/components/chat/types"

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
  HISTORY_API_URL = config.historyApiUrl.replace(/\/$/, "")
  return HISTORY_API_URL
}

export type HistoryStatus = "ok" | "expired"

export interface HistoryResult {
  status: HistoryStatus
  messages: Message[]
}

interface HistoryMessageResponse {
  role: "user" | "assistant"
  content: string
  timestamp: string
}

/**
 * Fetch the restored transcript for a session.
 *
 * @param sessionId - The conversation/session identifier to restore
 * @param idToken - Cognito ID token for the API Gateway Cognito authorizer
 * @returns status "ok" with messages, or "expired" with an empty list
 */
export async function getHistory(sessionId: string, idToken: string): Promise<HistoryResult> {
  const base = await loadApiUrl()

  const response = await fetch(`${base}/history?sessionId=${encodeURIComponent(sessionId)}`, {
    method: "GET",
    headers: {
      Authorization: `Bearer ${idToken}`,
    },
  })

  if (!response.ok) {
    throw new Error(`Failed to load history: HTTP ${response.status}`)
  }

  const data: { status: HistoryStatus; messages: HistoryMessageResponse[] } = await response.json()

  return {
    status: data.status,
    messages: data.messages.map(m => ({
      role: m.role,
      content: m.content,
      timestamp: m.timestamp,
    })),
  }
}
