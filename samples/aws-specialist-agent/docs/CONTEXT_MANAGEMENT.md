# Context Management Guide

Practical guide for managing LLM context windows in long-running or multi-turn agent conversations within FAST.

As agents handle longer conversations — especially those involving many tool calls, large tool results, or iterative workflows — the conversation history can grow to exceed the model's context window. Even before overflow, large contexts degrade model performance, increase latency, and balloon costs. Context management strategies address this by proactively or reactively compressing, trimming, or summarizing conversation history.

This guide covers the built-in options available in Strands and LangGraph, when to use each, and how to implement a fully custom solution when the built-in options don't fit your use case.

---

## When Do You Need Context Management?

You likely need context management if your agent:

- Runs **multi-turn conversations** that accumulate many messages over time
- Performs **iterative workflows** with many sequential tool calls (e.g., code generation loops, data analysis pipelines)
- Returns **large tool results** (e.g., file contents, API responses, screenshots)
- Runs **autonomously for extended periods** without human intervention

For simple single-turn or short multi-turn chat agents, the default behavior (no explicit context management) is usually sufficient, especially with the most powerful LLMs with large (200k+ token) context windows.

---

## Strategy Comparison

| Strategy                  | Information Loss                                      | Complexity | Best For                                                                                                                                                                                  |
| ------------------------- | ----------------------------------------------------- | ---------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Sliding Window**        | High — older messages are dropped entirely            | Low        | Simple chat bots, short conversations, user experiences which don't require referencing older messages, or applications with long term memory built-in to handle referencing older topics |
| **Summarization**         | Low — key information is preserved in compressed form | Medium     | Multi-turn assistants, iterative workflows, user experiences where pausing for several seconds when the context window overflows (to generate the summary) are acceptable.                |
| **Proactive Compression** | Low–Medium — triggers before overflow                 | Medium     | Long-running autonomous agents, applications using weaker LLMs which struggle to return high quality results when their context window is over 50% full.                                  |
| **Custom Hook-Based**     | Configurable — full control over what is preserved    | High       | Specialized long-running agents with external memory                                                                                                                                      |

---

## Option 1: Sliding Window (Strands)

The simplest approach. Keeps the N most recent messages and discards older ones. Strands provides `SlidingWindowConversationManager` out of the box.

### How It Works

- Maintains a fixed window of messages (default: 40)
- When the window is exceeded, the oldest messages are removed
- Preserves tool use/result pairs to avoid invalid conversation state
- Optionally truncates large tool results (keeping first/last 200 characters)

### Configuration

```python
from strands import Agent
from strands.agent.conversation_manager import SlidingWindowConversationManager

agent = Agent(
    model=model,
    tools=tools,
    conversation_manager=SlidingWindowConversationManager(
        window_size=40,                # Max messages to keep (default: 40)
        should_truncate_results=True,  # Truncate large tool results (default: True)
    ),
)
```

### Proactive Compression

The sliding window manager also supports proactive compression, which triggers context reduction before the context window overflows rather than waiting for an error:

```python
conversation_manager=SlidingWindowConversationManager(
    window_size=40,
    proactive_compression=True,  # Compress at 70% context usage (default threshold)
)

# Or with a custom threshold:
conversation_manager=SlidingWindowConversationManager(
    window_size=40,
    proactive_compression={"compression_threshold": 0.5},  # Compress at 50%
)
```

### Per-Turn Management

For agents that perform many tool operations in loops (e.g., web browsing with frequent screenshots), enable per-turn management to proactively trim before every model call:

```python
conversation_manager=SlidingWindowConversationManager(
    window_size=40,
    per_turn=True,   # Apply before every model call
    # per_turn=5,    # Or apply every 5 model calls
)
```

### Applying to FAST Strands Pattern

To add sliding window management to the `agent/strands-single-agent/basic_agent.py`, pass the `conversation_manager` parameter when constructing the `Agent`:

```python
from strands.agent.conversation_manager import SlidingWindowConversationManager

# In create_strands_agent():
agent = Agent(
    model=model,
    system_prompt=SYSTEM_PROMPT,
    tools=[gateway_client, code_tools.execute_python_securely],
    conversation_manager=SlidingWindowConversationManager(window_size=40),
    session_manager=session_manager,
)
```

### Pros and Cons

| Pros                      | Cons                                                       |
| ------------------------- | ---------------------------------------------------------- |
| Zero additional LLM calls | Complete loss of older context                             |
| No added latency or cost  | Agent "forgets" earlier conversation                       |
| Simple to configure       | Not suitable for long-running tasks requiring full history |

