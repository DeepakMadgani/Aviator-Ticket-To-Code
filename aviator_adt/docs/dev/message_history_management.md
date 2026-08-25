# Message History Management

## Overview

The Content Aviator ADT implements message history management to handle long conversations that might exceed LLM context window limits. This feature is based on the [LangGraph documentation for managing conversation history in ReAct agents](https://langchain-ai.github.io/langgraph/how-tos/create-react-agent-manage-message-history/).

## Features

The system supports two strategies for managing message history:

1. **Summarization** (default): Uses the `SummarizationNode` from `langmem` to create summaries of earlier messages when the conversation exceeds token limits
2. **Trimming**: Uses `trim_messages` utility to keep only the most recent messages within the token limit

## Configuration

Message history management is controlled via environment variables or settings:

```python
# Enable/disable message history management
MESSAGE_HISTORY_ENABLED=true  # default: true

# Strategy: "summarization" or "trimming"
MESSAGE_HISTORY_STRATEGY=summarization  # default: summarization

# Maximum tokens before triggering history management
MESSAGE_HISTORY_MAX_TOKENS=5120  # default: 5120

# Maximum tokens for summary (when using summarization)
MESSAGE_HISTORY_MAX_SUMMARY_TOKENS=2048  # default: 2048
```

## How It Works

### Architecture

The message history management is implemented as a dedicated node in the LangGraph state machine:

```mermaid
--8<-- "graph_mermaid.md"
```

### State Model

The `StateModel` includes a new field to support message history management:

- **`summarization_context`**: Tracks summarization state to avoid re-summarizing on every call

### Message History Management Strategy

The implementation uses LangGraph's `RemoveMessage` to replace the entire message history when management is triggered:

- When disabled or under token limits, the node returns an empty dict (no state changes)
- When triggered, it returns `{"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *managed_messages]}`
- This replaces the entire conversation history with the trimmed or summarized version
- For summarization, messages are normalized to ensure proper content structure
- The LLM always receives `state.messages` directly (no separate field needed)

## Usage Examples

### Example 1: Summarization Strategy (Default)

When a conversation grows beyond the configured token limit, the system will automatically summarize earlier messages:

```python
# Long conversation example
config = {"configurable": {"thread_id": "user-123"}}

# First query
inputs = {"messages": [("user", "What's the weather in NYC?")]}
result = await graph.ainvoke(inputs, config=config)

# Multiple follow-up queries that build context
inputs = {"messages": [("user", "What's it known for?")]}
result = await graph.ainvoke(inputs, config=config)

inputs = {"messages": [("user", "Tell me about its history")]}
result = await graph.ainvoke(inputs, config=config)

# ... many more queries ...

# When token limit is reached, earlier messages are automatically replaced
# with summarized version (using RemoveMessage to clear old messages)
inputs = {"messages": [("user", "Where can I find the best bagel?")]}
result = await graph.ainvoke(inputs, config=config)
# state.messages is updated to: [SystemMessage(summary), recent messages...]
# The LLM receives state.messages directly
```

### Example 2: Trimming Strategy

To use trimming instead of summarization:

```bash
export MESSAGE_HISTORY_STRATEGY=trimming
```

This will keep only the most recent messages that fit within `MESSAGE_HISTORY_MAX_TOKENS`.

### Example 3: Disabling Message History Management

For short conversations or testing:

```bash
export MESSAGE_HISTORY_ENABLED=false
```

## Implementation Details

### The `manage_message_history` Node

The core logic is in the `__manage_message_history` method:

::: aviator.graph.ContentAviatorAgent.__manage_message_history
    options:
      heading_level: 4
      show_root_heading: false

### Integration with Assistant Node

The `__assistant` node uses `state.messages` directly. The `manage_message_history` node runs before the assistant node in the graph flow (see [graph.py](../../src/aviator/graph.py)).

::: aviator.graph.ContentAviatorAgent.__assistant
    options:
      heading_level: 4
      show_root_heading: false

## Monitoring

When message history management is triggered, you'll see streaming events in the output:

- For trimming: 
  - `"Trimming conversation history..."`
  - `"Trimmed 5 messages from history"` (if any messages were trimmed)
- For summarization: 
  - `"Summarizing conversation history..."`
  - `"Summarized 8 messages from history"` (if any messages were summarized)

These messages are sent via the LangGraph `get_stream_writer()` API.

## Best Practices

1. **Choose the right strategy**:
   - Use **summarization** for conversations where historical context is important
   - Use **trimming** for task-oriented conversations where only recent exchanges matter

2. **Tune token limits**:
   - Consider your LLM's context window
   - Leave room for system prompts, tool definitions, and responses
   - Default of 10240 tokens works well for most use cases
   - Summary max tokens (default 2048) should be sufficient for context retention

3. **Monitor token usage**:
   - Check logs for message history management events
   - Adjust limits based on your conversation patterns

4. **Test both strategies**:
   - Different types of conversations benefit from different approaches
   - A/B test to find optimal settings for your use case

## Dependencies

The summarization feature requires the `langmem` package:

```bash
uv add langmem
```

## References

- [LangGraph: How to manage conversation history in a ReAct Agent](https://langchain-ai.github.io/langgraph/how-tos/create-react-agent-manage-message-history/)
- [LangChain: Trimming Messages](https://python.langchain.com/docs/how_to/trim_messages/)
- [LangMem: SummarizationNode](https://langchain-ai.github.io/langmem/guides/summarization/)
