"""Tests for the WebSocket API handler (/v1/chat/stream)."""

import json
from unittest.mock import MagicMock, patch

import pytest


def _make_astream(*events):
    """Return an async generator function that yields the given (mode, payload) tuples."""

    async def _gen(*args, **kwargs):
        for event in events:
            yield event

    return _gen


def _make_mock_span(trace_id: str = "trace-id-test") -> MagicMock:
    """Create a mock Langfuse span with JSON-serialisable attributes."""
    span = MagicMock()
    span.trace_id = trace_id
    return span


def _make_ai_msg(content: str, msg_type: str = "ai") -> MagicMock:
    msg = MagicMock()
    msg.content = content
    msg.type = msg_type
    msg.name = None
    return msg


def _collect_ws_messages(ws, send_payload: dict) -> list[dict]:
    """Send one message and collect all replies until the 'final' frame."""
    ws.send_json(send_payload)
    messages: list[dict] = []
    while True:
        raw = ws.receive_text()
        msg = json.loads(raw)
        messages.append(msg)
        if msg.get("type") == "final":
            break
    return messages


@pytest.fixture
def client():
    """Reuse the same TestClient setup as conftest but isolated for WS tests."""
    import os

    os.environ["VECTOR_STORE"] = "memory"
    os.environ["CHECKPOINTER"] = "memory"
    os.environ["USAGE_TRACKING_ENABLED"] = "false"

    from fastapi.testclient import TestClient

    from aviator.main import app

    with TestClient(app) as c:
        c.headers.update({"auth-ticket": "some-valid-ticket"})
        yield c


class TestWebsocketMessageStreaming:
    """Unit tests targeting the 'messages' stream-mode filter in websocket_ws.

    The key conditional under test:
        if msg.content and metadata.get("langgraph_node", "") in
               ["format_answer"]:
    """

    def _run_ws(self, client, events: list, content: str = "hello") -> list[dict]:
        """Spin up the WS endpoint with a mocked graph and return all received frames."""
        mock_graph = MagicMock()
        mock_graph.astream = _make_astream(*events)

        mock_span = _make_mock_span()

        with (
            patch(
                "aviator.api.websockets.aviator.get_graph",
                return_value=mock_graph,
            ),
            patch("aviator.api.websockets.get_auth_handler_ws", return_value=None),
            patch("aviator.api.websockets.extract_document_mentions", return_value=[]),
            patch("aviator.api.websockets._track_usage"),
            patch("aviator.api.websockets.langfuse") as mock_langfuse,
        ):
            mock_langfuse.start_as_current_observation.return_value.__enter__ = MagicMock(return_value=mock_span)
            mock_langfuse.start_as_current_observation.return_value.__exit__ = MagicMock(return_value=False)

            with client.websocket_connect("/v1/chat/stream") as ws:
                return _collect_ws_messages(ws, {"content": content, "context": {}})

    def test_summary_message_is_streamed_from_format_answer(self, client):
        """Summary responses are forwarded from format_answer."""
        ai_msg = _make_ai_msg("Here is the summary.")
        events = [
            ("values", {"messages": [], "references": [], "where": []}),
            ("messages", (ai_msg, {"langgraph_node": "format_answer"})),
        ]

        frames = self._run_ws(client, events, content="summarize")

        streamed_ai = [f for f in frames if f.get("type") == "ai" and f.get("content") == "Here is the summary."]
        assert streamed_ai, "Expected an 'ai' frame from format_answer but got none"

    def test_format_answer_frame_contains_metadata(self, client):
        """The streamed 'ai' frame includes name and metadata fields."""
        ai_msg = _make_ai_msg("Summary text")
        node_meta = {"langgraph_node": "format_answer", "extra": "data"}
        events = [
            ("values", {"messages": [], "references": [], "where": []}),
            ("messages", (ai_msg, node_meta)),
        ]

        frames = self._run_ws(client, events, content="summarize")

        ai_frames = [f for f in frames if f.get("type") == "ai"]
        assert ai_frames, "No ai frame received"
        frame = ai_frames[0]
        assert "name" in frame
        assert "metadata" in frame

    # ------------------------------------------------------------------
    # Existing behaviour: format_answer node still streams
    # ------------------------------------------------------------------

    def test_format_answer_message_is_streamed(self, client):
        """Messages from the format_answer node must still be forwarded (regression guard)."""
        ai_msg = _make_ai_msg("Formatted answer.")
        events = [
            ("values", {"messages": [], "references": [], "where": []}),
            ("messages", (ai_msg, {"langgraph_node": "format_answer"})),
        ]

        frames = self._run_ws(client, events, content="what is this?")

        streamed_ai = [f for f in frames if f.get("type") == "ai" and f.get("content") == "Formatted answer."]
        assert streamed_ai, "Expected an 'ai' frame from format_answer but got none"

    # ------------------------------------------------------------------
    # Negative case: other nodes must NOT stream
    # ------------------------------------------------------------------

    def test_assistant_node_message_is_not_streamed(self, client):
        """Messages from the assistant node must NOT be forwarded to the client."""
        ai_msg = _make_ai_msg("Intermediate reasoning.")
        events = [
            ("values", {"messages": [], "references": [], "where": []}),
            ("messages", (ai_msg, {"langgraph_node": "assistant"})),
        ]

        frames = self._run_ws(client, events)

        leaked = [f for f in frames if f.get("type") == "ai" and f.get("content") == "Intermediate reasoning."]
        assert not leaked, "Assistant-node message should not be forwarded but it was"

    def test_unknown_node_message_is_not_streamed(self, client):
        """Messages from an unrecognised node must not be sent to the client."""
        ai_msg = _make_ai_msg("Private tool content.")
        events = [
            ("values", {"messages": [], "references": [], "where": []}),
            ("messages", (ai_msg, {"langgraph_node": "some_internal_node"})),
        ]

        frames = self._run_ws(client, events)

        leaked = [f for f in frames if f.get("content") == "Private tool content."]
        assert not leaked, "Unknown-node message should not be forwarded but it was"

    def test_format_answer_list_content_is_joined(self, client):
        """List-type msg.content is correctly joined before forwarding."""
        ai_msg = MagicMock()
        ai_msg.content = [{"type": "text", "text": "Part A"}, {"type": "text", "text": " Part B"}]
        ai_msg.type = "ai"
        ai_msg.name = None

        events = [
            ("values", {"messages": [], "references": [], "where": []}),
            ("messages", (ai_msg, {"langgraph_node": "format_answer"})),
        ]

        frames = self._run_ws(client, events, content="summarize")

        ai_frames = [f for f in frames if f.get("type") == "ai"]
        assert ai_frames
        assert ai_frames[0]["content"] == "Part A Part B"

    # ------------------------------------------------------------------
    # Metadata frame is always sent
    # ------------------------------------------------------------------

    def test_metadata_frame_always_present(self, client):
        """A 'metadata' frame with thread_id and references is always sent."""
        events = [
            ("values", {"messages": [], "references": [], "where": []}),
        ]

        frames = self._run_ws(client, events)

        metadata_frames = [f for f in frames if f.get("type") == "metadata"]
        assert metadata_frames, "Expected a 'metadata' frame"
        assert "context" in metadata_frames[0]
        assert "thread_id" in metadata_frames[0]["context"]
        assert "references" in metadata_frames[0]
