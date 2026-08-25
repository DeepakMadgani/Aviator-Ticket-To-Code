"""Tests for the WebSocket-to-SSE relay (ws_streaming.py)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import websockets.exceptions
from starlette.responses import StreamingResponse

from aviator.a2a.handler.ws_streaming import _sse, ws_to_a2a_sse_stream

# ── _sse helper ───────────────────────────────────────────────────────────────


class TestSseHelper:
    """Tests for the _sse SSE frame formatter."""

    def test_formats_sse_frame(self):
        frame = _sse("task", {"id": "t1"})
        assert frame.startswith("event: task\n")
        assert '"id": "t1"' in frame
        assert frame.endswith("\n\n")

    def test_data_is_json_encoded(self):
        frame = _sse("status-update", {"final": True})
        lines = frame.strip().split("\n")
        data_line = next(line for line in lines if line.startswith("data:"))
        payload = json.loads(data_line[len("data: ") :])
        assert payload["final"] is True


# ── ws_to_a2a_sse_stream ──────────────────────────────────────────────────────


async def _collect_stream(response: StreamingResponse) -> list[dict]:
    """Drain a StreamingResponse and parse each SSE frame into (event, data) dicts."""
    frames = []
    async for chunk in response.body_iterator:
        text = chunk if isinstance(chunk, str) else chunk.decode()
        for block in text.split("\n\n"):
            block = block.strip()
            if not block:
                continue
            event = ""
            data = {}
            for line in block.split("\n"):
                if line.startswith("event:"):
                    event = line[len("event:") :].strip()
                elif line.startswith("data:"):
                    data = json.loads(line[len("data:") :].strip())
            if event:
                frames.append({"event": event, "data": data})
    return frames


def _make_ws(messages: list[str], close_ok: bool = True):
    """Build a mock websocket context manager yielding *messages*."""

    async def _aiter(self):
        for m in messages:
            yield m
        if not close_ok:
            raise websockets.exceptions.ConnectionClosedOK(None, None)

    ws = MagicMock()
    ws.send = AsyncMock()
    ws.__aiter__ = _aiter
    ws.__aenter__ = AsyncMock(return_value=ws)
    ws.__aexit__ = AsyncMock(return_value=False)
    return ws


class TestWsToA2aSseStream:
    """Tests for ws_to_a2a_sse_stream()."""

    @pytest.mark.asyncio
    async def test_returns_streaming_response(self):
        ws = _make_ws([])

        with (
            patch("aviator.a2a.handler.ws_streaming.task_service"),
            patch("aviator.a2a.handler.ws_streaming.websockets.connect", return_value=ws),
        ):
            result = await ws_to_a2a_sse_stream("t1", "ctx1", None, "ws://localhost/v1/chat/stream", {})

        assert isinstance(result, StreamingResponse)
        assert result.media_type == "text/event-stream"

    @pytest.mark.asyncio
    async def test_emits_submitted_and_working_events(self):
        ws = _make_ws([])

        with (
            patch("aviator.a2a.handler.ws_streaming.task_service"),
            patch("aviator.a2a.handler.ws_streaming.websockets.connect", return_value=ws),
        ):
            response = await ws_to_a2a_sse_stream("t1", "ctx1", None, "ws://localhost/v1/chat/stream", {})
            frames = await _collect_stream(response)

        events = [f["event"] for f in frames]
        assert "task" in events
        assert "status-update" in events

    @pytest.mark.asyncio
    async def test_token_chunk_emits_artifact_update(self):
        msg = json.dumps({"type": "token", "content": "Hello"})
        ws = _make_ws([msg])

        with (
            patch("aviator.a2a.handler.ws_streaming.task_service"),
            patch("aviator.a2a.handler.ws_streaming.websockets.connect", return_value=ws),
        ):
            response = await ws_to_a2a_sse_stream("t1", "ctx1", None, "ws://localhost/v1/chat/stream", {})
            frames = await _collect_stream(response)

        artifact_frames = [f for f in frames if f["event"] == "artifact-update"]
        assert len(artifact_frames) >= 1

    @pytest.mark.asyncio
    async def test_ai_message_captured_as_final_answer(self):
        msg = json.dumps({"type": "ai", "content": "Full answer", "answer": "Full answer"})
        ws = _make_ws([msg])

        states_persisted = []

        def capture(task_id, schema, state, **kwargs):
            states_persisted.append((state, kwargs.get("artifacts")))

        with (
            patch("aviator.a2a.handler.ws_streaming.task_service") as mock_ts,
            patch("aviator.a2a.handler.ws_streaming.websockets.connect", return_value=ws),
        ):
            mock_ts.update_state.side_effect = capture
            response = await ws_to_a2a_sse_stream("t1", "ctx1", None, "ws://localhost/v1/chat/stream", {})
            await _collect_stream(response)

        # Final persisted state should be "completed" with the artifact
        final = next((s for s in states_persisted if s[0] == "completed"), None)
        assert final is not None
        assert final[1] is not None
        assert final[1]["result"] == "Full answer"

    @pytest.mark.asyncio
    async def test_done_message_type_captured_as_final_answer(self):
        msg = json.dumps({"type": "done", "answer": "Done answer"})
        ws = _make_ws([msg])

        states_persisted = []

        def capture(task_id, schema, state, **kwargs):
            states_persisted.append((state, kwargs.get("artifacts")))

        with (
            patch("aviator.a2a.handler.ws_streaming.task_service") as mock_ts,
            patch("aviator.a2a.handler.ws_streaming.websockets.connect", return_value=ws),
        ):
            mock_ts.update_state.side_effect = capture
            response = await ws_to_a2a_sse_stream("t1", "ctx1", None, "ws://localhost/v1/chat/stream", {})
            await _collect_stream(response)

        final = next((s for s in states_persisted if s[0] == "completed"), None)
        assert final is not None
        assert final[1]["result"] == "Done answer"

    @pytest.mark.asyncio
    async def test_housekeeping_frames_skipped(self):
        """feedback/final/metadata frames produce no artifact-update events."""
        messages = [
            json.dumps({"type": "feedback", "content": "ignored"}),
            json.dumps({"type": "final", "content": "ignored"}),
            json.dumps({"type": "metadata", "content": "ignored"}),
        ]
        ws = _make_ws(messages)

        with (
            patch("aviator.a2a.handler.ws_streaming.task_service"),
            patch("aviator.a2a.handler.ws_streaming.websockets.connect", return_value=ws),
        ):
            response = await ws_to_a2a_sse_stream("t1", "ctx1", None, "ws://localhost/v1/chat/stream", {})
            frames = await _collect_stream(response)

        artifact_frames = [f for f in frames if f["event"] == "artifact-update"]
        assert artifact_frames == []

    @pytest.mark.asyncio
    async def test_progress_event_emits_status_update_with_label(self):
        """A message with a label but no content emits a status-update with progressLabel."""
        msg = json.dumps({"type": "routing", "label": "Searching documents"})
        ws = _make_ws([msg])

        with (
            patch("aviator.a2a.handler.ws_streaming.task_service"),
            patch("aviator.a2a.handler.ws_streaming.websockets.connect", return_value=ws),
        ):
            response = await ws_to_a2a_sse_stream("t1", "ctx1", None, "ws://localhost/v1/chat/stream", {})
            frames = await _collect_stream(response)

        status_frames = [f for f in frames if f["event"] == "status-update"]
        progress_frames = [f for f in status_frames if f["data"].get("statusUpdate", {}).get("progressLabel")]
        assert len(progress_frames) >= 1
        assert progress_frames[0]["data"]["statusUpdate"]["progressLabel"] == "Searching documents"

    @pytest.mark.asyncio
    async def test_invalid_json_message_skipped(self):
        """Non-JSON frames are silently ignored."""
        messages = ["not-json", json.dumps({"type": "token", "content": "valid"})]
        ws = _make_ws(messages)

        with (
            patch("aviator.a2a.handler.ws_streaming.task_service"),
            patch("aviator.a2a.handler.ws_streaming.websockets.connect", return_value=ws),
        ):
            response = await ws_to_a2a_sse_stream("t1", "ctx1", None, "ws://localhost/v1/chat/stream", {})
            frames = await _collect_stream(response)

        artifact_frames = [f for f in frames if f["event"] == "artifact-update"]
        assert len(artifact_frames) == 1  # only the valid message

    @pytest.mark.asyncio
    async def test_connection_closed_ok_handled_gracefully(self):
        """ConnectionClosedOK during iteration completes the stream as completed."""

        async def _aiter_close_ok(self):
            raise websockets.exceptions.ConnectionClosedOK(None, None)
            yield  # make it a generator

        ws = MagicMock()
        ws.send = AsyncMock()
        ws.__aiter__ = _aiter_close_ok
        ws.__aenter__ = AsyncMock(return_value=ws)
        ws.__aexit__ = AsyncMock(return_value=False)

        states = []

        with (
            patch("aviator.a2a.handler.ws_streaming.task_service") as mock_ts,
            patch("aviator.a2a.handler.ws_streaming.websockets.connect", return_value=ws),
        ):
            mock_ts.update_state.side_effect = lambda tid, schema, state, **kw: states.append(state)
            response = await ws_to_a2a_sse_stream("t1", "ctx1", None, "ws://localhost/v1/chat/stream", {})
            await _collect_stream(response)

        assert "completed" in states

    @pytest.mark.asyncio
    async def test_websocket_connection_error_emits_failed_event(self):
        """Exception connecting to /v1/chat/stream results in a failed status-update SSE event."""

        def _raise(*_args, **_kwargs):
            raise OSError("Connection refused")

        with (
            patch("aviator.a2a.handler.ws_streaming.task_service") as mock_ts,
            patch("aviator.a2a.handler.ws_streaming.websockets.connect", side_effect=_raise),
        ):
            response = await ws_to_a2a_sse_stream("t1", "ctx1", None, "ws://localhost/v1/chat/stream", {})
            frames = await _collect_stream(response)

        failed_frames = [
            f
            for f in frames
            if f["event"] == "status-update"
            and f["data"].get("statusUpdate", {}).get("status", {}).get("state") == "failed"
        ]
        assert len(failed_frames) >= 1
        mock_ts.update_state.assert_any_call("t1", None, "failed")

    @pytest.mark.asyncio
    async def test_final_status_update_has_final_true(self):
        """The last SSE status-update must have final=True."""
        ws = _make_ws([])

        with (
            patch("aviator.a2a.handler.ws_streaming.task_service"),
            patch("aviator.a2a.handler.ws_streaming.websockets.connect", return_value=ws),
        ):
            response = await ws_to_a2a_sse_stream("t1", "ctx1", None, "ws://localhost/v1/chat/stream", {})
            frames = await _collect_stream(response)

        status_frames = [f for f in frames if f["event"] == "status-update"]
        last_status = status_frames[-1]
        assert last_status["data"]["statusUpdate"]["final"] is True

    @pytest.mark.asyncio
    async def test_no_messages_emits_completed_with_no_artifact(self):
        """Empty stream persists completed state with no artifacts."""
        ws = _make_ws([])

        captured_artifacts = []

        def capture(task_id, schema, state, **kwargs):
            if state == "completed":
                captured_artifacts.append(kwargs.get("artifacts"))

        with (
            patch("aviator.a2a.handler.ws_streaming.task_service") as mock_ts,
            patch("aviator.a2a.handler.ws_streaming.websockets.connect", return_value=ws),
        ):
            mock_ts.update_state.side_effect = capture
            response = await ws_to_a2a_sse_stream("t1", "ctx1", None, "ws://localhost/v1/chat/stream", {})
            await _collect_stream(response)

        assert captured_artifacts == [None]

    @pytest.mark.asyncio
    async def test_headers_forwarded_to_websocket(self):
        """Custom headers are passed through to the websockets.connect call."""
        ws = _make_ws([])
        connect_kwargs: dict = {}

        def capture_connect(url, **kwargs):
            connect_kwargs.update(kwargs)
            return ws

        with (
            patch("aviator.a2a.handler.ws_streaming.task_service"),
            patch("aviator.a2a.handler.ws_streaming.websockets.connect", side_effect=capture_connect),
        ):
            response = await ws_to_a2a_sse_stream(
                "t1", "ctx1", None, "ws://localhost/v1/chat/stream", {}, headers={"auth-ticket": "my-ticket"}
            )
            await _collect_stream(response)

        assert connect_kwargs.get("additional_headers") == {"auth-ticket": "my-ticket"}

    @pytest.mark.asyncio
    async def test_text_field_used_as_content(self):
        """Plugin-style messages with 'text' field are treated as content."""
        msg = json.dumps({"event": "chunk", "text": "Plugin token"})
        ws = _make_ws([msg])

        with (
            patch("aviator.a2a.handler.ws_streaming.task_service"),
            patch("aviator.a2a.handler.ws_streaming.websockets.connect", return_value=ws),
        ):
            response = await ws_to_a2a_sse_stream("t1", "ctx1", None, "ws://localhost/v1/chat/stream", {})
            frames = await _collect_stream(response)

        artifact_frames = [f for f in frames if f["event"] == "artifact-update"]
        assert len(artifact_frames) == 1
