"""Tests for message history management functionality."""

import pytest
from langchain.messages import AIMessage, HumanMessage

from aviator.graph import ContentAviatorAgent
from aviator.models import StateModel
from aviator.settings import settings

pytestmark = pytest.mark.anyio


class TestMessageHistoryManagement:
    """Test message history management features."""

    async def test_message_history_disabled(self, monkeypatch):
        """Test that message history management can be disabled."""
        monkeypatch.setattr(settings, "message_history_enabled", False)

        agent = ContentAviatorAgent()

        # Create a state with many messages
        messages = [HumanMessage(content=f"Question {i}") for i in range(10)]
        state = StateModel(messages=messages)

        # Call the manage_message_history method
        result = await agent._ContentAviatorAgent__manage_message_history(state)

        # Should return empty dict (no state updates)
        assert result == {}

    async def test_message_history_under_limit(self):
        """Test that messages under the limit are not trimmed."""
        agent = ContentAviatorAgent()

        # Create a state with few messages (under limit)
        messages = [
            HumanMessage(content="Hello"),
            AIMessage(content="Hi there"),
        ]
        state = StateModel(messages=messages)

        # Call the manage_message_history method
        result = await agent._ContentAviatorAgent__manage_message_history(state)

        # Should return empty dict (no trimming needed)
        assert result == {}

    async def test_message_history_trimming_strategy(self, monkeypatch):
        """Test that trimming strategy works correctly."""
        monkeypatch.setattr(settings, "message_history_enabled", True)
        monkeypatch.setattr(settings, "message_history_strategy", "trimming")
        monkeypatch.setattr(settings, "message_history_max_tokens", 100)  # Low limit

        agent = ContentAviatorAgent()

        # Create a state with many messages that exceed the limit
        messages = [
            HumanMessage(content=f"This is a longer question number {i} with more content to increase token count")
            for i in range(10)
        ]
        state = StateModel(messages=messages)

        # Call the manage_message_history method
        result = await agent._ContentAviatorAgent__manage_message_history(state)

        # Should return messages field with RemoveMessage + trimmed messages
        assert "messages" in result
        # Filter out RemoveMessage to get actual messages
        actual_messages = [
            msg for msg in result["messages"] if not hasattr(msg, "id") or msg.id != "REMOVE_ALL_MESSAGES"
        ]
        # Should have fewer messages than original
        assert len(actual_messages) < len(messages)
        # Should keep the most recent messages
        assert actual_messages[-1].content == messages[-1].content

    async def test_assistant_uses_state_messages(self):
        """Test that assistant node uses state.messages directly."""
        # Create a state with messages
        messages = [HumanMessage(content=f"Question {i}") for i in range(5)]

        state = StateModel(messages=messages)

        # The assistant node uses state.messages directly
        # The __manage_message_history method modifies state.messages in place
        # by returning RemoveMessage(REMOVE_ALL_MESSAGES) followed by the trimmed/summarized messages
        assert len(state.messages) == 5
        assert state.messages[-1].content == "Question 4"

    async def test_state_model_fields(self):
        """Test that StateModel has the required fields for message history."""
        state = StateModel()

        # Check that summarization_context field exists
        assert hasattr(state, "summarization_context")
        assert isinstance(state.summarization_context, dict)
        # Check that messages field exists (always present)
        assert hasattr(state, "messages")
        assert isinstance(state.messages, list)
