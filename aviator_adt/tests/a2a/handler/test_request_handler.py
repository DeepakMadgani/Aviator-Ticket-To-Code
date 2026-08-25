"""Tests for A2AChatHandler."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aviator.a2a.handler.request_handler import A2AChatHandler
from aviator.a2a.models import (
    A2AAgentInvokeResponse,
    Task,
    TaskState,
)


def _make_request(
    headers=None,
    cookies=None,
    url="http://localhost:8000/agent",
):
    """Build a minimal mock FastAPI Request."""
    req = MagicMock()
    req.headers = headers or {"auth-ticket": "ticket"}
    req.cookies = cookies or {}
    req.url = MagicMock()
    req.url.scheme = "http"
    req.url.netloc = "localhost:8000"
    return req


def _user(tenant_id="tenant1"):
    return {"tenantId": tenant_id}


# ── _map_response ─────────────────────────────────────────────────────────────


class TestMapResponse:
    """Tests for A2AChatHandler._map_response()."""

    def test_maps_all_fields(self):
        handler = A2AChatHandler()
        raw = {
            "result": "Answer here",
            "context": '{"thread_id": "t1"}',
            "where": [],
            "references": [{"documentID": "doc-1"}],
        }
        resp = handler._map_response(raw)
        assert isinstance(resp, A2AAgentInvokeResponse)
        assert resp.result == "Answer here"
        assert resp.context == '{"thread_id": "t1"}'
        assert resp.references == [{"documentID": "doc-1"}]

    def test_missing_fields_use_defaults(self):
        handler = A2AChatHandler()
        resp = handler._map_response({})
        assert resp.result is None
        assert resp.references == []


# ── _call_v1_chat with injected client ───────────────────────────────────────


class TestCallV1Chat:
    """Tests for A2AChatHandler._call_v1_chat()."""

    @pytest.mark.asyncio
    async def test_success_returns_json(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"result": "ok", "references": []}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        handler = A2AChatHandler(client=mock_client)
        result = await handler._call_v1_chat("http://localhost", {"messages": []}, {}, {})

        assert result == {"result": "ok", "references": []}

    @pytest.mark.asyncio
    async def test_http_error_raises_http_exception(self):
        from fastapi import HTTPException

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        handler = A2AChatHandler(client=mock_client)
        with pytest.raises(HTTPException) as exc_info:
            await handler._call_v1_chat("http://localhost", {}, {}, {})

        assert exc_info.value.status_code == 500


# ── handle_message_send ───────────────────────────────────────────────────────


class TestHandleMessageSend:
    """Tests for A2AChatHandler.handle_message_send()."""

    @pytest.mark.asyncio
    async def test_returns_task_immediately(self, monkeypatch):
        handler = A2AChatHandler()

        payload = {
            "context": json.dumps({"thread_id": "t-1"}),
            "messages": [{"author": "user", "content": "hello"}],
        }
        request = _make_request()
        user = _user()

        with (
            patch("aviator.a2a.handler.request_handler.task_service") as mock_ts,
            patch("aviator.a2a.handler.request_handler.start_task") as mock_start,
        ):
            mock_ts.create.return_value = SimpleNamespace(id="task-x", context_id="t-1", state="submitted")
            mock_start.return_value = MagicMock()

            task = await handler.handle_message_send(payload, request, user)

        assert isinstance(task, Task)
        assert task.status.state == TaskState.SUBMITTED
        mock_start.assert_called_once()

    @pytest.mark.asyncio
    async def test_task_id_is_unique_each_call(self):
        handler = A2AChatHandler()
        payload = {"context": json.dumps({"thread_id": "t-2"}), "messages": []}
        request = _make_request()
        user = _user()

        task_ids = []

        with (
            patch("aviator.a2a.handler.request_handler.task_service") as mock_ts,
            patch("aviator.a2a.handler.request_handler.start_task") as mock_start,
        ):
            mock_ts.create.return_value = SimpleNamespace(id="id", context_id="t-2", state="submitted")
            mock_start.return_value = MagicMock()

            for _ in range(3):
                task = await handler.handle_message_send(payload, request, user)
                task_ids.append(task.id)

        # All task IDs should be different UUIDs
        assert len(set(task_ids)) == 3


# ── _create_and_persist_task ─────────────────────────────────────────────────


class TestCreateAndPersistTask:
    """Tests for A2AChatHandler._create_and_persist_task()."""

    def test_extracts_context_id_from_payload(self):
        handler = A2AChatHandler()
        payload = {"context": json.dumps({"thread_id": "thread-abc"}), "messages": []}
        user = {"tenantId": "t1"}

        with patch("aviator.a2a.handler.request_handler.task_service") as mock_ts:
            mock_ts.create.return_value = SimpleNamespace(id="x", context_id="thread-abc", state="submitted")
            _task_id, schema_name, context_id = handler._create_and_persist_task(payload, user)

        assert context_id == "thread-abc"
        assert schema_name is not None  # tenant-prefixed or default

    def test_none_context_sets_none_context_id(self):
        handler = A2AChatHandler()
        payload = {"messages": []}
        user = {"tenantId": None}

        with patch("aviator.a2a.handler.request_handler.task_service") as mock_ts:
            mock_ts.create.return_value = SimpleNamespace(id="x", context_id=None, state="submitted")
            _, _, context_id = handler._create_and_persist_task(payload, user)

        assert context_id is None


# ── handle_message_stream ─────────────────────────────────────────────────────


class TestHandleMessageStream:
    """Tests for A2AChatHandler.handle_message_stream()."""

    @pytest.mark.asyncio
    async def test_returns_streaming_response(self):
        from starlette.responses import StreamingResponse

        handler = A2AChatHandler()
        payload = {
            "context": json.dumps({"thread_id": "t-stream-1"}),
            "messages": [{"author": "user", "content": "stream me"}],
            "where": [],
        }
        request = _make_request()
        user = _user()

        mock_streaming = MagicMock(spec=StreamingResponse)

        with (
            patch("aviator.a2a.handler.request_handler.task_service") as mock_ts,
            patch(
                "aviator.a2a.handler.request_handler.ws_to_a2a_sse_stream", new=AsyncMock(return_value=mock_streaming)
            ),
            patch("aviator.a2a.handler.request_handler.build_base_url", return_value="http://localhost:8000"),
            patch("aviator.a2a.handler.request_handler.build_forward_headers", return_value={"auth-ticket": "t"}),
        ):
            mock_ts.create.return_value = SimpleNamespace(id="stream-task", context_id="t-stream-1", state="submitted")
            result = await handler.handle_message_stream(payload, request, user)

        assert result is mock_streaming

    @pytest.mark.asyncio
    async def test_ws_url_uses_ws_scheme_for_http(self):
        """Http base URL is converted to ws:// for the WebSocket connection."""
        from starlette.responses import StreamingResponse

        handler = A2AChatHandler()
        payload = {
            "context": json.dumps({"thread_id": "t-ws"}),
            "messages": [{"author": "user", "content": "hello"}],
        }
        request = _make_request()
        user = _user()

        captured: dict = {}

        async def capture_ws_url(**kwargs):
            captured.update(kwargs)
            return MagicMock(spec=StreamingResponse)

        with (
            patch("aviator.a2a.handler.request_handler.task_service") as mock_ts,
            patch("aviator.a2a.handler.request_handler.ws_to_a2a_sse_stream", side_effect=capture_ws_url),
            patch("aviator.a2a.handler.request_handler.build_base_url", return_value="http://localhost:8000"),
            patch("aviator.a2a.handler.request_handler.build_forward_headers", return_value={}),
        ):
            mock_ts.create.return_value = SimpleNamespace(id="t", context_id="t-ws", state="submitted")
            await handler.handle_message_stream(payload, request, user)

        assert captured["ws_url"] == "ws://localhost:8000/v1/chat/stream"

    @pytest.mark.asyncio
    async def test_ws_url_uses_wss_scheme_for_https(self):
        """Https base URL is converted to wss:// for the WebSocket connection."""
        from starlette.responses import StreamingResponse

        handler = A2AChatHandler()
        payload = {
            "context": json.dumps({"thread_id": "t-wss"}),
            "messages": [{"author": "user", "content": "hello"}],
        }
        request = _make_request()
        user = _user()

        captured: dict = {}

        async def capture_ws_url(**kwargs):
            captured.update(kwargs)
            return MagicMock(spec=StreamingResponse)

        with (
            patch("aviator.a2a.handler.request_handler.task_service") as mock_ts,
            patch("aviator.a2a.handler.request_handler.ws_to_a2a_sse_stream", side_effect=capture_ws_url),
            patch("aviator.a2a.handler.request_handler.build_base_url", return_value="https://agent.example.com"),
            patch("aviator.a2a.handler.request_handler.build_forward_headers", return_value={}),
        ):
            mock_ts.create.return_value = SimpleNamespace(id="t", context_id="t-wss", state="submitted")
            await handler.handle_message_stream(payload, request, user)

        assert captured["ws_url"] == "wss://agent.example.com/v1/chat/stream"

    @pytest.mark.asyncio
    async def test_string_context_parsed_to_dict(self):
        """String context is JSON-decoded before being forwarded to /v1/chat/stream."""
        from starlette.responses import StreamingResponse

        handler = A2AChatHandler()
        payload = {
            "context": json.dumps({"thread_id": "t-ctx"}),
            "messages": [{"author": "user", "content": "hi"}],
        }
        request = _make_request()
        user = _user()

        captured: dict = {}

        async def capture(**kwargs):
            captured.update(kwargs)
            return MagicMock(spec=StreamingResponse)

        with (
            patch("aviator.a2a.handler.request_handler.task_service") as mock_ts,
            patch("aviator.a2a.handler.request_handler.ws_to_a2a_sse_stream", side_effect=capture),
            patch("aviator.a2a.handler.request_handler.build_base_url", return_value="http://localhost"),
            patch("aviator.a2a.handler.request_handler.build_forward_headers", return_value={}),
        ):
            mock_ts.create.return_value = SimpleNamespace(id="t", context_id="t-ctx", state="submitted")
            await handler.handle_message_stream(payload, request, user)

        # ws_payload["context"] must be a dict, not the raw JSON string
        assert isinstance(captured["ws_payload"]["context"], dict)
        assert captured["ws_payload"]["context"]["thread_id"] == "t-ctx"

    @pytest.mark.asyncio
    async def test_invalid_string_context_falls_back_to_empty_dict(self):
        """Malformed JSON context is silently replaced with an empty dict."""
        from starlette.responses import StreamingResponse

        handler = A2AChatHandler()
        payload = {
            "context": "not-valid-json",
            "messages": [{"author": "user", "content": "hi"}],
        }
        request = _make_request()
        user = _user()

        captured: dict = {}

        async def capture(**kwargs):
            captured.update(kwargs)
            return MagicMock(spec=StreamingResponse)

        with (
            patch.object(
                A2AChatHandler,
                "_create_and_persist_task",
                return_value=("task-x", "public", None),
            ),
            patch("aviator.a2a.handler.request_handler.ws_to_a2a_sse_stream", side_effect=capture),
            patch("aviator.a2a.handler.request_handler.build_base_url", return_value="http://localhost"),
            patch("aviator.a2a.handler.request_handler.build_forward_headers", return_value={}),
        ):
            await handler.handle_message_stream(payload, request, user)

        assert captured["ws_payload"]["context"] == {}