📚 **Strands Docs**: [SlidingWindowConversationManager API](https://strandsagents.com/docs/api/python/strands.agent.conversation_manager.sliding_window_conversation_manager/)

---

## Option 2: Summarizing Conversation Manager (Strands)

Summarizes older messages using an LLM call instead of discarding them entirely. This preserves key information while reducing token count.

### How It Works

- When context overflow occurs (or proactive threshold is reached), the oldest N% of messages are summarized
- The summary replaces the original messages as a single user message
- Recent messages are preserved verbatim
- A separate LLM call generates the summary

### Configuration

```python
from strands import Agent
from strands.agent.conversation_manager import SummarizingConversationManager

agent = Agent(
    model=model,
    tools=tools,
    conversation_manager=SummarizingConversationManager(
        summary_ratio=0.3,               # Summarize oldest 30% of messages (default)
        preserve_recent_messages=10,      # Always keep last 10 messages (default)
        proactive_compression=True,       # Compress at 70% context usage
    ),
)
```

### Using a Separate Summarization Agent

By default, the summarizing manager uses the parent agent (with its tools) to generate summaries. For more control, you can provide a dedicated summarization agent:

```python
from strands import Agent
from strands.agent.conversation_manager import SummarizingConversationManager

# Lightweight agent just for summarization — no tools needed
summarizer = Agent(
    model="us.anthropic.claude-sonnet-4-20250514-v1:0",
    system_prompt="You are a conversation summarizer. Create concise bullet-point summaries.",
)

agent = Agent(
    model=model,
    tools=tools,
    conversation_manager=SummarizingConversationManager(
        summary_ratio=0.3,
        preserve_recent_messages=10,
        summarization_agent=summarizer,
        proactive_compression={"compression_threshold": 0.5},
    ),
)
```

### Applying to FAST Strands Pattern

```python
from strands.agent.conversation_manager import SummarizingConversationManager

# In create_strands_agent():
agent = Agent(
    model=model,
    system_prompt=SYSTEM_PROMPT,
    tools=[gateway_client, code_tools.execute_python_securely],
    conversation_manager=SummarizingConversationManager(
        summary_ratio=0.3,
        preserve_recent_messages=10,
        proactive_compression=True,
    ),
    session_manager=session_manager,
)
```

### Pros and Cons

| Pros                                         | Cons                                      |
| -------------------------------------------- | ----------------------------------------- |
| Preserves key information from older context | Additional LLM call adds latency and cost |
| Configurable compression ratio               | Summary quality depends on the model      |
| Built-in, no custom code needed              | May lose nuance or specific details       |

📚 **Strands Docs**: [SummarizingConversationManager API](https://strandsagents.com/docs/api/python/strands.agent.conversation_manager.summarizing_conversation_manager/)

---

## Option 3: LangGraph Middleware (Trim / Summarize)

LangGraph provides middleware-based approaches for context management using `@before_model` decorators.

### Trim Messages

Remove older messages before each model call, keeping only recent ones:

```python
from langchain.messages import RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langchain.agents import create_agent, AgentState
from langchain.agents.middleware import before_model
from langgraph.runtime import Runtime
from typing import Any


@before_model
def trim_messages(state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
    """Keep only the last few messages to fit context window."""
    messages = state["messages"]
    if len(messages) <= 10:
        return None

    first_msg = messages[0]
    recent_messages = messages[-10:]
    return {
        "messages": [
            RemoveMessage(id=REMOVE_ALL_MESSAGES),
            first_msg,
            *recent_messages,
        ]
    }
```

### Summarize Messages

Use the built-in `SummarizationMiddleware` for automatic summarization:

```python
from langchain.agents import create_agent
from langchain.agents.middleware import SummarizationMiddleware
from langgraph.checkpoint.memory import InMemorySaver

agent = create_agent(
    model="us.anthropic.claude-sonnet-4-20250514-v1:0",
    tools=tools,
    middleware=[
        SummarizationMiddleware(
            model="us.anthropic.claude-sonnet-4-20250514-v1:0",
            trigger=("tokens", 4000),   # Trigger when token count exceeds 4000
            keep=("messages", 20),      # Keep last 20 messages verbatim
        )
    ],
    checkpointer=InMemorySaver(),
)
```

### Pros and Cons

| Pros                                         | Cons                                 |
| -------------------------------------------- | ------------------------------------ |
| Native LangGraph integration                 | Requires LangGraph-specific patterns |
| Composable with other middleware             | Different API than Strands           |
| `SummarizationMiddleware` handles complexity | Token counting adds minor overhead   |

📚 **LangGraph Docs**: [Short-term Memory & Summarization](https://docs.langchain.com/oss/python/langchain/short-term-memory)

---

## Option 4: Custom Hook-Based Context Management (Strands)

For advanced use cases — particularly long-running autonomous agents — the built-in managers may not provide enough control. You can implement a fully custom solution using Strands hooks.

This approach is ideal when you need to:

- Trigger summarization at a specific **percentage** of context window usage (not just on overflow)
- **Re-inject external memory** (e.g., a log file, knowledge base, or structured state) after compression
- Use a **raw Bedrock Converse call** for summarization (avoiding the agent loop and tool invocations)
- Emit **custom stream events** to notify the frontend about context management activity
- Implement **fallback strategies** when summarization fails

### Architecture

The pattern uses two components working together:

1. **A no-op `ConversationManager`** — Disables Strands' built-in context management entirely
2. **A `HookProvider`** — Registers on `BeforeModelCallEvent` to perform proactive context management before every LLM call

### Implementation

#### Step 1: Create a No-Op Conversation Manager inheriting from `strands.agent.conversation_manager.ConversationManager`

```python
from strands.agent.conversation_manager import ConversationManager


class NoOpConversationManager(ConversationManager):
    """Disables built-in context management.

    Strands requires a ConversationManager but we handle context reduction
    ourselves via a hook. Both methods are intentionally empty.
    """

    def apply_management(self, agent, **kwargs):
        """No-op: context management is handled by the hook."""
        pass

    def reduce_context(self, agent, **kwargs):
        """No-op: context management is handled by the hook."""
        pass
```

#### Step 2: Create the Context Check Hook

```python
import logging
import os

import boto3
from strands.hooks import HookProvider, HookRegistry, BeforeModelCallEvent

logger = logging.getLogger(__name__)

# Set this to your model's context window size
CONTEXT_WINDOW_TOKENS = 200_000  # e.g., Claude Sonnet

SUMMARIZATION_PROMPT = """You are a conversation summarizer. Provide a concise summary.

Format Requirements:
- Create a structured summary in bullet-point format
- Do NOT respond conversationally
- Include: key decisions, tool executions and results, current state, next steps
"""


class ContextCheckHook(HookProvider):
    """Proactively summarize conversation when context usage exceeds threshold.

    Fires on BeforeModelCallEvent. When context exceeds threshold_pct,
    summarizes older messages via a direct Bedrock Converse call (no agent
    loop, no tools), preserving the most recent messages verbatim.

    Args:
        threshold_pct: Percentage of context window that triggers summarization.
        preserve_recent: Number of most recent messages to keep verbatim.
        model_id: Model ID to use for the summarization call.
    """

    def __init__(
        self,
        threshold_pct: float = 50.0,
        preserve_recent: int = 6,
        model_id: str = "us.anthropic.claude-sonnet-4-20250514-v1:0",
    ):
        self.threshold_pct = threshold_pct
        self.preserve_recent = preserve_recent
        self._model_id = model_id
        self._bedrock = None

    def register_hooks(self, registry: HookRegistry, **kwargs) -> None:
        """Register the before-model-call check."""
        registry.add_callback(BeforeModelCallEvent, self._check)

    def _check(self, event: BeforeModelCallEvent) -> None:
        """Check context usage and trigger summarization if threshold exceeded."""
        agent = event.agent
        # Walk backward to find the last assistant message with usage metadata
        for msg in reversed(agent.messages):
            if msg.get("role") == "assistant":
                usage = msg.get("metadata", {}).get("usage", {})
                if usage:
                    input_tokens = usage.get("inputTokens", 0)
                    cache_tokens = usage.get("cacheReadInputTokens", 0)
                    pct = (input_tokens + cache_tokens) / CONTEXT_WINDOW_TOKENS * 100
                    if pct >= self.threshold_pct:
                        logger.info("Context at %.1f%% — summarizing", pct)
                        self._summarize_and_replace(agent)
                return  # Only check the most recent assistant message

    def _summarize_and_replace(self, agent) -> None:
        """Summarize older messages and replace them with the summary."""
        messages = agent.messages
        if len(messages) <= self.preserve_recent:
            return

        # Find split point — avoid breaking tool_use/tool_result pairs
        split = len(messages) - self.preserve_recent
        while split > 0 and self._is_tool_result(messages[split]):
            split -= 1
        if split <= 0:
            return

        to_summarize = messages[:split]
        to_keep = messages[split:]

        # Convert to text-only format for the summarization call
        converse_messages = self._to_text_only(to_summarize)
        converse_messages.append({
            "role": "user",
            "content": [{"text": "Please summarize this conversation."}],
        })

        # Direct Bedrock Converse call — no agent loop, no tools
        if not self._bedrock:
            self._bedrock = boto3.client(
                "bedrock-runtime",
                region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
            )

        try:
            response = self._bedrock.converse(
                modelId=self._model_id,
                system=[{"text": SUMMARIZATION_PROMPT}],
                messages=converse_messages,
                inferenceConfig={"maxTokens": 4096},
            )
            summary_text = response["output"]["message"]["content"][0]["text"]
        except Exception as e:
            logger.error("Summarization failed: %s — falling back to truncation", e)
            summary_text = "(conversation history truncated due to context limits)"

        # Replace agent messages in-place
        agent.messages[:] = [
            {"role": "user", "content": [{"text": f"## Previous Conversation Summary\n\n{summary_text}"}]},
            {"role": "assistant", "content": [{"text": "Understood. I'll continue from where we left off."}]},
        ] + to_keep

    def _is_tool_result(self, msg: dict) -> bool:
        """Check if a message contains a toolResult block."""
        content = msg.get("content", [])
        if isinstance(content, list):
            return any(isinstance(b, dict) and "toolResult" in b for b in content)
        return False

    def _to_text_only(self, messages: list[dict]) -> list[dict]:
        """Convert messages to text-only format (strips toolUse/toolResult blocks).

        This is necessary because Bedrock Converse API doesn't allow toolUse
        blocks without corresponding tool definitions in the request.
        """
        result = []
        for msg in messages:
            role = msg.get("role")
            if role not in ("user", "assistant"):
                continue
            content = msg.get("content", [])
            if isinstance(content, str):
                result.append({"role": role, "content": [{"text": content}]})
                continue
            text_blocks = []
            for block in content:
                if isinstance(block, dict):
                    if "text" in block:
                        text_blocks.append({"text": block["text"]})
                    elif "toolUse" in block:
                        name = block["toolUse"].get("name", "unknown")
                        text_blocks.append({"text": f"[Called tool: {name}]"})
                    elif "toolResult" in block:
                        tr_content = block["toolResult"].get("content", [])
                        snippet = next(
                            (c["text"][:200] for c in tr_content if isinstance(c, dict) and "text" in c),
                            "result received",
                        )
                        text_blocks.append({"text": f"[Tool result: {snippet}]"})
            if text_blocks:
                result.append({"role": role, "content": text_blocks})

        # Ensure alternating user/assistant (Bedrock requirement)
        cleaned = []
        for msg in result:
            if cleaned and cleaned[-1]["role"] == msg["role"]:
                cleaned[-1]["content"].extend(msg["content"])
            else:
                cleaned.append(msg)
        if cleaned and cleaned[0]["role"] == "assistant":
            cleaned.insert(0, {"role": "user", "content": [{"text": "(start of conversation)"}]})

        return cleaned
```

#### Step 3: Wire It Into the Agent

```python
from strands import Agent

agent = Agent(
    model=model,
    system_prompt=SYSTEM_PROMPT,
    tools=tools,
    hooks=[ContextCheckHook(threshold_pct=50.0, preserve_recent=6)],
    conversation_manager=NoOpConversationManager(),
    session_manager=session_manager,
)
```

### Advanced: Re-Injecting External Memory After Summarization

For long-running agents that maintain structured state outside the conversation (e.g., a progress log, a results file, or a knowledge base), you can re-inject that state after summarization to restore critical context that may have been compressed:

```python
def _summarize_and_replace(self, agent) -> None:
    # ... (summarization logic as above) ...

    # After replacing messages, re-inject external state
    self._inject_external_state(agent)

def _inject_external_state(self, agent) -> None:
    """Re-inject structured external state after context compression."""
    state_path = os.environ.get("AGENT_STATE_FILE", "")
    if not state_path:
        return
    try:
        with open(state_path) as f:
            state_content = f.read()
    except FileNotFoundError:
        return

    agent.messages.append({
        "role": "user",
        "content": [{"text": (
            "Context was compressed. Here is the current state log — "
            "use it to recall what has been done and the current status:\n\n"
            + state_content
        )}],
    })
```

This pattern is particularly powerful for agents that:

- Maintain a running log of actions taken and results observed
- Need to preserve structured data (tables, configurations) across compressions
- Operate in iterative optimization loops where history of attempts matters

### Pros and Cons

| Pros                                                           | Cons                                                |
| -------------------------------------------------------------- | --------------------------------------------------- |
| Full control over trigger timing and compression behavior      | More code to write and maintain                     |
| Can integrate external memory re-injection                     | Must handle Bedrock API format constraints manually |
| Proactive — fires before overflow, preserving more detail      | Requires understanding of message format internals  |
| Direct Bedrock call avoids agent loop / tool invocation issues | Must handle tool_use/tool_result pair splitting     |

---

## Design Decisions and Best Practices

### Choosing a Threshold

- **70%** (default for built-in proactive compression) — Good for most multi-turn chat agents. Leaves headroom for the model's response.
- **50%** — Better for long-running autonomous agents that accumulate context rapidly. Triggers earlier, preserving more detail in the summary.
- **On overflow only** (reactive) — Simplest, but risks losing information if the overflow error discards the last request.

Overall the threshold is a metric that can be quantitatively determined if you have data to test on. For example try running with a variety of thresholds and see at what threshold hallucinations start to happen and be sure to set a threshold below that. This can also be done with A/B testing and collecting user feedback signals.

### Preserving Tool Pairs

When trimming or splitting messages, never break a `toolUse` block from its corresponding `toolResult`. This creates an invalid conversation state that will cause API errors. Always walk backward from your split point to find a clean boundary.

### Text-Only Conversion for Summarization

When making a separate Bedrock Converse call for summarization, you cannot include `toolUse` or `toolResult` blocks without also providing the corresponding tool definitions. The simplest solution is to convert these blocks to text descriptions (e.g., `[Called tool: analyze_data]`, `[Tool result: 42 records processed]`).

### Cost Considerations

Each summarization call is an additional LLM invocation:

- A summarization of ~50K tokens of context using Claude Sonnet costs approximately $0.15–$0.25
- For agents that trigger summarization frequently, consider using a smaller/cheaper model for the summarization call
- The sliding window approach has zero additional cost but loses information

### Conversation Validity Rules (Bedrock Converse API)

When manipulating `agent.messages` directly, ensure:

1. Messages alternate between `user` and `assistant` roles
2. The conversation starts with a `user` message
3. Every `toolUse` block has a corresponding `toolResult` in the next user message
4. No empty content blocks

---

## Quick Reference: Which Option to Choose

| Use Case                                      | Recommended Approach                                      |
| --------------------------------------------- | --------------------------------------------------------- |
| Simple chatbot, short conversations           | Sliding Window (Option 1)                                 |
| Multi-turn assistant, moderate length         | Summarizing Manager (Option 2) with proactive compression |
| LangGraph-based agent                         | LangGraph Middleware (Option 3)                           |
| Long-running autonomous agent (hours)         | Custom Hook (Option 4) with external memory re-injection  |
| Agent with large tool results (images, files) | Sliding Window with `per_turn=True` and truncation        |

---

## Further Reading

- [Strands ConversationManager API](https://strandsagents.com/docs/api/python/strands.agent.conversation_manager.conversation_manager/)
- [Strands SlidingWindowConversationManager](https://strandsagents.com/docs/api/python/strands.agent.conversation_manager.sliding_window_conversation_manager/)
- [Strands SummarizingConversationManager](https://strandsagents.com/docs/api/python/strands.agent.conversation_manager.summarizing_conversation_manager/)
- [Strands Hooks API](https://strandsagents.com/docs/api/python/strands.hooks.events/)
- [LangGraph Short-term Memory](https://docs.langchain.com/oss/python/langchain/short-term-memory)
- [LangMem Summarization Guide](https://langchain-ai.github.io/langmem/guides/summarization/)
- [FAST Memory Integration Guide](./MEMORY_INTEGRATION.md)

## Design notes

Chat UX decisions specific to this derivative:

- **Chat history sidebar with Lambda-backed restore**: previous conversations are listed by hitting a dedicated history API and individual sessions are restored by querying a DynamoDB index. Storing only an index in DDB (not the full messages) keeps writes cheap and avoids duplicating Memory state.
- **Slim streaming events**: streaming payloads are minimised to avoid hitting the AgentCore Runtime per-event size limit. Diagnostic data is omitted from the wire format and reconstructed client-side.
- **Sticky auto-scroll**: the chat view auto-scrolls during streaming only while the user is at the bottom, so reading earlier messages mid-stream is not interrupted.
- **Cmd+Enter to send, IME-safe**: composition events from Japanese/Chinese input methods are detected so Enter during conversion does not submit. Cmd/Ctrl+Enter is the canonical submit binding.
- **Tolerate OpenAI tool-use ID reuse**: OpenAI re-uses `toolUseId` values across rounds in the same turn while Anthropic does not. The chat UI tracks tool calls without assuming IDs are globally unique.
- **Mermaid rendering**: fenced ` ```mermaid ` blocks in agent responses are rendered to SVG client-side so architecture answers can include diagrams without extra tooling.
