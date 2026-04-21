// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * LLM Council Agent Streaming Parser
 * 
 * This parser processes Server-Sent Events (SSE) from the LLM Council agent
 * which implements a 3-stage deliberation process:
 * 1. Stage 1: Multiple models provide independent responses
 * 2. Stage 2: Models anonymously rank each other's responses
 * 3. Stage 3: Chairman model synthesizes final answer
 * 
 * EVENTS HANDLED:
 * - stage1_start: Stage 1 beginning
 * - stage1_complete: Stage 1 results with individual model responses
 * - stage2_start: Stage 2 beginning
 * - stage2_complete: Stage 2 results with rankings and scores
 * - stage3_start: Stage 3 beginning
 * - stage3_complete: Stage 3 results with final synthesis
 * - complete: All stages finished
 * - error: An error occurred
 */

/**
 * Parse a streaming chunk from the LLM Council agent.
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

    // Handle different event types
    switch (json.type) {
      case 'stage1_start':
        // Stage 1 starting - show status message
        updateCallback({
          type: 'status',
          stage: 1,
          message: json.message || 'Collecting responses from council members...'
        });
        break;

      case 'stage1_complete':
        // Stage 1 complete - show individual responses
        updateCallback({
          type: 'stage1',
          data: json.data?.stage1
        });
        break;

      case 'stage2_start':
        // Stage 2 starting - show status message
        updateCallback({
          type: 'status',
          stage: 2,
          message: json.message || 'Council members are reviewing and ranking responses...'
        });
        break;

      case 'stage2_complete':
        // Stage 2 complete - show rankings
        updateCallback({
          type: 'stage2',
          data: json.data?.stage2,
          metadata: json.data?.metadata
        });
        break;

      case 'stage3_start':
        // Stage 3 starting - show status message
        updateCallback({
          type: 'status',
          stage: 3,
          message: json.message || 'Chairman is synthesizing final response...'
        });
        break;

      case 'stage3_complete':
        // Stage 3 complete - show final synthesis
        updateCallback({
          type: 'stage3',
          data: json.data?.stage3
        });
        break;

      case 'complete':
        // All stages complete
        updateCallback({
          type: 'complete',
          message: json.message || 'Council deliberation complete'
        });
        break;

      case 'error':
        // Error occurred
        updateCallback({
          type: 'error',
          error: json.error
        });
        break;

      default:
        // Unknown event type - log and ignore
        console.debug('Unknown council event type:', json.type);
    }

    return currentCompletion;
  } catch {
    // If JSON parsing fails, skip this line
    console.debug('Failed to parse council streaming event:', data);
    return currentCompletion;
  }
};
