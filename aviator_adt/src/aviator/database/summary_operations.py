"""Database operations utilities for workspace documents and summaries."""

from __future__ import annotations

import logging

import sqlalchemy
from sqlalchemy.dialects.postgresql import insert as pg_insert

from aviator.database import database_manager
from aviator.database.models import DocumentSummary
from aviator.exceptions import WorkspaceSummaryRetryableError

logger = logging.getLogger(__name__)


def upsert_workspace_document_with_summary(
    document_id: str,
    summary_text: str,
    title: str,
    title_embeddings: list[float] | None = None,
    schema_name: str | None = None,
) -> dict:
    """Insert or update the workspace document with summary.

    Args:
        document_id (str): Document identifier.
        summary_text (str): Summary text content.
        title (str): Title of the document.
        title_embeddings (list[float] | None): Vector embeddings for the title.
        schema_name (str | None): PostgreSQL schema to use.

    Returns:
        dict: Result containing:
            - success (bool): True if operation succeeded
            - operation (str): "create" or "update"

    Raises:
       WorkspaceSummaryError : If database operation fails.

    """
    db = database_manager.get()
    session = db.get_session(schema_name)

    try:
        # Check if record already exists
        existing = session.query(DocumentSummary).filter_by(document_id=document_id).first()

        if existing:
            # Update existing summary
            existing.summary = summary_text
            existing.title = title
            if title_embeddings is not None:
                existing.title_embeddings = title_embeddings
            logger.info("Updated summary for document %s", document_id)
            operation = "update"
        else:
            # Create new record
            new_record = DocumentSummary(
                document_id=document_id,
                summary=summary_text,
                title=title,
                title_embeddings=title_embeddings,
            )
            session.add(new_record)
            logger.info("Created new document summary for %s", document_id)
            operation = "create"

        session.commit()

    except sqlalchemy.exc.SQLAlchemyError as e:
        session.rollback()
        logger.error("Failed to store summary for document %s: %s", document_id, e)
        raise WorkspaceSummaryRetryableError(206, f"Error storing summary to the database: {e!s}") from e

    else:
        return {"success": True, "operation": operation}

    finally:
        session.close()


def delete_workspace_document_summary(document_id: str, schema_name: str | None = None) -> dict:
    """Delete a document summary.

    Args:
        document_id (str): Document identifier.
        schema_name (str | None): PostgreSQL schema to use.

    Returns:
        dict: Result containing:
            - success (bool): True if operation succeeded

    Raises:
       WorkspaceSummaryError : If database operation fails.

    """
    db = database_manager.get()
    session = db.get_session(schema_name)

    try:
        # Delete summary from database
        session.query(DocumentSummary).filter_by(document_id=document_id).delete()

        logger.info("Deleted summary for document %s in schema %s", document_id, schema_name or "default")
        session.commit()
    except sqlalchemy.exc.SQLAlchemyError as e:
        session.rollback()
        logger.error("Failed to delete summary for document %s: %s", document_id, e)
        raise WorkspaceSummaryRetryableError(206, f"Error deleting summary from the database: {e!s}") from e
    else:
        return {"success": True}
    finally:
        session.close()


def bulk_upsert_workspace_documents_with_summary(
    rows: list[dict],
    schema_name: str | None = None,
) -> int:
    """Bulk insert or update document summaries in a single transaction.

    Uses ``INSERT ... ON CONFLICT (document_id) DO UPDATE``
    via SQLAlchemy Core for efficient batch writes.

    Each dict in *rows* must contain keys:
        ``document_id``, ``summary``, ``title``,
        and optionally ``title_embeddings``.

    Args:
        rows: List of dicts with summary data.
        schema_name: PostgreSQL schema to use.

    Returns:
        Number of rows processed.

    Raises:
        WorkspaceSummaryError: If the database operation fails.

    """
    if not rows:
        return 0

    db = database_manager.get()
    session = db.get_session(schema_name)

    try:
        table = DocumentSummary.__table__
        stmt = pg_insert(table).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["document_id"],
            set_={
                "summary": stmt.excluded.summary,
                "title": stmt.excluded.title,
                "title_embeddings": stmt.excluded.title_embeddings,
            },
        )
        session.execute(stmt)
        session.commit()
        logger.info("Bulk upserted %d document summaries into schema '%s'", len(rows), schema_name or "default")
    except sqlalchemy.exc.SQLAlchemyError as e:
        session.rollback()
        logger.error("Bulk upsert failed: %s", e)
        raise WorkspaceSummaryRetryableError(206, f"Error in bulk upsert of summaries: {e!s}") from e
    finally:
        session.close()

    return len(rows)
