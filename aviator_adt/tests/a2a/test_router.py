"""Tests for the A2A FastAPI router."""

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from aviator.a2a.models import (
    JSONRPC_INVALID_PARAMS,
    JSONRPC_INVALID_REQUEST,
    JSONRPC_METHOD_NOT_FOUND,
    JSONRPC_TASK_NOT_CANCELABLE,
    JSONRPC_TASK_NOT_FOUND,
    Task,
    TaskState,
    TaskStatus,
)
from aviator.exceptions import TaskNotCancelableError, TaskNotFoundError

# `aviator.a2a.__init__` re-exports `router` (FastAPI object) as `aviator.a2a.router`,
# so `import aviator.a2a.router` resolves to the APIRouter rather than the module.
# Use sys.modules to obtain the actual module for patching.
_router_module = sys.modules["aviator.a2a.router"]


@pytest.fixture
def client():
    from aviator.main import app

    headers = {"auth-ticket": "some-valid-ticket"}
    with TestClient(app) as c:
        c.headers.update(headers)
        yield c


# ── Agent Card ────────────────────────────────────────────────────────────────


class TestGetAgentCard:
    """Tests for the GET /.well-known/agent.json endpoint."""

    def test_returns_agent_card(self, client):
        from aviator.a2a.models import AgentCapabilities, AgentCard, SecurityScheme, SupportedInterface

        mock_card = AgentCard(
            protocolVersions=["1.0"],
            name="Content Aviator Agent",
            version="1.0.0",
            description="Test agent",
            supportedInterfaces=[
                SupportedInterface(url="https://example.com/agent", protocolBinding="JSON-RPC", protocolVersion="1.0")
            ],
            capabilities=AgentCapabilities(),
            securitySchemes={"otcsTicket": SecurityScheme(type="apiKey", name="auth-ticket", location="header")},
            security=[{"otcsTicket": []}],
            defaultInputModes=["text/plain"],
            defaultOutputModes=["text/plain"],
            skills=[],
        )
        with patch.object(_router_module, "_agent_card_builder", MagicMock(build=AsyncMock(return_value=mock_card))):
            response = client.get("/agent/.well-known/agent.json")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Content Aviator Agent"

    def test_get_with_body_returns_400(self, client):
        response = client.request("GET", "/agent/.well-known/agent.json", content=b'{"key": "value"}')
        assert response.status_code == 400
        assert "body" in response.json()["detail"].lower()


# ── HTTP+JSON path (no jsonrpc field) ─────────────────────────────────────────


class TestA2AEndpointHttpJson:
    """Tests for the HTTP+JSON (no jsonrpc field) path of POST /agent."""

    _valid_payload = {
        "message": {
            "role": "user",
            "parts": [{"text": "What is X?"}],
            "contextId": "thread-1",
            "metadata": {},
        }
    }

    def _mock_task(self, task_id="task-123", state=TaskState.SUBMITTED):
        return Task(id=task_id, status=TaskStatus(state=state))

    def test_message_send_returns_task(self, client):
        mock_handler = AsyncMock(return_value=self._mock_task())
        with patch.object(_router_module, "method_registry", MagicMock(get=MagicMock(return_value=mock_handler))):
            response = client.post("/agent", json=self._valid_payload)
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "task-123"

    def test_invalid_message_format_returns_400(self, client):
        response = client.post("/agent", json={"message": "not-a-dict"})
        assert response.status_code == 400

    def test_message_stream_uses_streaming_method(self, client):
        from starlette.responses import StreamingResponse

        async def fake_stream(*args, **kwargs):
            return StreamingResponse(iter([b"data: {}\n\n"]), media_type="text/event-stream")

        with patch.object(_router_module, "method_registry", MagicMock(get=MagicMock(return_value=fake_stream))):
            response = client.post(
                "/agent",
                json=self._valid_payload,
                headers={"accept": "text/event-stream"},
            )
        # Either 200 or the streaming response — just confirm no server error
        assert response.status_code < 500


