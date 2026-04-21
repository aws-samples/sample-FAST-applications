# Streaming Guide for Agents

## Overview

Your agent sends streaming events in SSE format. This guide explains how to integrate streaming with frontend.

## Integration Steps

1. **Your agent sends streaming events** (SSE format)
2. **Update the appropriate parser file** to handle your agent's events:
   - For **Strands agents**: Modify `frontend/src/services/strandsParser.js`
   - For **LangGraph agents**: Modify `frontend/src/services/langgraphParser.js`
   - For **other agent frameworks**: Create a new parser file and import it in `agentCoreService.js`
3. **Update `ChatInterface.tsx`** (optional) to display additional info like tool usage
4. **UI displays the parsed text** in real-time

---

## Current Implementation

### Backend: Strands Agent

**File:** `patterns/strands-single-agent/basic_agent.py`

The backend yields all raw Strands streaming events without filtering:

```python
async for event in agent.stream_async(user_query):
    yield event
```

### Frontend: Event Parser

**File:** `frontend/src/services/agentCoreService.js`

The parser extracts text from nested Bedrock Converse events in the `event` key:

```javascript
const parseStreamingChunk = (line, currentCompletion, updateCallback) => {
  if (!line?.trim() || !line.startsWith('data: ')) {
    return currentCompletion;
  }

  const data = line.substring(6).trim();
  if (!data) return currentCompletion;

  try {
    const json = JSON.parse(data);

    // Extract streaming text from contentBlockDelta event
    if (json.event?.contentBlockDelta?.delta?.text) {
      const newCompletion = currentCompletion + json.event.contentBlockDelta.delta.text;
      updateCallback(newCompletion);
      return newCompletion;
    }

    return currentCompletion;
  } catch (error) {
    console.debug('Failed to parse streaming event:', data);
    return currentCompletion;
  }
};
```

### Event Structure

Strands emits raw Bedrock Converse events nested in the `event` key:

```javascript
// Message lifecycle
data: {"event": {"messageStart": {"role": "assistant"}}}  // Handled: adds newline for separation

// Text streaming
data: {"event": {"contentBlockDelta": {"delta": {"text": "Hello"}}}}
data: {"event": {"contentBlockDelta": {"delta": {"text": " there"}}}}

// Message completion
data: {"event": {"contentBlockStop": {"contentBlockIndex": 0}}}
data: {"event": {"messageStop": {"stopReason": "end_turn"}}}  // or "tool_use"

// Metadata
data: {"event": {"metadata": {"usage": {"inputTokens": 88, "outputTokens": 30}}}}
```

**Current parser handles:**
- `messageStart`: Adds double newline (`\n\n`) when there's previous content for visual separation
- `contentBlockDelta.delta.text`: Accumulates text chunks for display

**Reference:** [Bedrock Converse Stream API](https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/bedrock-runtime/client/converse_stream.html)

---

## Enhanced Approach: Using Strands Schema Events

Strands provides these event types:

- `init_event_loop`, `start_event_loop`, `complete`: Lifecycle markers
- `data`: Text chunks (accumulate as they arrive)
- `message`: Final structured message with full content
- `result`: AgentResult with stop reason and metrics
- `current_tool_use`: Tool name, ID, and input parameters
- `tool_stream_event`: Events streamed from tool execution
- `event`: Raw Bedrock Converse events (current implementation uses this)

**Note:** Some events contain non-JSON-serializable Python objects (agent instances, UUIDs, etc.) and require filtering before yielding.

