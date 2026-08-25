"""Tests for backward-compatible chat history support in /v1/chat and WebSocket."""

import json
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage

# ---------------------------------------------------------------------------
# /v1/chat endpoint — chat history tests
# ---------------------------------------------------------------------------


def test_chat_with_full_history_without_thread_id(client, monkeypatch):
    """When no thread_id is provided and multiple messages are sent, all messages are forwarded to the graph."""
    from aviator.api import v1

    mock_graph = AsyncMock()
    mock_graph.ainvoke.return_value = {
        "messages": [AIMessage(content="Chrystel Great is the adjuster.")],
        "references": [],
    }
    monkeypatch.setattr(v1.aviator, "get_graph", AsyncMock(return_value=mock_graph))

    payload = {
        "context": "",
        "messages": [
            {"author": "user", "content": "Are there any good examples of closed cases for insulin shipments?"},
            {"author": "ai", "content": "Yes. Claim number SN-2023-921032..."},
            {"author": "user", "content": "are any over $50,000?"},
            {"author": "ai", "content": "Yes, there are two closed cases..."},
            {"author": "user", "content": "who is the adjuster for claim 0001034"},
        ],
    }

    response = client.post("/v1/chat", json=payload)

    assert response.status_code == 200
    # Verify all 5 messages were sent to the graph
    call_args = mock_graph.ainvoke.call_args
    input_messages = call_args[1]["input"]["messages"] if "input" in call_args[1] else call_args[0][0]["messages"]
    assert len(input_messages) == 5


def test_chat_with_single_message_without_thread_id(client, monkeypatch):
    """When no thread_id and only one message, send just that message (default behavior)."""
    from aviator.api import v1

    mock_graph = AsyncMock()
    mock_graph.ainvoke.return_value = {
        "messages": [AIMessage(content="Hello!")],
        "references": [],
    }
    monkeypatch.setattr(v1.aviator, "get_graph", AsyncMock(return_value=mock_graph))

    payload = {
        "messages": [{"author": "user", "content": "Hello"}],
    }

    response = client.post("/v1/chat", json=payload)

    assert response.status_code == 200
    call_args = mock_graph.ainvoke.call_args
    input_messages = call_args[1]["input"]["messages"] if "input" in call_args[1] else call_args[0][0]["messages"]
    assert len(input_messages) == 1


def test_chat_with_thread_id_sends_only_last_message(client, monkeypatch):
    """When thread_id is present, only the last message is sent regardless of history length."""
    from aviator.api import v1

    mock_graph = AsyncMock()
    mock_graph.ainvoke.return_value = {
        "messages": [AIMessage(content="The adjuster is Chrystel Great.")],
        "references": [],
    }
    monkeypatch.setattr(v1.aviator, "get_graph", AsyncMock(return_value=mock_graph))

    payload = {
        "context": json.dumps({"thread_id": "existing-thread-123"}),
        "messages": [
            {"author": "user", "content": "First question"},
            {"author": "ai", "content": "First answer"},
            {"author": "user", "content": "Second question"},
        ],
    }

    response = client.post("/v1/chat", json=payload)

    assert response.status_code == 200
    call_args = mock_graph.ainvoke.call_args
    input_messages = call_args[1]["input"]["messages"] if "input" in call_args[1] else call_args[0][0]["messages"]
    # Only the last message should be sent since thread_id exists
    assert len(input_messages) == 1
    assert input_messages[0][1] == "Second question"


# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------


def test_chat_history_must_start_with_user(client, monkeypatch):
    """Chat history starting with an AI message should fail validation."""
    from aviator.api import v1

    mock_graph = AsyncMock()
    monkeypatch.setattr(v1.aviator, "get_graph", AsyncMock(return_value=mock_graph))

    payload = {
        "messages": [
            {"author": "ai", "content": "I started the conversation"},
            {"author": "user", "content": "Now I reply"},
            {"author": "ai", "content": "And I respond"},
            {"author": "user", "content": "Final question"},
        ],
    }

    response = client.post("/v1/chat", json=payload)
    assert response.status_code == 422
    assert "start and end with a user message" in response.json()["detail"]


