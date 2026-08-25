"""Migration consumer — Celery tasks that fetch from source and write to target.

Tasks are registered on the main aviator Celery app so the standard
aviator worker can process migration batches (no separate
migration-worker container required).  The producer sends tasks to the
``migration_queue`` (default ``csai-adt-migration``); the aviator worker
just needs to include that queue in its ``-Q`` argument.

**Single-tenant batch architecture:**
The producer groups source row IDs by tenant schema before batching,
so each batch targets a single target schema. Workers receive
single-tenant batches, fetch rows using WHERE id IN (...), and insert
directly to the specified schema (no cross-tenant switching).

This approach keeps queue messages small (just IDs, no embeddings),
distributes the source DB read load across workers, and optimizes
worker efficiency by avoiding schema switching within a batch.
"""

from __future__ import annotations

import hashlib
import json
import logging

from celery import Task
from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, MetaData, Table, Text, select, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from aviator.celery import celery
from aviator.utils.chunk_hash import generate_chunk_hash
from migration.db import dispose_engine, get_engine
from migration.models import MigrationBatch, SourceRow
from migration.schema_manager import ensure_schema
from migration.settings import settings

logger = logging.getLogger(__name__)


class MigrationTaskWithRetry(Task):
    """Celery task base class with exponential-backoff retries for migration tasks."""

    autoretry_for = (Exception,)
    retry_kwargs = {"max_retries": 7}
    retry_backoff = True


# ── Source DB access ──────────────────────────────────────────────────


def _get_source_table() -> Table:
    """Return a SQLAlchemy :class:`Table` reflecting the source vector store table."""
    metadata = MetaData(schema=settings.source_schema)
    return Table(
        settings.source_table,
        metadata,
        Column("id", UUID(as_uuid=True), primary_key=True),
        Column("text", Text),
        Column("metadata", JSONB),
        Column("embedding", Vector()),
        autoload_with=None,
        extend_existing=True,
    )


_FETCH_MAX_RETRIES = 5
_FETCH_BASE_DELAY = 5  # seconds


def _fetch_rows_by_ids(ids: list[str]) -> list:
    """Fetch rows from the source table using WHERE id IN (...).

    Returns a list of tuples: (id, text, metadata, embedding).
    """
    tbl = _get_source_table()
    stmt = select(tbl.c.id, tbl.c.text, tbl.c.metadata, tbl.c.embedding).where(tbl.c.id.in_(ids))

    engine = get_engine(settings.source_dsn)
    with Session(engine) as session:
        result = session.execute(stmt)
        return list(result)


def _fetch_rows_with_retry(ids: list[str]) -> list:
    """Fetch rows from the source table with retry on transient DB errors."""
    for attempt in range(1, _FETCH_MAX_RETRIES + 1):
        try:
            return _fetch_rows_by_ids(ids)
        except OperationalError:
            if attempt == _FETCH_MAX_RETRIES:
                raise
            delay = _FETCH_BASE_DELAY * (2 ** (attempt - 1))
            logger.warning(
                "Transient source DB error (attempt %d/%d). Retrying in %ds…",
                attempt,
                _FETCH_MAX_RETRIES,
                delay,
                exc_info=True,
            )
            dispose_engine(settings.source_dsn)
            import time

            time.sleep(delay)
    return []  # unreachable


def _parse_embedding(embedding: object) -> list[float]:
    """Parse embedding from various source formats."""
    if isinstance(embedding, list):
        return embedding
    if isinstance(embedding, str):
        return json.loads(embedding)
    # numpy array from pgvector - use tolist() for proper conversion
    if hasattr(embedding, "tolist"):
        return embedding.tolist()
    # fallback for other iterables
    return list(embedding)


def _convert_raw_rows_to_source_rows(raw_rows: list) -> list[SourceRow]:
    """Convert fetched rows to SourceRow objects."""
    result = []
    for row_id, _text, metadata, embedding in raw_rows:
        meta = metadata if isinstance(metadata, dict) else json.loads(metadata)
        result.append(
            SourceRow(
                id=str(row_id),
                text=_text,
                metadata=meta,
                embedding=_parse_embedding(embedding),
            )
        )
    return result


# ── Target DB access ──────────────────────────────────────────────────


