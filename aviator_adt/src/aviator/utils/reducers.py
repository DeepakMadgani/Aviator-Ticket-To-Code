"""Reducer functions for LangGraph state management."""

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aviator.models import WhereClauseReferenceModel

logger = logging.getLogger(__name__)


def reduce_where_clauses(
    left: list["WhereClauseReferenceModel"] | None,
    right: list["WhereClauseReferenceModel"] | dict[str, Any] | None,
) -> list["WhereClauseReferenceModel"]:
    """Merge where clauses with override-by-default and opt-in append behavior.

    Default behavior: override previous where clauses with the incoming list.
    Append behavior: allowed only when an explicit server-side append flag is
    provided in dict payload form, e.g. {"append": true, "where": [...]}.
    """

    existing = left or []
    logger.debug("Merging where clauses - existing=%s, incoming=%s", existing, right)

    if right is None:
        logger.debug("No incoming where clauses provided, using existing: %s", existing)
        return existing

    if isinstance(right, list):
        logger.info("Override existing where clauses with incoming list: %s", right)
        return right

    if isinstance(right, dict):
        incoming = right.get("where")
        if incoming is None:
            incoming = right.get("value")
        if not isinstance(incoming, list):
            incoming = []

        append_mode = bool(right.get("append"))
        merged_where = [*existing, *incoming] if append_mode else incoming
        output_where = []
        # handling deduplication of where clauses when append mode is enabled
        for where_clause in merged_where:
            if where_clause not in output_where:
                output_where.append(where_clause)
        action = "appended" if append_mode else "overridden"
        logger.info(
            "Where clauses %s successfully - : existing=%s, incoming=%s, final=%s",
            action,
            existing,
            incoming,
            output_where,
        )
        return output_where

    logger.debug("Invalid incoming payload format, using existing where clauses: %s", existing)
    return existing
