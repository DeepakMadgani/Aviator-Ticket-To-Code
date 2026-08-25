"""Implement Content Aviator ADT authentication."""

import logging
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException, Request, status

from aviator import plugins
from aviator.settings import settings

logger = logging.getLogger(__name__)


# This dependency will try every registered scheme in turn
def require_authentication(endpoint: str | None = None) -> Callable:
    """Try to authenticate the user via all registered authentication schemes."""

    def none_auth_handler(request: Request) -> dict[str, Any]:
        result = {}

        if settings.dev_tools and settings.multi_tenant_enabled:
            # dummy support for multi-tenancy via header (for testing without auth handlers)
            result["tenantId"] = request.headers.get("tenantid", None)

        return result

    if settings.content_system is None:
        return none_auth_handler

    handler = plugins.get_auth_handler(endpoint)
    if handler is None:

        def missing_auth_handler(request: Request) -> dict[str, Any]:  # noqa: ARG001
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication failed for this request. Please contact the administrator",
            )

        logger.error(
            "No auth handler configured for content_system='%s'. Denying request.",
            settings.content_system,
        )
        return missing_auth_handler

    return handler
