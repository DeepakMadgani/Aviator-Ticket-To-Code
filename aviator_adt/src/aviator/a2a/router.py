"""A2A APIs.

Responsibilities (and *only* these):
  - Declare FastAPI routes and their authentication requirements.
  - Parse / validate the HTTP request envelope.
  - Delegate all business logic to the command registry.
  - Map results or exceptions back to HTTP responses.

No business logic lives here. Adding a new A2A method means registering
a new command in ``commands.py`` — this file never needs to change.

URL layout (A2A specification — https://google.github.io/A2A):
  GET  /.well-known/agent.json   — Agent Card (discovery)
  POST /a2a                      — Unified endpoint: JSON-RPC 2.0 *and*
                                   HTTP+JSON convenience variant.
                                   Requests without a ``jsonrpc`` field are
                                   treated as plain HTTP+JSON ``message/send``
                                   invocations.  Requests with ``jsonrpc: "2.0"``
                                   are dispatched via the JSON-RPC registry.
                                   ``message/stream`` returns SSE (text/event-stream).
"""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Body, Header, Request, Response, Security
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from aviator.a2a.handler import jsonrpc_error, method_registry
from aviator.a2a.models import (
    CONTENT_TYPE_SSE,
    JSONRPC_INTERNAL_ERROR,
    JSONRPC_INVALID_PARAMS,
    JSONRPC_INVALID_REQUEST,
    JSONRPC_METHOD_NOT_FOUND,
    JSONRPC_TASK_NOT_CANCELABLE,
    JSONRPC_TASK_NOT_FOUND,
    MESSAGE_METHODS,
    A2AAgentInvokeRequest,
    A2AMethod,
    AgentCard,
    JsonRpcRequest,
    JsonRpcResponse,
)
from aviator.a2a.util import AgentCardBuilder
from aviator.api.auth import require_authentication
from aviator.exceptions import TaskNotCancelableError, TaskNotFoundError
from aviator.utils.limiter import limiter

logger = logging.getLogger(__name__)


def _first_validation_error_msg(exc: ValidationError) -> str:
    """Return a human-readable message for the first error in a Pydantic ValidationError."""
    first = exc.errors()[0] if exc.errors() else {}
    loc = " → ".join(str(p) for p in first.get("loc", []))
    msg = first.get("msg", str(exc))
    return f"{loc}: {msg}" if loc else msg


router = APIRouter(tags=["Agent2Agent"], prefix="/agent")

# Methods whose handlers return ``StreamingResponse`` instead of a Pydantic model.
_STREAMING_METHODS: frozenset[str] = frozenset({A2AMethod.MESSAGE_STREAM})

# Shared builder instance (stateless — safe to reuse)
_agent_card_builder = AgentCardBuilder()


# ── Agent Card ────────────────────────────────────────────────────────────────


@router.get("/.well-known/agent.json")
@limiter.limit("15/minute")
async def get_agent_card(request: Request) -> AgentCard:
    """Return the A2A Agent Card (discovery document) as per the A2A specification."""
    return await _agent_card_builder.build(request)


# ── Unified A2A endpoint (/a2a) ───────────────────────────────────────────────
#
# POST /a2a handles both wire formats:
#   • HTTP+JSON  — no ``jsonrpc`` field → ``message/send`` (sync Task response)
#                  or ``message/stream`` when ``Accept: text/event-stream``.
#   • JSON-RPC 2.0 — ``jsonrpc: "2.0"`` + ``method`` → dispatched via registry.


