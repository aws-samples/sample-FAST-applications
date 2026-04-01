// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Upload a file to S3 via presigned URL.
 *
 * @param file - The file to upload.
 * @param accessToken - Cognito access token for authorization.
 */
export async function uploadFile(file: File, accessToken: string): Promise<void> {
  const config = await fetch("/aws-exports.json").then(r => r.json())
  const apiUrl = config.feedbackApiUrl

  // Get presigned URL
  const response = await fetch(`${apiUrl}upload`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${accessToken}`,
    },
    body: JSON.stringify({ filename: file.name }),
  })

  if (!response.ok) {
    throw new Error(`Failed to get upload URL: ${response.statusText}`)
  }

  const { uploadUrl } = await response.json()

  // Upload file to S3
  const uploadResponse = await fetch(uploadUrl, {
    method: "PUT",
    body: file,
    headers: {
      "Content-Type": file.type,
    },
  })

  if (!uploadResponse.ok) {
    throw new Error(`Upload failed: ${uploadResponse.statusText}`)
  }
}

/**
 * Trigger Knowledge Base sync (start ingestion job).
 *
 * @param accessToken - Cognito access token for authorization.
 */
export async function syncKnowledgeBase(accessToken: string): Promise<void> {
  const config = await fetch("/aws-exports.json").then(r => r.json())
  const apiUrl = config.feedbackApiUrl

  const response = await fetch(`${apiUrl}sync`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${accessToken}`,
    },
  })

  if (!response.ok) {
    throw new Error(`Sync failed: ${response.statusText}`)
  }
}
