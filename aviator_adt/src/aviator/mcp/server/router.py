"""MCP server router — endpoints for binding tools to tenants.

Handles:
  - ``GET  /mcp-server/list/alltools``    — discover all exposed tools
  - ``POST /mcp-server/register/tools``   — register tools for a tenant
  - ``GET  /mcp-server/list/tools``       — list registered tools
  - ``DELETE /mcp-server/tools``          — remove registered tools
  - ``GET  /mcp-server/health``           — server-side health check
"""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Response, Security, status
from fastapi.responses import JSONResponse

from aviator.api.auth import require_authentication
from aviator.mcp.models import TenantLookupConfigCreateRequest, TenantLookupConfigResponse
from aviator.services.mcp_service import mcp_server_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mcp-server")


# ---------------------------------------------------------------------------
# Tool discovery
# ---------------------------------------------------------------------------


@router.get(
    "/list/alltools",
    responses={
        401: {"description": "Unauthorized"},
        500: {"description": "Internal server error"},
    },
)
async def get_all_discovered_tools(
    user: Annotated[dict[str, Any], Security(require_authentication())],  # noqa: ARG001
) -> list[dict]:
    """Return every tool currently available on the Aviator MCP server."""
    try:
        return mcp_server_service.get_all_discovered_tools()
    except Exception as exc:
        logger.error("Failed to discover tools: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to discover tools",
        ) from exc


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


@router.post(
    "/register/tools",
    responses={
        401: {"description": "Unauthorized"},
        400: {"description": "Bad request"},
        409: {"description": "Conflict — tool already registered"},
        500: {"description": "Internal server error"},
    },
    status_code=status.HTTP_201_CREATED,
)
async def register_tools(
    body: TenantLookupConfigCreateRequest,
    user: Annotated[dict[str, Any], Security(require_authentication())],
) -> TenantLookupConfigResponse:
    """Register allowed MCP tools for the calling tenant/subscription.

    Uses the public schema when the token carries no ``tenantId``.
    """
    result = mcp_server_service.register_tools(body.tools, user)
    return TenantLookupConfigResponse.model_validate(result)


@router.get(
    "/list/tools",
    responses={
        401: {"description": "Unauthorized"},
        400: {"description": "Bad request — token must include both tenantId and subscriptions, or neither"},
        404: {"description": "No registered tools found for this subscription"},
        500: {"description": "Internal server error"},
    },
)
async def list_registered_tools(
    user: Annotated[dict[str, Any], Security(require_authentication())],
) -> list[dict]:
    """List tools registered for the calling tenant/subscription."""
    return mcp_server_service.list_registered_tools(user)


@router.delete(
    "/tools",
    responses={
        401: {"description": "Unauthorized"},
        400: {"description": "Bad request — token must include both tenantId and subscriptions, or neither"},
        204: {"description": "No tools specified — no action taken"},
        404: {"description": "Tool(s) not registered"},
        500: {"description": "Internal server error"},
    },
    status_code=status.HTTP_200_OK,
)
async def delete_registered_tools(
    user: Annotated[dict[str, Any], Security(require_authentication())],
    body: TenantLookupConfigCreateRequest,
) -> Response:
    """Remove specific tools from the registered tool list.

    If the client provides an empty `tools` list, return 204 with an
    `X-Message` header describing the no-op. Returning a body with 204
    can trigger ASGI/server errors and is unreliable across clients.
    If the request omits the body entirely, FastAPI will return a 422
    validation error.
    """
    if not body.tools:
        return Response(
            status_code=status.HTTP_204_NO_CONTENT,
            headers={"X-Message": "No tools specified for deletion; no action taken"},
        )

    result = mcp_server_service.delete_tools(body.tools, user)
    return JSONResponse(status_code=status.HTTP_200_OK, content=result)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@router.get("/health")
async def mcp_server_health() -> dict[str, Any]:
    """Health check for the Aviator MCP server (DB-based)."""
    try:
        tools = mcp_server_service.get_all_discovered_tools()
        return {
            "mcp_enabled": True,
            "total_tools": len(tools),
            "health_status": "healthy",
        }
    except Exception as exc:
        logger.error("MCP server health check failed: %s", exc)
        return {"mcp_enabled": False, "health_status": "unhealthy", "error": str(exc)}