def _get_target_table(schema: str, table: str) -> Table:
    """Return a SQLAlchemy :class:`Table` reflecting the target vector store table."""
    metadata = MetaData(schema=schema)
    return Table(
        table,
        metadata,
        Column("langchain_id", UUID(as_uuid=True), primary_key=True),
        Column("content", Text),
        Column("embedding", Vector()),
        Column("langchain_metadata", JSONB),
        Column("workspace_id", Text),
        Column("document_id", Text),
        Column("text_hash", Text),
        extend_existing=True,
    )


def _convert_loc_to_start_index(meta: dict) -> dict:
    """Convert source ``loc.lines`` to target ``start_index``.

    The source DB (csai) stores chunk position as::

        {"loc": {"lines": {"from": 0, "to": 5}}}

    where ``from`` / ``to`` are **line numbers** (0-based).

    The target DB (aviator_adt) uses ``start_index`` which
    is normally a **character offset**, but for migrated data we store the
    line-based value under ``start_index`` to avoid confusion with the
    native character-offset ``start_index``.

    The primary consumer (``tools/rag.py``) uses ``start_index`` for ordering
    chunks within a document; migrated chunks use ``start_index`` which can
    be recognised and handled accordingly.

    After conversion the ``loc`` key is removed to avoid stale duplicates.
    """
    loc = meta.get("loc")
    if isinstance(loc, dict):
        lines = loc.get("lines")
        if isinstance(lines, dict) and "from" in lines and "start_index" not in meta:
            meta["start_index"] = lines["from"]
        # Remove the source-specific key to keep metadata clean.
        del meta["loc"]
    return meta


def _prepare_metadata(meta: dict) -> dict:
    """Prepare source metadata for the target DB.

    1. Convert ``loc.lines.from`` → ``start_index``.
    2. Optionally strip ``tenantID``.
    3. Tag with ``_source`` = ``csai_legacy``.
    """
    meta = _convert_loc_to_start_index(meta)
    if settings.strip_tenant_from_meta:
        meta = {k: v for k, v in meta.items() if k != "tenantID"}
    meta["_source"] = "csai_legacy"
    return meta


def _insert_batch(schema: str, rows: list[SourceRow]) -> int:
    """Insert a list of SourceRow objects into *schema*.*table*.

    Uses SQLAlchemy Core's ``insert().on_conflict_do_nothing()`` for
    idempotent bulk inserts.

    Returns the number of rows in the batch (upper bound for inserts).
    ``ON CONFLICT DO NOTHING`` means some may be skipped, but for
    migration progress reporting the batch size is accurate enough.
    """
    table_name = settings.target_table
    tbl = _get_target_table(schema, table_name)

    values_list = []
    for row in rows:
        meta = _prepare_metadata(row.metadata.copy())
        content = row.text
        values_list.append(
            {
                "langchain_id": row.id,
                "content": content,
                "embedding": row.embedding,
                "langchain_metadata": meta,
                "workspace_id": meta.get("workspaceID"),
                "document_id": meta.get("documentID"),
                "text_hash": generate_chunk_hash(content) if content else None,
            }
        )

    engine = get_engine(settings.target_dsn)
    stmt = pg_insert(tbl).values(values_list).on_conflict_do_nothing(index_elements=["langchain_id"])
    with Session(engine) as session:
        session.execute(stmt)
        session.commit()

    return len(values_list)