# ── process ───────────────────────────────────────────────────────────────────


class TestProcess:
    """Tests for A2AChatHandler.process() — end-to-end internal chat call."""

    @pytest.mark.asyncio
    async def test_process_returns_mapped_response(self):
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "result": "The answer",
            "context": '{"thread_id": "t1"}',
            "where": [],
            "references": [],
        }
        mock_client.post = AsyncMock(return_value=mock_response)

        handler = A2AChatHandler(client=mock_client)
        request = _make_request()

        with (
            patch("aviator.a2a.handler.request_handler.build_base_url", return_value="http://localhost"),
            patch("aviator.a2a.handler.request_handler.build_forward_headers", return_value={}),
        ):
            result = await handler.process({"messages": [], "context": "{}"}, request, {})

        assert isinstance(result, A2AAgentInvokeResponse)
        assert result.result == "The answer"

    @pytest.mark.asyncio
    async def test_process_raises_on_chat_error(self):
        from fastapi import HTTPException

        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.text = "Service Unavailable"
        mock_client.post = AsyncMock(return_value=mock_response)

        handler = A2AChatHandler(client=mock_client)
        request = _make_request()

        with (
            patch("aviator.a2a.handler.request_handler.build_base_url", return_value="http://localhost"),
            patch("aviator.a2a.handler.request_handler.build_forward_headers", return_value={}),
            pytest.raises(HTTPException) as exc_info,
        ):
            await handler.process({"messages": []}, request, {})

        assert exc_info.value.status_code == 503