@router.post("")
@limiter.limit("15/minute")
async def a2a_endpoint(
    request: Request,
    payload: Annotated[dict[str, Any], Body(...)],
    user: Annotated[dict, Security(require_authentication())],
    _a2a_version: Annotated[str | None, Header(alias="A2A-Version")] = "1.0",
) -> Response:
    """Unified A2A endpoint — JSON-RPC 2.0 and HTTP+JSON variant (POST /a2a)."""
    # ── HTTP+JSON path ────────────────────────────────────────────────────────
    if "jsonrpc" not in payload:
        # Detect likely JSON-RPC request missing the required "jsonrpc" field
        if "method" in payload or ("id" in payload and "params" in payload):
            rpc_id = payload.get("id")
            return jsonrpc_error(
                rpc_id,
                JSONRPC_INVALID_REQUEST,
                "Missing required field: 'jsonrpc'. JSON-RPC requests must include '\"jsonrpc\": \"2.0\"'",
                http_status=400,
            )

        accept = request.headers.get("accept", "")
        method = A2AMethod.MESSAGE_STREAM if CONTENT_TYPE_SSE in accept else A2AMethod.MESSAGE_SEND

        try:
            invoke_request = A2AAgentInvokeRequest.model_validate(payload)
        except ValidationError as exc:
            return JSONResponse(status_code=422, content={"detail": _first_validation_error_msg(exc)})
        except Exception as exc:
            return JSONResponse(status_code=400, content={"detail": str(exc)})

        handler = method_registry.get(method)
        result = await handler(invoke_request.model_dump(mode="json"), request, user)

        # Streaming handler returns StreamingResponse directly.
        if method in _STREAMING_METHODS:
            return result

        return JSONResponse(result.model_dump(mode="json"))

    # ── JSON-RPC 2.0 path ─────────────────────────────────────────────────────
    rpc_id = payload.get("id")

    try:
        rpc_request = JsonRpcRequest.model_validate(payload)
    except ValidationError as exc:
        return jsonrpc_error(rpc_id, JSONRPC_INVALID_REQUEST, _first_validation_error_msg(exc), http_status=422)
    except Exception as exc:
        return jsonrpc_error(rpc_id, JSONRPC_INVALID_REQUEST, str(exc))

    if rpc_request.id is None:
        return jsonrpc_error(None, JSONRPC_INVALID_REQUEST, "id field is required")

    handler = method_registry.get(rpc_request.method)
    if not handler:
        return jsonrpc_error(
            rpc_id,
            JSONRPC_METHOD_NOT_FOUND,
            f"Method '{rpc_request.method}' is not supported",
            http_status=404,
        )

    # ── Validate params for message methods ────────────────────────────────
    if rpc_request.method in MESSAGE_METHODS:
        if not rpc_request.params:
            return jsonrpc_error(rpc_id, JSONRPC_INVALID_PARAMS, "params field is required for message methods")
        try:
            params = A2AAgentInvokeRequest.model_validate(rpc_request.params).model_dump(mode="json")
        except ValidationError as exc:
            return jsonrpc_error(rpc_id, JSONRPC_INVALID_PARAMS, _first_validation_error_msg(exc), http_status=422)
    else:
        # Task management methods (tasks/get, tasks/cancel)
        logger.info(
            "Processing A2A task method '%s' with params: %s",
            rpc_request.method,
            rpc_request.params or {},
        )
        params = rpc_request.params or {}

    try:
        # Streaming methods return an SSE StreamingResponse directly.
        if rpc_request.method in _STREAMING_METHODS:
            if CONTENT_TYPE_SSE not in request.headers.get("accept", ""):
                return jsonrpc_error(
                    rpc_id,
                    JSONRPC_INVALID_REQUEST,
                    f"Streaming requires 'Accept: {CONTENT_TYPE_SSE}' header",
                    http_status=400,
                )
            return await handler(params, request, user)

        result = await handler(params, request, user)
        return JSONResponse(JsonRpcResponse(id=rpc_id, result=result.model_dump()).model_dump())

    except ValueError as exc:
        return jsonrpc_error(rpc_id, JSONRPC_INVALID_PARAMS, str(exc), http_status=400)
    except TaskNotFoundError as exc:
        return jsonrpc_error(rpc_id, JSONRPC_TASK_NOT_FOUND, str(exc), http_status=404)
    except TaskNotCancelableError as exc:
        return jsonrpc_error(rpc_id, JSONRPC_TASK_NOT_CANCELABLE, str(exc), http_status=409)
    except Exception as exc:
        logger.exception("A2A RPC error for method '%s'", rpc_request.method)
        return jsonrpc_error(rpc_id, JSONRPC_INTERNAL_ERROR, str(exc), http_status=500)
