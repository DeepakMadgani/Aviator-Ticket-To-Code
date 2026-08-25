"""JSON-RPC error response helpers.

Centralises the construction of well-formed JSON-RPC 2.0 error responses
so the router stays free of repetitive boilerplate.
"""

from fastapi.responses import JSONResponse

from aviator.a2a.models import JsonRpcError, JsonRpcResponse


def jsonrpc_error(
    rpc_id: str | int | None,
    code: int,
    message: str,
    *,
    http_status: int = 400,
) -> JSONResponse:
    """Build a JSON-RPC 2.0 error ``JSONResponse``.

    Args:
        rpc_id:      The ``id`` from the original request (may be ``None``).
        code:        JSON-RPC error code (use ``JSONRPC_*`` constants).
        message:     Human-readable error description.
        http_status: HTTP status code for the response (default ``400``).

    Returns:
        A :class:`~fastapi.responses.JSONResponse` ready to return from a route.

    """
    body = JsonRpcResponse(
        id=rpc_id,
        error=JsonRpcError(code=code, message=message),
    ).model_dump()
    return JSONResponse(status_code=http_status, content=body)
