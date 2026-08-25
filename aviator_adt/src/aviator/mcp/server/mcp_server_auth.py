"""Authentication utilities for MCP Server.

Handles user authentication and context extraction for MCP server requests.
Uses the same require_authentication contract as API endpoints.
"""

import logging

from fastapi import HTTPException
from fastmcp.server.dependencies import get_http_request

from aviator.api.auth import require_authentication

logger = logging.getLogger(__name__)


def resolve_user_from_request() -> dict:
    """Authenticate request and extract user context.

    Uses require_authentication() which is implemented by content system plugins.
    This follows the same contract as API endpoints in v1.py.

    Extracts token from either:
    - Authorization header (Bearer token)
    - otcsticket header (OTDS specific)

    Returns:
        User context dict with tenantId, userId, etc.
        Empty dict for anonymous/failed authentication

    """
    request = get_http_request()
    # Extract token from Authorization header
    auth_header = request.headers.get("authorization", "")

    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:]
        is_bearer = True
    else:
        # Fallback: Check for OTDS ticket in custom header
        token = request.headers.get("otcsticket") or None
        is_bearer = False

    if not token:
        logger.error("Unauthorized: no credentials provided")
        raise HTTPException(
            status_code=401,
            detail="Unauthorized: no credentials provided",
        )

    # Use the same auth handler as API endpoints (provided by content system plugins)
    handler = require_authentication()
    if handler is None:
        logger.error("No auth handler configured — cannot authenticate MCP request")
        raise HTTPException(status_code=401, detail="Unauthorized: no authentication handler configured")

    def _raise_unauthorized() -> None:
        raise HTTPException(
            status_code=401,
            detail="Unauthorized: authentication failed",
        )

    try:
        user = handler(auth_header) if is_bearer else handler(token)
        if user:
            logger.debug("Authenticated user: %s (tenant: %s)", user.get("userId"), user.get("tenantId"))
            return user
        _raise_unauthorized()
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("Authentication failed: %s", e)
        _raise_unauthorized()
