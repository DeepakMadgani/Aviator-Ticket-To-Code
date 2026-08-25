"""MCP Server for Aviator ADT - Exposes existing tools via MCP protocol.

This server automatically discovers and exposes all LangChain tools from aviator.tools and plugin tools
so external clients (SAP, Salesforce, etc.) can call client.get_tools() to discover them.

FastMCP handles tool discovery (tools/list) and invocation (tools/call) automatically.
"""

import logging
import os
from typing import TYPE_CHECKING

from fastapi import HTTPException
from fastmcp import FastMCP

if TYPE_CHECKING:
    from langchain.tools import BaseTool

from aviator.mcp.server.mcp_auth_middleware import AuthMiddleware
from aviator.mcp.server.mcp_server_auth import resolve_user_from_request
from aviator.mcp.server.utils import (
    _mcp_request_user,
    discover_tools,
    get_tools_for_tenant,
    langchain_tool_to_mcp,
)

logger = logging.getLogger(__name__)


class AviatorMCPServer:
    """MCP Server that exposes Aviator tools.

    Discovers tools from aviator.tools and plugins, filters out tools with
    injected params, and exposes them via FastMCP (which auto-handles discovery).
    """

    def __init__(self) -> None:
        """Initialize MCP server with all available tools."""
        self.tools_map: dict[str, BaseTool] = discover_tools()
        logger.info("Aviator MCP Server initialized with %d tools", len(self.tools_map))

    def _create_mcp_app(self) -> FastMCP:
        """Create and configure FastMCP application with tool overrides.

        Returns:
            Configured FastMCP instance with authentication and tenant-aware tool filtering

        """
        # Convert LangChain tools to FastMCP format
        mcp_tools = []
        for tool in self.tools_map.values():
            mcp_tool = langchain_tool_to_mcp(tool)
            if mcp_tool:
                mcp_tools.append(mcp_tool)

        logger.info("Registering %d tools with FastMCP", len(mcp_tools))

        mcp = FastMCP(name="Aviator MCP Server", tools=mcp_tools)

        # Store original methods before overriding
        original_list_tools = mcp._list_tools  # noqa: SLF001
        original_call_tool = mcp.call_tool

        # Add authentication middleware that runs on ALL MCP requests (including initialize)
        mcp.add_middleware(AuthMiddleware())

        # Override tool discovery to support tenant-based filtering
        async def _list_tools() -> list:
            """List tools available for the current tenant, filtered by permissions."""
            # User set by auth middleware; None means middleware failed or was bypassed
            user = _mcp_request_user.get()
            if user is None:
                logger.error("_list_tools: no authenticated user in context — rejecting request")
                raise HTTPException(status_code=401, detail="Unauthorized")
            tenant_id = user.get("tenantId")
            subscription_id = user.get("subscriptions", [None])[0]

            # Get allowed tools for this tenant
            try:
                allowed = get_tools_for_tenant(tenant_id, subscription_id)
                logger.info("_list_tools: tenant_id='%s', calling original list_tools", tenant_id)
                all_mcp_tools = await original_list_tools()
            except HTTPException:
                raise
            except Exception as e:
                logger.exception("_list_tools failed for tenant '%s'", tenant_id)
                raise HTTPException(status_code=500, detail="Failed to retrieve tool list") from e

            # Filter based on tenant permissions
            # allowed=None means no config found in DB → return empty list
            filtered = [] if allowed is None else [t for t in all_mcp_tools if t.name in allowed]

            logger.info(
                "_list_tools: tenant_id='%s' tools=%d/%d",
                tenant_id,
                len(filtered),
                len(all_mcp_tools),
            )
            return filtered

        # Override tool invocation to inject user context and enforce permissions
        async def call_tool(name: str, arguments: dict | None = None, **kwargs: object) -> object:
            """Call a tool by name for the current tenant, enforcing permissions."""
            # User is set by AuthMiddleware for list/initialize, but call_tool overrides
            # FastMCP's call_tool entirely, bypassing _run_middleware. Resolve directly as fallback.
            user = _mcp_request_user.get()
            if user is None:
                try:
                    user = resolve_user_from_request()
                except HTTPException:
                    raise
                except Exception as e:
                    logger.exception("Failed to resolve user in call_tool")
                    raise HTTPException(status_code=401, detail="Unauthorized") from e
                _mcp_request_user.set(user)
            tenant_id = user.get("tenantId")
            subscription_id = user.get("subscriptions", [None])[0]

            # Check tenant permissions
            # allowed=None means no config found in DB → deny all tools
            allowed = get_tools_for_tenant(tenant_id, subscription_id)
            if allowed is None or name not in allowed:
                logger.warning("Access denied to tool '%s' for tenant '%s'", name, tenant_id)
                raise HTTPException(
                    status_code=403,
                    detail=f"Tool '{name}' is not permitted for tenant '{tenant_id}'",
                )

            logger.info("call_tool: tool='%s' tenant_id='%s'", name, tenant_id)

            # _mcp_request_user is already set by middleware; tools can call get_mcp_user() directly
            # Pass run_middleware=False so original_call_tool goes straight to core logic and does
            # not re-enter _run_middleware (which would trigger AuthMiddleware.on_message a second time).
            kwargs.pop("run_middleware", None)
            try:
                return await original_call_tool(name, arguments, run_middleware=False, **kwargs)
            except HTTPException:
                raise
            except Exception as e:
                logger.exception("call_tool failed: tool='%s' tenant_id='%s'", name, tenant_id)
                raise HTTPException(status_code=500, detail=f"Tool '{name}' execution failed") from e

        # Apply overrides
        mcp._list_tools = _list_tools  # noqa: SLF001
        mcp.call_tool = call_tool

        return mcp

    def run(self, host: str = "0.0.0.0", port: int = 8100, path: str = "/") -> None:  # noqa: S104
        """Run MCP server with HTTP transport.

        Args:
            host: Host to bind to (default: 0.0.0.0)
            port: Port to bind to (default: 8100, configurable via MCP_PORT env var)
            path: HTTP endpoint path (default: /)

        """
        # Allow port override via environment variable
        port = int(os.getenv("MCP_PORT", port))

        # Allow path override via environment variable
        path = str(os.getenv("MCP_PATH", path))

        logger.info("Starting Aviator MCP Server on HTTP transport: %s:%d%s", host, port, path)

        mcp = self._create_mcp_app()
        mcp.run(host=host, port=port, transport="http", path=path)


def main() -> None:
    """Entry point for MCP server."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    server = AviatorMCPServer()
    server.run()


if __name__ == "__main__":
    main()
