"""Migration producer — reads source IDs and publishes tenant-grouped batches.

The producer:
1. Reads all row IDs with their tenant info from the source table (paginated).
2. Groups IDs by target schema (based on tenantID in metadata).
3. Batches IDs within each tenant group by ``batch_size``.
4. Publishes each batch with its target_schema to the queue.

Workers receive single-tenant batches, fetch rows using WHERE id IN (...),
and write directly to the specified schema (no grouping needed).

This approach keeps queue messages small (just IDs), ensures each batch
targets a single schema, and distributes the source DB read load across workers.
"""

from __future__ import annotations

import hashlib
import logging
import signal
import threading
from typing import TYPE_CHECKING

from sqlalchemy import Engine, MetaData, Table, select, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from migration.checkpoint import load_progress, mark_completed, save_progress
from migration.db import dispose_engine, get_engine
from migration.models import MigrationBatch, MigrationProgress
from migration.preflight import run_preflight
from migration.settings import settings

if TYPE_CHECKING:
    from celery import Celery

logger = logging.getLogger(__name__)

# ── Graceful shutdown & producer exclusion ────────────────────────────

_shutdown_event = threading.Event()


def _signal_handler(signum: int, frame) -> None:  # noqa: ANN001, ARG001
    """Set the shutdown event when SIGTERM or SIGINT is received."""
    logger.info("Received signal %d — initiating graceful shutdown…", signum)
    _shutdown_event.set()


def _producer_lock_key() -> int:
    """Derive a deterministic 64-bit signed int for the producer advisory lock."""
    digest = hashlib.sha256(b"migration_producer_exclusive").digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


_LOCK_ACQUIRE_ATTEMPTS = 12
_LOCK_ACQUIRE_INTERVAL = 10  # seconds


def _acquire_producer_lock(engine: Engine) -> None:
    """Acquire a session-level PostgreSQL advisory lock.

    Prevents concurrent migration producers from publishing duplicate
    messages.  Retries up to ``_LOCK_ACQUIRE_ATTEMPTS`` times, giving a
    previous producer time to finish gracefully after receiving SIGTERM.
    """
    key = _producer_lock_key()
    for attempt in range(1, _LOCK_ACQUIRE_ATTEMPTS + 1):
        if _shutdown_event.is_set():
            msg = "Shutdown requested while waiting for producer lock."
            raise RuntimeError(msg)
        conn = engine.connect()
        acquired = conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": key}).scalar()
        if acquired:
            logger.info("Acquired producer advisory lock (attempt %d).", attempt)
            return conn
        conn.close()
        if attempt < _LOCK_ACQUIRE_ATTEMPTS:
            logger.info(
                "Another producer holds the lock (attempt %d/%d). Retrying in %ds…",
                attempt,
                _LOCK_ACQUIRE_ATTEMPTS,
                _LOCK_ACQUIRE_INTERVAL,
            )
            _shutdown_event.wait(_LOCK_ACQUIRE_INTERVAL)
    msg = (
        f"Could not acquire producer lock after {_LOCK_ACQUIRE_ATTEMPTS} attempts — "
        "another producer may still be running."
    )
    raise RuntimeError(msg)


def _release_producer_lock(conn) -> None:  # noqa: ANN001
    """Release the producer advisory lock and close the connection."""
    try:
        conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _producer_lock_key()})
        conn.commit()
        logger.info("Released producer advisory lock.")
    finally:
        conn.close()


def _get_source_table() -> Table:
    """Return a SQLAlchemy :class:`Table` reflecting the source vector store table."""
    from pgvector.sqlalchemy import Vector
    from sqlalchemy import Column, Text

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


# ── Retry constants ───────────────────────────────────────────────────

_FETCH_MAX_RETRIES = 5
_FETCH_BASE_DELAY = 5  # seconds
_ID_PAGE_SIZE = 50_000  # Number of IDs to fetch per query (paginated)


def _fetch_ids_with_tenant_page(engine: Engine, last_id: str | None) -> list[tuple[str, str]]:
    """Fetch a page of source row IDs with tenant info, ordered by id.

    Returns a list of (id, target_schema) tuples (up to _ID_PAGE_SIZE).
    The target_schema is resolved from metadata->>'tenantID'.
    """
    from migration.schema_manager import resolve_target_schema

    tbl = _get_source_table()
    # Fetch id and tenantID from metadata JSONB
    stmt = select(tbl.c.id, tbl.c.metadata["tenantID"].astext).order_by(tbl.c.id).limit(_ID_PAGE_SIZE)
    if last_id is not None:
        stmt = stmt.where(tbl.c.id > last_id)

    with Session(engine) as session:
        result = session.execute(stmt)
        rows = []
        for row_id, tenant_id in result:
            # Resolve schema using the same logic as consumer
            schema = resolve_target_schema({"tenantID": tenant_id or ""})
            rows.append((str(row_id), schema))
        return rows


