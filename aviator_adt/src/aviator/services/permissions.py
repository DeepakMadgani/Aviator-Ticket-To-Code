"""Implement Content Aviator ADT RAG permission filtering."""

import asyncio
import inspect
import logging
from typing import Any

from fastapi import HTTPException, status
from opentelemetry import trace

from aviator.plugins import load_rag_permission_filters
from aviator.settings import settings

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


async def apply_rag_permission_filter[T](
    items: list[T],
    user: dict[str, Any] | None,
    span_name: str = "apply_rag_permission_filter",
) -> list[T]:
    """Apply RAG permission filter with mandatory fail-closed enforcement.

    When content_system is configured, a matching permission filter plugin MUST exist
    and execute successfully, or an empty list is returned. When content_system is
    not configured, all items pass through.

    Args:
        items: List of items to filter (Chunk, ContextDocumentModel, etc.).
        user: User object for permission checking.
        span_name: Span name for tracing (default: apply_rag_permission_filter).

    Returns:
        Filtered items list. Empty list if content_system is set but no plugin found
        or plugin fails.

    """
    if settings.content_system is None:
        return items

    with tracer.start_as_current_span(f"{span_name}:permission_check:{settings.content_system}"):
        logger.info("Applying permission filter to items.")

        perm_filter = next(
            (plugin_filter for name, plugin_filter in load_rag_permission_filters() if name == settings.content_system),
            None,
        )

        if perm_filter is None:
            logger.error(
                "No RAG permission filter plugin configured for content system '%s'. Raising 403.",
                settings.content_system,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission check failed for content system '{settings.content_system}'.",
            )

        try:
            if inspect.iscoroutinefunction(perm_filter):
                filtered = await perm_filter(items, user)
            else:
                filtered = await asyncio.to_thread(perm_filter, items, user)
            logger.info(
                "Permission filter '%s' passed. %d -> %d item(s) after filtering.",
                settings.content_system,
                len(items),
                len(filtered),
            )
        except Exception as e:
            logger.error(
                "Permission filter '%s' failed with error: %s. Returning 0 items.",
                settings.content_system,
                str(e),
            )
            return []
        else:
            return filtered
