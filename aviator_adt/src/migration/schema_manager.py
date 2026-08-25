"""Lazy tenant schema creation for migration targets.

Uses ``aviator.vector_store.schema`` (the single source of truth) for
table initialisation via ``PGEngine`` and index creation, ensuring the
migration produces the exact same schema that the main ADT application
expects.
"""

from __future__ import annotations

import logging
import threading

from psycopg.errors import DuplicateTable
from sqlalchemy.exc import ProgrammingError

from aviator.vector_store.schema import create_indexes, create_schema_if_not_exists, init_vector_table
from migration.settings import settings

logger = logging.getLogger(__name__)

# Thread-safe cache of schemas known to exist.
_initialised_schemas: set[str] = set()
_lock = threading.Lock()


def ensure_schema(schema_name: str) -> None:
    """Ensure that *schema_name* exists with the correct table and indexes.

    Uses a thread-safe cache so each schema is initialised at most once per
    process lifetime.  ``CREATE SCHEMA IF NOT EXISTS`` + ``IF NOT EXISTS``
    on the table / indexes makes this safe even under concurrent workers.

    When ``settings.defer_indexes`` is ``True``, only the table is created
    (no secondary indexes).  Use the ``rebuild_indexes`` Celery task (or
    ``python -m migration.index_manager rebuild``) after the migration to
    build all indexes in one pass.
    """
    if schema_name in _initialised_schemas:
        return

    with _lock:
        # Double-checked locking.
        if schema_name in _initialised_schemas:
            return

        logger.info("Ensuring target schema '%s' exists…", schema_name)
        create_schema_if_not_exists(settings.target_dsn, schema_name)

        # Use langchain PGEngine for table creation — single source of truth.
        try:
            init_vector_table(
                dsn=settings.target_dsn,
                schema=schema_name,
                table=settings.target_table,
                vector_size=settings.vector_size,
                metadata_json_column=settings.metadata_json_column,
            )
        except ProgrammingError as exc:
            if isinstance(getattr(exc, "orig", None), DuplicateTable):
                logger.info("Table '%s.%s' already exists.", schema_name, settings.target_table)
            else:
                raise

        if not settings.defer_indexes:
            create_indexes(
                dsn=settings.target_dsn,
                schema=schema_name,
                table=settings.target_table,
                metadata_json_column=settings.metadata_json_column,
            )
        else:
            logger.info(
                "Skipping index creation for '%s' (defer_indexes=True).",
                schema_name,
            )

        # Create ORM-managed tables (e.g. workspace_document_summaries)
        # so the summary backfill consumer can write to them immediately.
        try:
            from sqlalchemy import create_engine

            from aviator.database.models import Base

            engine = create_engine(settings.target_dsn)
            with engine.begin() as conn:
                mapped = conn.execution_options(schema_translate_map={None: schema_name})
                Base.metadata.create_all(bind=mapped, checkfirst=True)
            engine.dispose()
            logger.info("ORM tables created in schema '%s'.", schema_name)
        except Exception:
            logger.exception("Failed to create ORM tables in schema '%s'.", schema_name)
            raise

        _initialised_schemas.add(schema_name)
        logger.info("Schema '%s' ready.", schema_name)


def resolve_target_schema(metadata: dict) -> str:
    """Determine the target schema from a row's metadata.

    Logic:
        * ``metadata['tenantID']`` present and non-empty → ``{prefix}{tenantID}``
        * Otherwise → ``settings.target_default_schema``
    """
    tenant_id = (metadata.get("tenantID") or "").strip()
    if tenant_id:
        return f"{settings.target_tenant_prefix}{tenant_id}"
    return settings.target_default_schema