def _fetch_all_ids_grouped_by_tenant() -> dict[str, list[str]]:
    """Fetch all source row IDs grouped by target schema.

    Paginates through the source table to avoid memory issues with very
    large tables. Returns a dict mapping target_schema → list of IDs.
    """
    from collections import defaultdict

    grouped: dict[str, list[str]] = defaultdict(list)
    last_id: str | None = None
    total_fetched = 0

    for attempt in range(1, _FETCH_MAX_RETRIES + 1):
        try:
            engine = get_engine(settings.source_dsn)
            while True:
                if _shutdown_event.is_set():
                    msg = "Shutdown requested while fetching IDs."
                    raise RuntimeError(msg)

                page = _fetch_ids_with_tenant_page(engine, last_id)
                if not page:
                    break

                for row_id, schema in page:
                    grouped[schema].append(row_id)

                total_fetched += len(page)
                last_id = page[-1][0]

                if len(page) < _ID_PAGE_SIZE:
                    # Last page
                    break

            logger.info(
                "Fetched %d total IDs from source table, grouped into %d schemas.",
                total_fetched,
                len(grouped),
            )
            return dict(grouped)

        except OperationalError:
            if attempt == _FETCH_MAX_RETRIES:
                raise
            delay = _FETCH_BASE_DELAY * (2 ** (attempt - 1))
            logger.warning(
                "Transient source DB error during ID fetch (attempt %d/%d). Retrying in %ds…",
                attempt,
                _FETCH_MAX_RETRIES,
                delay,
                exc_info=True,
            )
            dispose_engine(settings.source_dsn)
            # Reset and retry from the beginning
            grouped = defaultdict(list)
            last_id = None
            total_fetched = 0
            _shutdown_event.wait(delay)

    return {}  # unreachable, satisfies type checker


def _publish_batch(celery_app, batch: MigrationBatch, batch_num: int) -> None:  # noqa: ANN001
    """Send a single-tenant ID batch to the Celery migration task queue."""
    celery_app.send_task(
        "migration.consumer.process_migration_batch",
        args=[batch.model_dump(mode="json")],
        queue=settings.migration_queue,
        exchange=settings.migration_queue,
        routing_key=settings.migration_queue,
    )
    logger.debug(
        "Published batch %d → schema='%s', %d IDs",
        batch_num,
        batch.target_schema,
        len(batch.ids),
    )


def run_producer(celery_app: Celery) -> None:
    """Run the main producer loop.

    1. Install signal handlers for graceful shutdown.
    2. Run pre-flight checks.
    3. Acquire an advisory lock (only one producer may run at a time).
    4. Count source rows and publish (offset, limit) batches.
    5. Update checkpoint after each published batch.
    6. On SIGTERM/SIGINT, stop after the current batch and save checkpoint.

    The advisory lock prevents concurrent producers from publishing
    duplicate messages.  On process termination the lock is auto-released
    by PostgreSQL so a replacement producer can start immediately.
    """
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    run_preflight()

    engine = get_engine(settings.target_dsn)
    lock_conn = _acquire_producer_lock(engine)
    try:
        _run_producer_locked(celery_app, engine)
    finally:
        _release_producer_lock(lock_conn)


