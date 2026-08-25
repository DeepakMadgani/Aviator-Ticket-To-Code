"""Tests for JSON-RPC error handler helper."""

from fastapi.responses import JSONResponse

from aviator.a2a.handler.error_handler import jsonrpc_error
from aviator.a2a.models import JSONRPC_INVALID_REQUEST, JSONRPC_METHOD_NOT_FOUND


class TestJsonRpcError:
    """Tests for the jsonrpc_error helper."""

    def test_returns_json_response(self):
        response = jsonrpc_error("req-1", JSONRPC_INVALID_REQUEST, "Bad request")
        assert isinstance(response, JSONResponse)

    def test_default_status_code_is_400(self):
        response = jsonrpc_error(None, JSONRPC_INVALID_REQUEST, "Bad request")
        assert response.status_code == 400

    def test_custom_http_status(self):
        response = jsonrpc_error("req-1", JSONRPC_METHOD_NOT_FOUND, "Not found", http_status=404)
        assert response.status_code == 404

    def test_rpc_id_echoed(self):
        import json

        response = jsonrpc_error("my-id", JSONRPC_INVALID_REQUEST, "Error message")
        body = json.loads(response.body)
        assert body["id"] == "my-id"
        assert body["jsonrpc"] == "2.0"

    def test_error_code_and_message(self):
        import json

        response = jsonrpc_error("req-2", JSONRPC_METHOD_NOT_FOUND, "Method not found")
        body = json.loads(response.body)
        assert body["error"]["code"] == JSONRPC_METHOD_NOT_FOUND
        assert body["error"]["message"] == "Method not found"

    def test_none_id(self):
        import json

        response = jsonrpc_error(None, JSONRPC_INVALID_REQUEST, "Error")
        body = json.loads(response.body)
        assert body["id"] is None

    def test_integer_id(self):
        import json

        response = jsonrpc_error(42, JSONRPC_INVALID_REQUEST, "Error")
        body = json.loads(response.body)
        assert body["id"] == 42
