"""Pre-flight checks executed before the migration begins."""

from __future__ import annotations

import logging
import sys

from sqlalchemy import text
from sqlalchemy.orm import Session

from migration.checkpoint import load_progress
from migration.db import get_engine
from migration.settings import settings

logger = logging.getLogger(__name__)


class PreflightError(RuntimeError):
    """Raised when a pre-flight check fails."""


class MigrationAlreadyCompleteError(PreflightError):
    """Raised when the migration has already been marked as completed."""


def _check_source_db() -> None:
    """Verify the source DB is reachable and the source table exists."""
    if not settings.source_dsn:
        msg = (
            "Source database connection is not configured. "
            "Set MIGRATION_SOURCE_DSN or the individual MIGRATION_SOURCE_HOST/USER/PASSWORD/DATABASE variables."
        )
        raise PreflightError(msg)
    logger.info("Checking source database…")
    engine = get_engine(settings.source_dsn)
    with Session(engine) as session:
        row = session.execute(
            text("SELECT 1 FROM information_schema.tables WHERE table_schema = :schema AND table_name = :table"),
            {"schema": settings.source_schema, "table": settings.source_table},
        ).fetchone()
        if row is None:
            msg = f"Source table '{settings.source_schema}.{settings.source_table}' does not exist."
            raise PreflightError(msg)
    logger.info("Source DB OK — table '%s.%s' found.", settings.source_schema, settings.source_table)


def _check_target_db() -> None:
    """Verify the target DB is reachable."""
    logger.info("Checking target database…")
    engine = get_engine(settings.target_dsn)
    with Session(engine) as session:
        session.execute(text("SELECT 1"))
    logger.info("Target DB OK.")


def _get_vector_dimension(dsn: str, schema: str, table: str) -> int | None:
    """Query the vector dimension of the ``embedding`` column.

    Returns ``None`` if the table or column does not exist yet.
    """
    engine = get_engine(dsn)
    with Session(engine) as session:
        row = session.execute(
            text(
                """
                SELECT atttypmod
                FROM pg_attribute a
                JOIN pg_class c ON a.attrelid = c.oid
                JOIN pg_namespace n ON c.relnamespace = n.oid
                WHERE n.nspname = :schema
                  AND c.relname = :table
                  AND a.attname = 'embedding'
                  AND a.atttypmod > 0
                """
            ),
            {"schema": schema, "table": table},
        ).fetchone()
        return row[0] if row else None


def _check_dimensions() -> None:
    """Ensure vector dimensions match between source and target (if target table exists)."""
    logger.info("Checking vector dimensions…")
    source_dim = _get_vector_dimension(settings.source_dsn, settings.source_schema, settings.source_table)
    if source_dim is None:
        logger.warning(
            "Could not determine source vector dimension (column may not have a fixed type modifier). "
            "Skipping dimension check."
        )
        return

    target_dim = _get_vector_dimension(settings.target_dsn, settings.target_default_schema, settings.target_table)
    if target_dim is None:
        logger.info("Target table does not exist yet — dimension check will pass.")
        return

    if source_dim != target_dim:
        msg = (
            f"Vector dimension mismatch: source={source_dim}, target={target_dim}. "
            f"Source and target embedding columns must have the same dimensionality."
        )
        raise PreflightError(msg)

    logger.info("Dimensions match: %d.", source_dim)


def _check_not_completed() -> None:
    """Ensure the migration has not already been marked as completed."""
    progress = load_progress()
    if progress.status == "completed":
        msg = "Migration already completed. Delete or reset the migration_state row to re-run."
        raise MigrationAlreadyCompleteError(msg)
    logger.info("Migration state: %s (migrated=%d).", progress.status, progress.migrated)


def run_preflight() -> None:
    """Execute all pre-flight checks. Calls ``sys.exit(1)`` on failure."""
    checks = [
        _check_source_db,
        _check_target_db,
        _check_dimensions,
        _check_not_completed,
    ]
    for check in checks:
        try:
            check()
        except MigrationAlreadyCompleteError:
            logger.info("Migration already completed — exiting cleanly.")
            sys.exit(0)
        except PreflightError:
            logger.exception("Pre-flight check failed")
            sys.exit(1)
        except Exception:
            logger.exception("Unexpected error during pre-flight check")
            sys.exit(1)

    logger.info("All pre-flight checks passed.")