# ── JSON-RPC 2.0 path ─────────────────────────────────────────────────────────


class TestA2AEndpointJsonRpc:
    """Tests for the JSON-RPC 2.0 path of POST /agent."""

    _base_rpc = {
        "jsonrpc": "2.0",
        "id": "req-1",
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"text": "Hello"}],
                "contextId": "ctx-1",
            }
        },
    }

    def _mock_task(self, task_id="task-rpc-1", state=TaskState.SUBMITTED):
        t = Task(id=task_id, status=TaskStatus(state=state))
        return MagicMock(model_dump=MagicMock(return_value=t.model_dump()))

    def test_valid_rpc_message_send(self, client):
        handler = AsyncMock(return_value=self._mock_task())
        with patch.object(_router_module, "method_registry", MagicMock(get=MagicMock(return_value=handler))):
            response = client.post("/agent", json=self._base_rpc)
        assert response.status_code == 200
        data = response.json()
        assert data["jsonrpc"] == "2.0"
        assert data["id"] == "req-1"

    def test_invalid_jsonrpc_version_returns_error(self, client):
        payload = {**self._base_rpc, "jsonrpc": "1.0"}
        response = client.post("/agent", json=payload)
        assert response.status_code == 422
        data = response.json()
        assert data["error"]["code"] == JSONRPC_INVALID_REQUEST

    def test_unknown_method_returns_method_not_found(self, client):
        payload = {**self._base_rpc, "method": "unknown/method"}
        with patch.object(_router_module, "method_registry", MagicMock(get=MagicMock(return_value=None))):
            response = client.post("/agent", json=payload)
        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == JSONRPC_METHOD_NOT_FOUND

    def test_task_not_found_returns_rpc_error(self, client):
        async def raise_not_found(*_args, **_kwargs):
            raise TaskNotFoundError("Task 'x' not found")

        with patch.object(_router_module, "method_registry", MagicMock(get=MagicMock(return_value=raise_not_found))):
            response = client.post("/agent", json={**self._base_rpc, "method": "tasks/get"})
        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == JSONRPC_TASK_NOT_FOUND

    def test_task_not_cancelable_returns_rpc_error(self, client):
        async def raise_not_cancelable(*_args, **_kwargs):
            raise TaskNotCancelableError("Already done")

        with patch.object(
            _router_module, "method_registry", MagicMock(get=MagicMock(return_value=raise_not_cancelable))
        ):
            response = client.post("/agent", json={**self._base_rpc, "method": "tasks/cancel"})
        assert response.status_code == 409
        data = response.json()
        assert data["error"]["code"] == JSONRPC_TASK_NOT_CANCELABLE

    def test_internal_exception_returns_500(self, client):
        async def raise_unexpected(*_args, **_kwargs):
            raise RuntimeError("unexpected")

        with patch.object(_router_module, "method_registry", MagicMock(get=MagicMock(return_value=raise_unexpected))):
            response = client.post("/agent", json=self._base_rpc)
        assert response.status_code == 500
        data = response.json()
        assert data["error"]["code"] == -32603

    def test_missing_id_returns_invalid_request(self, client):
        payload = {k: v for k, v in self._base_rpc.items() if k != "id"}
        response = client.post("/agent", json=payload)
        assert response.status_code == 400
        data = response.json()
        assert data["error"]["code"] == JSONRPC_INVALID_REQUEST
        assert "id" in data["error"]["message"]

    def test_missing_params_returns_invalid_params(self, client):
        payload = {**self._base_rpc}
        del payload["params"]
        handler = AsyncMock()
        with patch.object(_router_module, "method_registry", MagicMock(get=MagicMock(return_value=handler))):
            response = client.post("/agent", json=payload)
        assert response.status_code == 400
        data = response.json()
        assert data["error"]["code"] == JSONRPC_INVALID_PARAMS
        assert "params" in data["error"]["message"]

    def test_missing_message_in_params_returns_invalid_params(self, client):
        payload = {**self._base_rpc, "params": {}}
        handler = AsyncMock()
        with patch.object(_router_module, "method_registry", MagicMock(get=MagicMock(return_value=handler))):
            response = client.post("/agent", json=payload)
        assert response.status_code == 400
        data = response.json()
        assert data["error"]["code"] == JSONRPC_INVALID_PARAMS
        assert "message" in data["error"]["message"].lower()

    def test_missing_role_returns_invalid_params(self, client):
        payload = {**self._base_rpc, "params": {"message": {"parts": [{"text": "hello"}]}}}
        handler = AsyncMock()
        with patch.object(_router_module, "method_registry", MagicMock(get=MagicMock(return_value=handler))):
            response = client.post("/agent", json=payload)
        assert response.status_code == 422
        data = response.json()
        assert data["error"]["code"] == JSONRPC_INVALID_PARAMS
        assert "role" in data["error"]["message"].lower()

    def test_missing_parts_returns_invalid_params(self, client):
        payload = {**self._base_rpc, "params": {"message": {"role": "user"}}}
        handler = AsyncMock()
        with patch.object(_router_module, "method_registry", MagicMock(get=MagicMock(return_value=handler))):
            response = client.post("/agent", json=payload)
        assert response.status_code == 422
        data = response.json()
        assert data["error"]["code"] == JSONRPC_INVALID_PARAMS
        assert "parts" in data["error"]["message"].lower()

    def test_empty_parts_returns_invalid_params(self, client):
        payload = {**self._base_rpc, "params": {"message": {"role": "user", "parts": []}}}
        handler = AsyncMock()
        with patch.object(_router_module, "method_registry", MagicMock(get=MagicMock(return_value=handler))):
            response = client.post("/agent", json=payload)
        assert response.status_code == 422
        data = response.json()
        assert data["error"]["code"] == JSONRPC_INVALID_PARAMS
        assert "parts" in data["error"]["message"].lower()

    def test_missing_text_in_parts_returns_invalid_params(self, client):
        payload = {**self._base_rpc, "params": {"message": {"role": "user", "parts": [{"data": {}}]}}}
        handler = AsyncMock()
        with patch.object(_router_module, "method_registry", MagicMock(get=MagicMock(return_value=handler))):
            response = client.post("/agent", json=payload)
        assert response.status_code == 422
        data = response.json()
        assert data["error"]["code"] == JSONRPC_INVALID_PARAMS

    def test_empty_text_in_parts_returns_invalid_params(self, client):
        payload = {**self._base_rpc, "params": {"message": {"role": "user", "parts": [{"text": "  "}]}}}
        handler = AsyncMock()
        with patch.object(_router_module, "method_registry", MagicMock(get=MagicMock(return_value=handler))):
            response = client.post("/agent", json=payload)
        assert response.status_code == 422
        data = response.json()
        assert data["error"]["code"] == JSONRPC_INVALID_PARAMS


# ── HTTP+JSON validation ──────────────────────────────────────────────────────


class TestA2AEndpointHttpJsonValidation:
    """Tests for field-level validation on the HTTP+JSON (no jsonrpc field) path."""

    def test_missing_message_returns_422(self, client):
        response = client.post("/agent", json={})
        assert response.status_code == 422

    def test_missing_role_returns_422(self, client):
        response = client.post("/agent", json={"message": {"parts": [{"text": "hi"}]}})
        assert response.status_code == 422
        assert "role" in response.json()["detail"].lower()

    def test_missing_parts_returns_422(self, client):
        response = client.post("/agent", json={"message": {"role": "user"}})
        assert response.status_code == 422
        assert "parts" in response.json()["detail"].lower()

    def test_empty_text_returns_422(self, client):
        response = client.post("/agent", json={"message": {"role": "user", "parts": [{"text": "  "}]}})
        assert response.status_code == 422
        assert "text" in response.json()["detail"].lower()