def _run_producer_locked(celery_app: Celery, engine: Engine) -> None:
    """Core producer logic, executed while holding the advisory lock.

    Fetch all source row IDs grouped by tenant schema, batch within each
    tenant, and publish single-tenant batches. Workers receive batches
    that target a single schema, avoiding cross-tenant switching.

    A single persistent session to the **target** database is held
    open for checkpoint reads/writes so we don't exhaust
    ``max_connections`` over millions of rows.
    """
    with Session(engine) as target_session:
        progress = load_progress(session=target_session)
        if progress.status == "completed":
            logger.info("Migration already completed — nothing to do.")
            return

        # Fetch all IDs grouped by target schema
        grouped_ids = _fetch_all_ids_grouped_by_tenant()
        total_rows = sum(len(ids) for ids in grouped_ids.values())
        logger.info("Source table has %d rows across %d schemas.", total_rows, len(grouped_ids))

        if total_rows == 0:
            logger.info("Source table is empty — nothing to migrate.")
            mark_completed(0, session=target_session)
            return

        # Drop indexes before migration if enabled and migration has not started
        if settings.drop_indexes_before_migration and progress.migrated == 0:
            if settings.dry_run:
                logger.info("[DRY-RUN] Would drop indexes for %d schemas.", len(grouped_ids))
            else:
                for schema in sorted(grouped_ids.keys()):
                    celery_app.send_task(
                        "migration.consumer.drop_schema_indexes",
                        args=[schema],
                        queue=settings.migration_queue,
                        exchange=settings.migration_queue,
                        routing_key=settings.migration_queue,
                    )
                logger.info(
                    "Index drop tasks enqueued for %d schemas — will complete before batches are processed.",
                    len(grouped_ids),
                )

        if progress.total_rows is None:
            progress.total_rows = total_rows

        # Resume from checkpoint: skip already-published IDs
        # Note: resumption is approximate (by count) since we don't track per-tenant state
        skip_count = progress.migrated
        batch_size = settings.batch_size

        batches_published = 0
        total_published = 0
        skipped = 0

        # Process tenants in sorted order for deterministic resumption
        for schema in sorted(grouped_ids.keys()):
            tenant_ids = grouped_ids[schema]

            # Skip already-published IDs (approximate resumption)
            if skipped + len(tenant_ids) <= skip_count:
                skipped += len(tenant_ids)
                continue

            # Partial skip within this tenant
            tenant_start = max(0, skip_count - skipped)
            skipped += tenant_start

            for i in range(tenant_start, len(tenant_ids), batch_size):
                if _shutdown_event.is_set():
                    logger.info(
                        "Shutdown requested — stopping after %d / %d rows.",
                        total_published + skipped,
                        total_rows,
                    )
                    return

                batch_ids = tenant_ids[i : i + batch_size]
                batch = MigrationBatch(target_schema=schema, ids=batch_ids)
                batches_published += 1

                if settings.dry_run:
                    logger.info(
                        "[DRY-RUN] Would publish batch %d: schema='%s', %d IDs",
                        batches_published,
                        schema,
                        len(batch_ids),
                    )
                else:
                    _publish_batch(celery_app, batch, batches_published)

                total_published += len(batch_ids)

                # Save progress: migrated = total published so far (across all tenants)
                progress = MigrationProgress(
                    last_id=batch_ids[-1],
                    status="in_progress",
                    total_rows=total_rows,
                    migrated=total_published + skipped,
                )
                save_progress(progress, session=target_session)

                logger.info(
                    "Progress: %d / %d rows published (batch %d, schema='%s', %d IDs)",
                    total_published + skipped,
                    total_rows,
                    batches_published,
                    schema,
                    len(batch_ids),
                )

        if _shutdown_event.is_set():
            return

        if total_published + skipped < total_rows:
            msg = (
                f"Migration incomplete: published {total_published + skipped} of {total_rows} rows. "
                f"Re-run the producer to continue from the last checkpoint."
            )
            logger.error(msg)
            raise RuntimeError(msg)

        mark_completed(total_rows, session=target_session)

    logger.info(
        "Producer finished — all %d rows published in %d batches across %d schemas.",
        total_rows,
        batches_published,
        len(grouped_ids),
    )

    if not settings.dry_run:
        # Fan out one index-rebuild task per schema so each is independently
        # retryable.  They run after all batch tasks because Celery processes
        # tasks in FIFO order and these are enqueued last.
        if settings.defer_indexes:
            from migration.index_manager import _get_all_target_schemas

            target_schemas = _get_all_target_schemas()
            for schema in target_schemas:
                celery_app.send_task(
                    "migration.consumer.rebuild_schema_indexes",
                    args=[schema],
                    queue=settings.migration_queue,
                    exchange=settings.migration_queue,
                    routing_key=settings.migration_queue,
                )
            logger.info(
                "Index rebuild tasks enqueued for %d schemas — will run after all batches are processed.",
                len(target_schemas),
            )

        # Schedule worker shutdown as the very last queued task.
        celery_app.send_task(
            "migration.consumer.shutdown_workers",
            queue=settings.migration_queue,
            exchange=settings.migration_queue,
            routing_key=settings.migration_queue,
        )
        logger.info("Worker shutdown task enqueued — workers will exit after all work is done.")
