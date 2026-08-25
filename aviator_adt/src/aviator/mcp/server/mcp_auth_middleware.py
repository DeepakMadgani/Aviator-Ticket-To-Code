"""FastMCP authentication middleware for the Aviator MCP Server.

Validates every incoming MCP request (including initialize) against the
configured auth handler and stores the resolved user in the
_mcp_request_user ContextVar so downstream handlers can access it
without performing a second auth call.
"""

import logging

from fastapi import HTTPException
from fastmcp.server.middleware import Middleware, MiddlewareContext

from aviator.mcp.server.mcp_server_auth import resolve_user_from_request
from aviator.mcp.server.utils import _mcp_request_user

logger = logging.getLogger(__name__)


class AuthMiddleware(Middleware):
    """FastMCP middleware that enforces authentication on every MCP request.

    - Runs before all MCP methods: initialize, tools/list, tools/call, etc.
    - Calls resolve_user_from_request() once per request to validate the token.
    - Stores the resolved user dict in _mcp_request_user ContextVar.
    - Resets the ContextVar after the request to prevent leaking across concurrent requests.
    - Raises HTTP 401 if auth fails or user cannot be resolved.
    """

    async def on_message(self, context: MiddlewareContext, call_next: callable) -> None:
        """Authenticate each MCP request and store user context."""
        try:
            user = resolve_user_from_request()
            logger.info(
                "Auth middleware: authenticated user for tenant_id='%s'",
                user.get("tenantId"),
            )
        except HTTPException as e:
            logger.error("Authentication failed in middleware: %s", e.detail)
            raise HTTPException(status_code=401, detail="Unauthorized") from e
        except Exception as e:
            logger.exception("Failed to resolve user in middleware")
            raise HTTPException(status_code=401, detail="Unauthorized") from e

        # Store user once per request; reset after to avoid leaking across concurrent requests
        token = _mcp_request_user.set(user)
        try:
            return await call_next(context)
        finally:
            _mcp_request_user.reset(token)
