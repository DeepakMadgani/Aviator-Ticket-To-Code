"""MCP client router — endpoints for managing remote MCP server configurations.

Handles:
  - Operations for ``/mcp-client/servers`` (server configs per tenant)
  - ``GET /mcp-client/tools``         (list tools loaded from remote servers)
  - ``POST /mcp-client/tools/refresh`` (force tool re-discovery)
  - ``GET /mcp-client/health``        (client-side health check)
"""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Request, Response, Security, status
from fastapi.responses import JSONResponse

from aviator.api.auth import require_authentication
from aviator.mcp import mcp_client_manager
from aviator.mcp.models import MCPServerConfig, MCPServerConfigResponse, MCPToolResponse
from aviator.services.mcp_service import mcp_client_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mcp-client")


@router.post("/servers", status_code=201)
def create_server_config(
    payload: MCPServerConfig,
    user: Annotated[dict[str, Any], Security(require_authentication())],
) -> MCPServerConfigResponse:
    """Create a remote MCP server configuration for the current tenant."""
    return mcp_client_service.create_server_config(payload, user)


@router.get("/servers")
async def list_server_configs(
    user: Annotated[dict[str, Any], Security(require_authentication())],
) -> list[MCPServerConfigResponse]:
    """List all remote MCP server configurations for the current tenant."""
    return mcp_client_service.list_server_configs(user)


@router.get("/servers/{server_id}")
async def get_server_config(
    server_id: str,
    user: Annotated[dict[str, Any], Security(require_authentication())],
) -> MCPServerConfigResponse:
    """Get a single remote MCP server configuration by ID."""
    return mcp_client_service.get_server_config(server_id, user)


@router.put("/servers/{server_id}")
def update_server_config(
    server_id: str,
    payload: MCPServerConfig,
    user: Annotated[dict[str, Any], Security(require_authentication())],
) -> MCPServerConfigResponse:
    """Update a remote MCP server configuration."""
    return mcp_client_service.update_server_config(server_id, payload, user)


@router.delete("/servers/{server_id}")
async def delete_server_config(
    server_id: str,
    request: Request,
    user: Annotated[dict[str, Any], Security(require_authentication())],
) -> Response:
    """Delete a remote MCP server configuration."""
    body = await request.body()
    if body and body.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Request body is not allowed for DELETE")
    result = mcp_client_service.delete_server_config(server_id, user)
    return JSONResponse(status_code=status.HTTP_200_OK, content=result.model_dump())


# ---------------------------------------------------------------------------
# Tool management
# ---------------------------------------------------------------------------


@router.get("/tools")
async def get_mcp_tools(
    request: Request,
    user: Annotated[dict[str, Any], Security(require_authentication())],
) -> list[MCPToolResponse]:
    """List all tools currently loaded from remote MCP servers."""
    try:
        logger.info("get_mcp_tools for tenant: %s", user.get("tenantId", "default"))
        tools = await mcp_client_manager.get_remote_mcp_tools(scope="default", user=user, request=request)
        return [
            MCPToolResponse(
                name=tool.name,
                description=getattr(tool, "description", None),
                server_name=tool.name,
                tool_schema=(
                    getattr(tool, "args_schema", {}).model_json_schema()
                    if hasattr(tool, "args_schema") and hasattr(tool.args_schema, "model_json_schema")
                    else None
                ),
            )
            for tool in tools
        ]
    except Exception as exc:
        logger.error("Failed to get MCP tools: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve MCP tools",
        ) from exc


@router.post("/tools/refresh")
async def refresh_mcp_tools(
    request: Request,
    user: Annotated[dict[str, Any], Security(require_authentication())],
) -> dict[str, str]:
    """Force re-discovery of tools from all configured remote MCP servers."""
    try:
        logger.info("Refreshing MCP tools for tenant: %s", user.get("tenantId", "default"))
        await mcp_client_manager.refresh_tools(user=user)
        tools = await mcp_client_manager.get_remote_mcp_tools(scope="default", user=user, request=request)
        return {"message": f"MCP tools refreshed. {len(tools)} tools available."}
    except Exception as exc:
        logger.error("Failed to refresh MCP tools: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to refresh MCP tools",
        ) from exc


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@router.get("/health")
async def mcp_client_health() -> dict[str, Any]:
    """Health check for the MCP client subsystem."""
    try:
        server_status = await mcp_client_manager.get_server_status()
        total = len(server_status)
        connected = sum(1 for s in server_status.values() if s.get("connected"))
        total_tools = sum(s.get("tool_count", 0) for s in server_status.values())
    except Exception as exc:
        logger.error("MCP client health check failed: %s", exc)
        return {"mcp_enabled": False, "health_status": "unhealthy", "error": str(exc)}
    else:
        return {
            "mcp_enabled": True,
            "total_servers": total,
            "connected_servers": connected,
            "total_tools": total_tools,
            "health_status": "healthy" if connected == total else "partial",
        }