**Reference:** [Strands Streaming Documentation](https://strandsagents.com/latest/documentation/docs/user-guide/concepts/streaming/overview/)

### Customizing: Pick Events to Stream

You control which events get sent (backend) and how they're displayed (frontend).

#### Step 1: Backend - Filter Events

**File:** `patterns/strands-single-agent/basic_agent.py`

Replace the event loop to filter specific events:

```python
async for event in agent.stream_async(user_query):
    # Send text chunks for display
    if 'data' in event and isinstance(event.get('data'), str):
        yield {'data': event['data']}

    # Send tool usage info
    if 'current_tool_use' in event and event['current_tool_use'].get('name'):
        yield {'tool': event['current_tool_use']['name']}
    
    # Send metadata (token usage) from raw Converse events
    if 'event' in event and event['event'].get('metadata'):
        yield {'metadata': event['event']['metadata']}
```

#### Step 2: Frontend Service - Parse Events

**File:** `frontend/src/services/agentCoreService.js`

Update the parser to handle filtered events. Add optional callbacks for tool and metadata events:

```javascript
const parseStreamingChunk = (line, currentCompletion, updateCallback, onToolUpdate, onMetadataUpdate) => {
  const json = JSON.parse(data);

  // Accumulate text chunks
  if (json.data && typeof json.data === 'string') {
    const newCompletion = currentCompletion + json.data;
    updateCallback(newCompletion);
    return newCompletion;
  }

  // Tool usage - could pass to callback for UI display
  if (json.tool) {
    console.log(`[Tool] Using: ${json.tool}`);
    // Optional: callback to update UI
    // onToolUpdate?.(json.tool);
  }

  // Token usage - could pass to callback for UI display
  if (json.metadata?.usage) {
    console.log(`[Usage] ${json.metadata.usage.totalTokens} tokens`);
    // Optional: callback to update UI
    // onMetadataUpdate?.(json.metadata.usage);
  }

return currentCompletion;
```

#### Step 3 (Optional): UI Component - Display Status

**File:** `frontend/src/components/chat/ChatInterface.tsx`

Add state and pass callbacks to display tool usage and token counts:

```javascript
const [toolStatus, setToolStatus] = useState('');
const [tokenUsage, setTokenUsage] = useState(null);

// Get auth tokens
const auth = useAuth()
const accessToken = auth.user?.access_token
const userId = auth.user?.profile?.sub

// In invokeAgentCore call, pass auth tokens and additional callbacks
const response = await invokeAgentCore(
  message,
  sessionId,
  (text) => {
    setMessages(prev => [...prev.slice(0, -1), 
      { role: 'assistant', content: text }
    ]);
  },
  accessToken,
  userId,
  // Optional tool callback
  (tool) => setToolStatus(`Using tool: ${tool}`),
  // Optional metadata callback
  (metadata) => setTokenUsage(metadata)
);

// Display in JSX
{toolStatus && <Box>{toolStatus}</Box>}
{tokenUsage && <Box>Tokens: {tokenUsage.totalTokens}</Box>}
```

**Note:** You'll need to update `agentCoreService.js` to accept and call these additional callbacks.

---

## LangGraph/LangChain Implementation

**Note:** LangGraph uses tuple-based streaming `(message_chunk, metadata)` and returns LangChain message objects with content as an array.

### Backend

**File:** `patterns/langgraph-single-agent/langgraph_agent.py`

```python
# Stream with messages mode - yields raw LangChain message chunks
async for event in graph.astream(
    {"messages": [("user", user_query)]},
    config=config,
    stream_mode="messages"
):
    message_chunk, metadata = event
    yield message_chunk  # Yields AIMessageChunk, ToolMessage, etc.
```

### Event Structure

LangGraph emits LangChain message objects that serialize to JSON with content as an **array of content blocks**:

```javascript
// Text streaming (AIMessageChunk)
data: {"content": [{"type": "text", "text": "Hello", "index": 0}], "type": "AIMessageChunk", ...}
data: {"content": [{"type": "text", "text": " there", "index": 0}], "type": "AIMessageChunk", ...}

// Tool call
data: {"content": "", "type": "AIMessageChunk", "tool_calls": [{"name": "tool_name", "args": {...}}], ...}

// Tool response
data: {"content": "Tool result", "type": "ToolMessage", "tool_call_id": "call_123", ...}

// Final chunk with usage metadata
data: {"content": "", "type": "AIMessageChunk", "chunk_position": "last", "usage_metadata": {...}}
```

### Frontend Parser

**File:** `frontend/src/services/agentCoreService.js`

The parser handles LangGraph's content array format and filters by message type:

```javascript
const parseStreamingChunk = (line, currentCompletion, updateCallback) => {
  const data = line.substring(6).trim();
  if (!data) return currentCompletion;
  
  try {
    const json = JSON.parse(data);
    
    // Handle LangGraph AIMessageChunk format (content is array)
    // Only process AIMessageChunk - filter out ToolMessage and other internal messages
    if (json.type === 'AIMessageChunk' && Array.isArray(json.content)) {
      // Handle empty content array (message start)
      if (json.content.length === 0) {
        if (currentCompletion) {
          const newCompletion = currentCompletion + '\n\n';
          updateCallback(newCompletion);
          return newCompletion;
        }
        return currentCompletion;
      }
      
      // Extract text from content blocks
      const textContent = json.content
        .filter(block => block.type === 'text' && block.text)
        .map(block => block.text)
        .join('');
      
      if (textContent) {
        const newCompletion = currentCompletion + textContent;
        updateCallback(newCompletion);
        return newCompletion;
      }
    }
    
    return currentCompletion;
  } catch (error) {
    console.debug('Failed to parse streaming event:', data);
    return currentCompletion;
  }
};
```

**Key Points:**
- Filter by `type === 'AIMessageChunk'` to only process assistant responses
- Ignore `ToolMessage` and other internal message types
- `content` is an **array of content blocks**, not a string
- Each block has `type`, `text`, and `index` fields
- Filter for `type === 'text'` to extract text content
- Join multiple text blocks if present

**Why Content is an Array:**
LangChain uses content blocks to support multimodal messages (text, images, tool calls) following the Anthropic/OpenAI message format.

**References:**
- [LangGraph Streaming](https://docs.langchain.com/oss/python/langgraph/streaming)
- [LangChain Streaming](https://docs.langchain.com/oss/python/langchain/streaming)

---

## Debugging

Enable console logging in the parser:

```javascript
console.log('[Streaming Event]', data);
```

Open browser console (F12) to see all events from your agent.
