"""MCP management API — aggregates client and server routers.

All endpoint logic lives in:
  - ``aviator.mcp.client.router``  — /mcp-client/* (remote MCP server config + tool loading)
  - ``aviator.mcp.server.router``  — /mcp-server/* (tool registration / lookup config)
"""

from fastapi import APIRouter

from aviator.mcp.client.router import router as _client_router
from aviator.mcp.server.router import router as _server_router

router = APIRouter(tags=["MCP Management"])

router.include_router(_client_router)
router.include_router(_server_router)