# ── _call_v1_chat without injected client ─────────────────────────────────────


class TestCallV1ChatNoClient:
    """Tests for _call_v1_chat using the default httpx.AsyncClient (no injection)."""

    @pytest.mark.asyncio
    async def test_uses_default_client_on_success(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"result": "ok"}

        mock_client_instance = AsyncMock()
        mock_client_instance.post = AsyncMock(return_value=mock_response)
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)

        handler = A2AChatHandler()  # no injected client

        with patch("aviator.a2a.handler.request_handler.httpx.AsyncClient", return_value=mock_client_instance):
            result = await handler._call_v1_chat("http://localhost", {}, {}, {})

        assert result == {"result": "ok"}

    @pytest.mark.asyncio
    async def test_raises_http_exception_on_error_status(self):
        from fastapi import HTTPException

        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = "Not Found"

        mock_client_instance = AsyncMock()
        mock_client_instance.post = AsyncMock(return_value=mock_response)
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)

        handler = A2AChatHandler()

        with (
            patch("aviator.a2a.handler.request_handler.httpx.AsyncClient", return_value=mock_client_instance),
            pytest.raises(HTTPException) as exc_info,
        ):
            await handler._call_v1_chat("http://localhost", {}, {}, {})

        assert exc_info.value.status_code == 404
