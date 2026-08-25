"""Test message history tool-message compaction functionality."""

from langchain.messages import AIMessage, HumanMessage, ToolMessage

from aviator.graph import RAG_HISTORY_STUB_MESSAGE, compact_tool_messages_in_history


class TestCompactToolMessages:
    """Test the compact_tool_messages_in_history function."""

    def test_compact_rag_tool_messages_default(self):
        """Test compacting only RAG ToolMessages by default."""
        messages = [
            HumanMessage(content="Question 1"),
            AIMessage(content="Answer 1"),
            ToolMessage(content="RAG result 1", tool_call_id="1", name="rag_query"),
            HumanMessage(content="Question 2"),
            AIMessage(content="Answer 2"),
            ToolMessage(content="RAG result 2", tool_call_id="2", name="rag_query"),
        ]

        compacted, compacted_count = compact_tool_messages_in_history(messages)

        assert compacted_count == 2
        assert len(compacted) == 6
        tool_messages = [msg for msg in compacted if isinstance(msg, ToolMessage)]
        assert all(msg.content == RAG_HISTORY_STUB_MESSAGE for msg in tool_messages)

    def test_compact_rag_keep_other_tools(self):
        """Test compacting RAG messages but keeping other tool messages unchanged."""
        messages = [
            HumanMessage(content="Question"),
            AIMessage(content="Searching...", tool_calls=[{"name": "rag_query", "args": {}, "id": "rag-1"}]),
            ToolMessage(content="RAG result - should be compacted", tool_call_id="rag-1", name="rag_query"),
            AIMessage(
                content="Creating chart...", tool_calls=[{"name": "generate_chart", "args": {}, "id": "chart-1"}]
            ),
            ToolMessage(content='{"chart": "data"}', tool_call_id="chart-1", name="generate_chart"),
            AIMessage(content="Here's your answer with chart"),
        ]

        compacted, compacted_count = compact_tool_messages_in_history(messages)

        # Only the RAG ToolMessage should be compacted.
        assert compacted_count == 1
        assert len(compacted) == 6

        rag_tool = next(msg for msg in compacted if isinstance(msg, ToolMessage) and msg.tool_call_id == "rag-1")
        assert rag_tool.content == RAG_HISTORY_STUB_MESSAGE

        chart_tool = next(msg for msg in compacted if isinstance(msg, ToolMessage) and msg.tool_call_id == "chart-1")
        assert "chart" in chart_tool.content

    def test_realistic_conversation_scenario(self):
        """Test realistic conversation - compacts RAG messages, keeps charts."""
        messages = [
            HumanMessage(content="What is quantum computing?"),
            AIMessage(
                content="Let me search...",
                tool_calls=[{"name": "rag_query", "args": {}, "id": "call-rag-1"}],
            ),
            ToolMessage(
                content='[{"CHUNK_ID": "1", "CHUNK_CONTENT": "' + "X" * 5000 + '"}]',
                tool_call_id="call-rag-1",
                name="rag_query",
            ),
            AIMessage(content="Quantum computing uses..."),
            HumanMessage(content="What are the applications?"),
            AIMessage(
                content="Searching...",
                tool_calls=[{"name": "rag_query", "args": {}, "id": "call-rag-2"}],
            ),
            ToolMessage(
                content='[{"CHUNK_ID": "2", "CHUNK_CONTENT": "' + "Y" * 5000 + '"}]',
                tool_call_id="call-rag-2",
                name="rag_query",
            ),
            AIMessage(content="Applications include..."),
            HumanMessage(content="Show me a chart"),
            AIMessage(
                content="Creating chart...",
                tool_calls=[{"name": "generate_vega_lite_chart", "args": {}, "id": "call-chart-1"}],
            ),
            ToolMessage(
                content='{"spec": {"mark": "bar"}}',
                tool_call_id="call-chart-1",
                name="generate_vega_lite_chart",
            ),
            AIMessage(content="Here's your chart..."),
        ]

        from langchain_core.messages.utils import count_tokens_approximately

        tokens_before = count_tokens_approximately(messages)
        compacted, compacted_count = compact_tool_messages_in_history(messages)

        # Should compact only 2 RAG ToolMessages.
        assert compacted_count == 2

        # Chart tool content should remain unchanged.
        chart_tool = next(
            msg for msg in compacted if isinstance(msg, ToolMessage) and msg.tool_call_id == "call-chart-1"
        )
        assert chart_tool.content == '{"spec": {"mark": "bar"}}'

        # RAG tool contents should be compacted.
        rag_tools = [
            msg
            for msg in compacted
            if isinstance(msg, ToolMessage) and msg.tool_call_id in {"call-rag-1", "call-rag-2"}
        ]
        assert len(rag_tools) == 2
        assert all(msg.content == RAG_HISTORY_STUB_MESSAGE for msg in rag_tools)

        # Human messages and AI messages are preserved.
        human_messages = [msg for msg in compacted if isinstance(msg, HumanMessage)]
        assert len(human_messages) == 3

        ai_messages = [msg for msg in compacted if isinstance(msg, AIMessage)]
        assert len(ai_messages) == 6

        tokens_after = count_tokens_approximately(compacted)
        assert tokens_after < tokens_before
        reduction = tokens_before - tokens_after
        assert reduction > 2000
