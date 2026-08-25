"""Service for retrieving document summaries and titles from the database."""

import logging

from opentelemetry import trace

from aviator.services.database import DatabaseManager
from aviator.settings import settings

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

TABLE_NAME = settings.summary_table_name


def _qualified_table(schema_name: str | None = None) -> str:
    """Return a schema-qualified table name when *schema_name* is provided."""
    return f"{schema_name}.{TABLE_NAME}" if schema_name else TABLE_NAME


async def _execute_query(query: str, params: list[str], description: str) -> list[tuple]:
    """Execute a parameterised query and return raw rows.

    Args:
        query: SQL query string with %s placeholders.
        params: Parameter values for the query.
        description: Human-readable label for log messages.

    Returns:
        List of row tuples, or empty list on error.

    """
    try:
        pool = await DatabaseManager.get_pool()
        async with pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(query, params)
            return await cur.fetchall()
    except Exception:
        logger.exception("Error executing query: %s", description)
        return []


def _in_clause(ids: list[str]) -> str:
    """Build a SQL IN clause placeholder string."""
    return ", ".join(["%s"] * len(ids))


@tracer.start_as_current_span("retrieve_summaries_by_doc_ids")
async def retrieve_summaries_by_doc_ids(
    doc_ids: list[str],
    schema_name: str | None = None,
) -> list[dict[str, str]]:
    """Retrieve document summaries for the given document IDs.

    Args:
        doc_ids: List of document IDs to fetch summaries for.
        schema_name: PostgreSQL schema to query. Falls back to unqualified table when *None*.

    Returns:
        List of dicts with keys: document_id, summary.

    """
    if not doc_ids:
        return []

    table = _qualified_table(schema_name)
    query = f"SELECT document_id, summary FROM {table} WHERE document_id IN ({_in_clause(doc_ids)})"
    rows = await _execute_query(query, doc_ids, "summaries")
    return [{"document_id": str(r[0]), "summary": str(r[1])} for r in rows]


@tracer.start_as_current_span("retrieve_titles_by_doc_ids")
async def retrieve_titles_by_doc_ids(
    doc_ids: list[str],
    schema_name: str | None = None,
) -> list[dict[str, str]]:
    """Retrieve document titles for the given document IDs.

    Args:
        doc_ids: List of document IDs to fetch titles for.
        schema_name: PostgreSQL schema to query. Falls back to unqualified table when *None*.

    Returns:
        List of dicts with keys: document_id, title.

    """
    if not doc_ids:
        return []

    table = _qualified_table(schema_name)
    query = f"SELECT document_id, title FROM {table} WHERE document_id IN ({_in_clause(doc_ids)})"
    rows = await _execute_query(query, doc_ids, "titles")
    return [{"document_id": str(r[0]), "summary": str(r[1])} for r in rows]
