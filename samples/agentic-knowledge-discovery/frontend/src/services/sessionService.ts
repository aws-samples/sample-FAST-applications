// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Session history service. Calls the Cognito-authorized sessions API, which
 * persists and serves the exact frontend transcript in DynamoDB. The API base
 * URL comes from aws-exports.json (sessionsApiUrl).
 */

import type { Message } from "@/components/chat/types"

export interface SessionSummary {
  sessionId: string
  title: string
  lastActivity: string | null
}

let baseUrl = ""

async function loadBaseUrl(): Promise<string> {
  if (baseUrl) return baseUrl
  const response = await fetch("/aws-exports.json")
  const config = await response.json()
  baseUrl = config.sessionsApiUrl || ""
  if (!baseUrl) throw new Error("Sessions API URL not configured")
  return baseUrl
}

function authHeaders(idToken: string): Record<string, string> {
  return {
    "Content-Type": "application/json",
    Authorization: `Bearer ${idToken}`,
  }
}

export async function listSessions(idToken: string): Promise<SessionSummary[]> {
  const url = await loadBaseUrl()
  const res = await fetch(`${url}sessions`, { headers: authHeaders(idToken) })
  if (!res.ok) throw new Error(`Failed to list sessions: ${res.status}`)
  const data = await res.json()
  return data.sessions ?? []
}

export async function getSession(sessionId: string, idToken: string): Promise<Message[]> {
  const url = await loadBaseUrl()
  const res = await fetch(`${url}sessions/${encodeURIComponent(sessionId)}`, {
    headers: authHeaders(idToken),
  })
  if (!res.ok) throw new Error(`Failed to load session: ${res.status}`)
  const data = await res.json()
  return data.messages ?? []
}

export async function saveSession(
  sessionId: string,
  title: string,
  messages: Message[],
  idToken: string
): Promise<void> {
  const url = await loadBaseUrl()
  const res = await fetch(`${url}sessions/${encodeURIComponent(sessionId)}`, {
    method: "PUT",
    headers: authHeaders(idToken),
    body: JSON.stringify({ title, messages, updatedAt: new Date().toISOString() }),
  })
  if (!res.ok) throw new Error(`Failed to save session: ${res.status}`)
}

export async function deleteSession(sessionId: string, idToken: string): Promise<void> {
  const url = await loadBaseUrl()
  const res = await fetch(`${url}sessions/${encodeURIComponent(sessionId)}`, {
    method: "DELETE",
    headers: authHeaders(idToken),
  })
  if (!res.ok) throw new Error(`Failed to delete session: ${res.status}`)
}
