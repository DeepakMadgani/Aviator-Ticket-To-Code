"""Manage target indexes for optimised bulk migration.

Dropping secondary indexes before the migration and rebuilding them
afterwards is **dramatically** faster than maintaining them incrementally
(typically 10-50x faster for millions of rows).

Usage:
    # Before migration — drop expensive indexes:
    uv run python -m migration.index_manager drop

    # After migration — rebuild all indexes:
    uv run python -m migration.index_manager rebuild

    # Check current index status:
    uv run python -m migration.index_manager status
"""

from __future__ import annotations

import logging
import sys

from sqlalchemy import text
from sqlalchemy.orm import Session

from aviator.vector_store.schema import create_indexes, quote_ident
from migration.db import get_engine
from migration.settings import settings

logger = logging.getLogger(__name__)


def _get_all_target_schemas() -> list[str]:
    """Return a list of schemas containing the target table."""
    engine = get_engine(settings.target_dsn)
    with Session(engine) as session:
        result = session.execute(
            text("SELECT DISTINCT table_schema FROM information_schema.tables WHERE table_name = :table"),
            {"table": settings.target_table},
        )
        return [row[0] for row in result]


def _get_existing_indexes(schema: str) -> list[dict]:
    """Return existing indexes on the target table in a given schema."""
    engine = get_engine(settings.target_dsn)
    with Session(engine) as session:
        result = session.execute(
            text(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE schemaname = :schema AND tablename = :table AND indexname != :pkey"
            ),
            {"schema": schema, "table": settings.target_table, "pkey": f"{settings.target_table}_pkey"},
        )
        return [{"name": row[0], "definition": row[1]} for row in result]


def drop_schema_indexes(schema: str) -> int:
    """Drop non-PK indexes for a **single** schema.

    Designed to be called from an individual Celery task so that each
    schema is independently retryable and acknowledged.

    Args:
        schema: The PostgreSQL schema to drop indexes for.

    Returns:
        The number of indexes dropped.

    """
    engine = get_engine(settings.target_dsn)
    with Session(engine) as session:
        indexes = _get_existing_indexes(schema)
        if not indexes:
            logger.info("Schema '%s': no non-PK indexes found.", schema)
            return 0
        for idx in indexes:
            logger.info("Dropping index: %s.%s", schema, idx["name"])
            session.execute(text(f"DROP INDEX IF EXISTS {quote_ident(schema)}.{quote_ident(idx['name'])}"))
        session.commit()
        logger.info("Schema '%s': dropped %d indexes.", schema, len(indexes))
        return len(indexes)


def drop_indexes(schemas: list[str] | None = None) -> None:
    """Drop non-PK indexes on the target table to speed up bulk inserts."""
    schemas = schemas or _get_all_target_schemas()
    if not schemas:
        logger.warning("No target schemas found — nothing to drop.")
        return

    for schema in schemas:
        drop_schema_indexes(schema)


def rebuild_schema_indexes(schema: str) -> None:
    """Rebuild indexes for a **single** schema.

    Designed to be called from an individual Celery task so that each
    schema is independently retryable and acknowledged.

    Delegates to :func:`aviator.vector_store.schema.create_indexes` which
    handles ``maintenance_work_mem`` escalation automatically.
    """
    create_indexes(
        dsn=settings.target_dsn,
        schema=schema,
        table=settings.target_table,
        metadata_json_column=settings.metadata_json_column,
        maintenance_work_mem=settings.maintenance_work_mem,
    )


def rebuild_indexes(schemas: list[str] | None = None) -> None:
    """Rebuild ANN, GIN, and BTREE indexes on the target table.

    Iterates over all schemas sequentially.  Primarily used by the CLI
    (``python -m migration.index_manager rebuild``).  The Celery-based
    migration uses per-schema tasks instead — see
    ``rebuild_schema_indexes_task`` in ``consumer.py``.
    """
    schemas = schemas or _get_all_target_schemas()
    if not schemas:
        logger.warning("No target schemas found — nothing to rebuild.")
        return

    for schema in schemas:
        rebuild_schema_indexes(schema)


def show_status(schemas: list[str] | None = None) -> None:
    """Print current index status for all target schemas."""
    schemas = schemas or _get_all_target_schemas()
    if not schemas:
        logger.info("No target schemas found.")
        return

    engine = get_engine(settings.target_dsn)
    for schema in schemas:
        indexes = _get_existing_indexes(schema)
        with Session(engine) as session:
            result = session.execute(
                text(f"SELECT count(*) FROM {quote_ident(schema)}.{quote_ident(settings.target_table)}")
            )
            row_count = result.scalar_one()
        logger.info("Schema '%s': %d rows, %d non-PK indexes", schema, row_count, len(indexes))
        for idx in indexes:
            logger.info("  - %s", idx["name"])


def main() -> None:
    """CLI entrypoint: drop | rebuild | status."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        stream=sys.stdout,
    )

    if len(sys.argv) < 2 or sys.argv[1] not in ("drop", "rebuild", "status"):
        print("Usage: python -m migration.index_manager <drop|rebuild|status>")  # noqa: T201
        sys.exit(1)

    action = sys.argv[1]
    if action == "drop":
        drop_indexes()
    elif action == "rebuild":
        rebuild_indexes()
    elif action == "status":
        show_status()


if __name__ == "__main__":
    main()