def _advisory_lock_key(schema: str) -> int:
    """Derive a deterministic 64-bit signed int for a PostgreSQL advisory lock."""
    digest = hashlib.sha256(f"rebuild_indexes:{schema}".encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


# ── Celery Tasks ──────────────────────────────────────────────────────


@celery.task(bind=True, base=MigrationTaskWithRetry, name="migration.consumer.process_migration_batch")
def process_migration_batch(self: Task, batch_dict: dict) -> dict:  # noqa: ARG001
    """Receive a single-tenant ID batch, fetch rows from source, write to target.

    Steps:
        1. Deserialise and validate the batch (target_schema + list of IDs).
        2. Connect to source DB and fetch rows using WHERE id IN (...).
        3. Ensure the target schema + table exist.
        4. Insert all rows into the single target schema using ``ON CONFLICT DO NOTHING``.
    """
    batch = MigrationBatch.model_validate(batch_dict)

    logger.info(
        "Processing batch: schema='%s', %d IDs",
        batch.target_schema,
        len(batch.ids),
    )

    if settings.dry_run:
        logger.info(
            "[DRY-RUN] Would fetch and migrate %d IDs to schema '%s'",
            len(batch.ids),
            batch.target_schema,
        )
        return {"target_schema": batch.target_schema, "ids_count": len(batch.ids), "processed": 0, "inserted": 0}

    # Fetch rows from source DB
    raw_rows = _fetch_rows_with_retry(batch.ids)
    if not raw_rows:
        logger.warning(
            "No rows fetched for %d IDs (schema='%s') — rows may have been deleted.",
            len(batch.ids),
            batch.target_schema,
        )
        return {"target_schema": batch.target_schema, "ids_count": len(batch.ids), "processed": 0, "inserted": 0}

    # Convert to SourceRow objects
    rows = _convert_raw_rows_to_source_rows(raw_rows)

    # Ensure target schema + table exist
    ensure_schema(batch.target_schema)

    # Insert all rows into the single target schema
    inserted = _insert_batch(batch.target_schema, rows)

    logger.info(
        "Batch complete: schema='%s', %d IDs, fetched=%d, inserted=%d",
        batch.target_schema,
        len(batch.ids),
        len(raw_rows),
        inserted,
    )

    return {
        "target_schema": batch.target_schema,
        "ids_count": len(batch.ids),
        "processed": len(raw_rows),
        "inserted": inserted,
    }


@celery.task(bind=True, base=MigrationTaskWithRetry, name="migration.consumer.rebuild_schema_indexes")
def rebuild_schema_indexes_task(self: Task, schema: str) -> dict:  # noqa: ARG001
    """Rebuild indexes for a single tenant schema.

    The producer fans out one task per schema so each is independently
    retryable and ack-ed.  ``CREATE INDEX IF NOT EXISTS`` makes this
    idempotent — a schema whose indexes already exist is a fast no-op.

    A PostgreSQL advisory lock prevents concurrent workers from building
    indexes for the same schema simultaneously.  If the lock cannot be
    acquired the task returns immediately (the other worker is already
    handling it).
    """
    from migration.index_manager import rebuild_schema_indexes

    lock_key = _advisory_lock_key(schema)
    engine = get_engine(settings.target_dsn)
    with engine.connect() as conn:
        acquired = conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_key}).scalar()
        if not acquired:
            logger.info("Another worker is already rebuilding indexes for '%s' — skipping.", schema)
            return {"schema": schema, "status": "skipped_concurrent"}
        try:
            logger.info("Rebuilding indexes for schema '%s'…", schema)
            rebuild_schema_indexes(schema)
            logger.info("Index rebuild complete for schema '%s'.", schema)
            return {"schema": schema, "status": "indexes_rebuilt"}
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key})
            conn.commit()


@celery.task(bind=True, base=MigrationTaskWithRetry, name="migration.consumer.drop_schema_indexes")
def drop_schema_indexes_task(self: Task, schema: str) -> dict:  # noqa: ARG001
    """Drop non-PK indexes for a single tenant schema before migration.

    The producer fans out one task per schema so each is independently
    retryable and ack-ed.  ``DROP INDEX IF NOT EXISTS`` makes this
    idempotent — a schema whose indexes are already dropped is a fast no-op.

    A PostgreSQL advisory lock prevents concurrent workers from dropping
    indexes for the same schema simultaneously.  If the lock cannot be
    acquired the task returns immediately (the other worker is already
    handling it).
    """
    from migration.index_manager import drop_schema_indexes

    lock_key = _advisory_lock_key(schema)
    engine = get_engine(settings.target_dsn)
    with engine.connect() as conn:
        acquired = conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_key}).scalar()
        if not acquired:
            logger.info("Another worker is already dropping indexes for '%s' — skipping.", schema)
            return {"schema": schema, "status": "skipped_concurrent"}
        try:
            logger.info("Dropping indexes for schema '%s'…", schema)
            dropped = drop_schema_indexes(schema)
            logger.info("Index drop complete for schema '%s' — %d indexes dropped.", schema, dropped)
            return {"schema": schema, "status": "indexes_dropped", "count": dropped}
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key})
            conn.commit()


@celery.task(bind=True, name="migration.consumer.shutdown_workers")
def shutdown_workers(self: Task) -> None:  # noqa: ARG001
    """Signal that migration is complete.

    This task is published by the producer as the **very last** message in
    the queue, after all batch tasks and the optional index rebuild.

    Because the migration tasks now run on the shared aviator worker,
    we only log completion instead of broadcasting a shutdown — the
    worker must stay alive for normal aviator operations.
    """
    logger.info("Migration complete — all batches and index rebuilds finished.")
