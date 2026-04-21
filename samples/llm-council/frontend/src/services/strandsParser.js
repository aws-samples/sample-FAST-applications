// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Strands Agent Streaming Parser
 * 
 * This parser processes Server-Sent Events (SSE) from Strands agents that use
 * the Amazon Bedrock Converse API format. Strands agents wrap their responses
 * in nested event structures.
 * 
 * EVENTS HANDLED:
 * 1. messageStart - Signals the beginning of a new assistant message
 *    Format: {"event": {"messageStart": {"role": "assistant"}}}
 *    Action: Adds newline separator between messages
 * 
 * 2. contentBlockDelta - Contains incremental text content
 *    Format: {"event": {"contentBlockDelta": {"delta": {"text": "Hello"}}}}
 *    Action: Appends text to the completion stream
 */

/**
 * Parse a streaming chunk from a Strands agent.
 * 
 * @param {string} line - The SSE line to parse
 * @param {string} currentCompletion - The accumulated completion text
 * @param {Function} updateCallback - Callback to update the UI with new text
 * @returns {string} Updated completion text
 */
export const parseStreamingChunk = (line, currentCompletion, updateCallback) => {
  // Skip empty lines
  if (!line || !line.trim()) {
    return currentCompletion;
  }

  // Strip "data: " prefix from SSE format
  if (!line.startsWith('data: ')) {
    return currentCompletion;
  }

  const data = line.substring(6).trim();

  // Skip empty data
  if (!data) {
    return currentCompletion;
  }

  // Parse JSON events
  try {
    const json = JSON.parse(data);

    // Handle message start - add newline for new assistant message
    // Example: {"event": {"messageStart": {"role": "assistant"}}}
    if (json.event?.messageStart?.role === 'assistant') {
      if (currentCompletion) {  // Only add newline if there's previous content
        const newCompletion = currentCompletion + '\n\n';
        updateCallback(newCompletion);
        return newCompletion;
      }
      return currentCompletion;
    }

    // Extract streaming text from contentBlockDelta event
    // Example: {"event": {"contentBlockDelta": {"delta": {"text": " there"}}}}
    if (json.event?.contentBlockDelta?.delta?.text) {
      const newCompletion = currentCompletion + json.event.contentBlockDelta.delta.text;
      updateCallback(newCompletion);
      return newCompletion;
    }

    // All other events are ignored (tool messages, metadata, etc.)
    return currentCompletion;
  } catch {
    // If JSON parsing fails, skip this line
    console.debug('Failed to parse streaming event:', data);
    return currentCompletion;
  }
};