def test_chat_history_must_end_with_user(client, monkeypatch):
    """Chat history ending with an AI message should fail validation."""
    from aviator.api import v1

    mock_graph = AsyncMock()
    monkeypatch.setattr(v1.aviator, "get_graph", AsyncMock(return_value=mock_graph))

    payload = {
        "messages": [
            {"author": "user", "content": "Question"},
            {"author": "ai", "content": "Answer"},
        ],
    }

    response = client.post("/v1/chat", json=payload)
    assert response.status_code == 422
    assert "start and end with a user message" in response.json()["detail"]


def test_chat_history_no_consecutive_same_role(client, monkeypatch):
    """Two consecutive user messages should fail validation."""
    from aviator.api import v1

    mock_graph = AsyncMock()
    monkeypatch.setattr(v1.aviator, "get_graph", AsyncMock(return_value=mock_graph))

    payload = {
        "messages": [
            {"author": "user", "content": "First question"},
            {"author": "user", "content": "Second question"},
            {"author": "ai", "content": "Answer"},
            {"author": "user", "content": "Third question"},
        ],
    }

    response = client.post("/v1/chat", json=payload)
    assert response.status_code == 422
    assert "consecutive messages from the same role" in response.json()["detail"]


def test_chat_history_consecutive_ai_messages_rejected(client, monkeypatch):
    """Two consecutive AI messages should fail validation."""
    from aviator.api import v1

    mock_graph = AsyncMock()
    monkeypatch.setattr(v1.aviator, "get_graph", AsyncMock(return_value=mock_graph))

    payload = {
        "messages": [
            {"author": "user", "content": "Question"},
            {"author": "ai", "content": "First answer"},
            {"author": "ai", "content": "Second answer"},
            {"author": "user", "content": "Follow up"},
        ],
    }

    response = client.post("/v1/chat", json=payload)
    assert response.status_code == 422
    assert "consecutive messages from the same role" in response.json()["detail"]


def test_chat_history_with_human_and_assistant_aliases(client, monkeypatch):
    """Chat history should work with 'human'/'assistant' role aliases."""
    from aviator.api import v1

    mock_graph = AsyncMock()
    mock_graph.ainvoke.return_value = {
        "messages": [AIMessage(content="Response")],
        "references": [],
    }
    monkeypatch.setattr(v1.aviator, "get_graph", AsyncMock(return_value=mock_graph))

    payload = {
        "messages": [
            {"author": "human", "content": "Question 1"},
            {"author": "assistant", "content": "Answer 1"},
            {"author": "human", "content": "Question 2"},
        ],
    }

    response = client.post("/v1/chat", json=payload)
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Unit tests for shared ChatHistoryValidator
# ---------------------------------------------------------------------------


def test_chat_history_validator_empty_messages():
    """Empty messages list should not raise."""
    from aviator.api.chat_history import ChatHistoryValidator

    ChatHistoryValidator.validate([])


def test_chat_history_validator_single_user_message():
    """Single user message should pass validation."""
    from aviator.api.chat_history import ChatHistoryValidator

    ChatHistoryValidator.validate([("user", "hello")])


def test_chat_history_validator_valid_alternating():
    """Valid alternating user/ai pattern should pass."""
    from aviator.api.chat_history import ChatHistoryValidator

    ChatHistoryValidator.validate(
        [
            ("user", "q1"),
            ("ai", "a1"),
            ("user", "q2"),
        ]
    )


def test_chat_history_validator_starts_with_ai_raises():
    """Starting with AI should raise start/end validation error."""
    from aviator.api.chat_history import ChatHistoryStartEndUserError, ChatHistoryValidator

    with pytest.raises(ChatHistoryStartEndUserError):
        ChatHistoryValidator.validate(
            [
                ("ai", "a1"),
                ("user", "q1"),
            ]
        )


def test_chat_history_validator_consecutive_users_raises():
    """Consecutive user messages should raise consecutive-role validation error."""
    from aviator.api.chat_history import ChatHistoryConsecutiveRoleError, ChatHistoryValidator

    with pytest.raises(ChatHistoryConsecutiveRoleError):
        ChatHistoryValidator.validate(
            [
                ("user", "q1"),
                ("human", "q2"),
                ("ai", "a1"),
                ("user", "q3"),
            ]
        )


def test_chat_history_validator_matches_ws_use_case():
    """Validator supports the same history shape used by WebSocket payloads."""
    from aviator.api.chat_history import ChatHistoryValidator

    ChatHistoryValidator.validate(
        [
            ("user", "q1"),
            ("assistant", "a1"),
            ("human", "q2"),
        ]
    )
