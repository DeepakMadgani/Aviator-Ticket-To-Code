"""Summary backfill producer — reads the CSAI source DB and publishes batches for summary generation.

Discovers documents in the old CSAI source vector store that do not yet have
summaries in the ADT target ``workspace_document_summaries`` table, reads and
aggregates their chunks, and publishes ``SummaryBatch`` messages to the
``csai-adt-summary`` Celery queue for the consumer to generate LLM summaries.

Source DB (old CSAI):
  - Table configured via ``MIGRATION_SOURCE_TABLE`` (connected via ``MIGRATION_SOURCE_DSN``)
  - Columns: ``id`` (UUID), ``text`` (TEXT), ``metadata`` (JSONB)
  - Document identity: ``metadata->>'documentID'``
  - Workspace: ``metadata->>'workspaceID'``
  - Chunk ordering: ``metadata->'loc'->'lines'->>'from'`` (line numbers, 0-based)

Target DB (ADT):
  - Table: ``workspace_document_summaries`` (connected via ``MIGRATION_TARGET_DSN``)
  - Used only for **filtering** — documents already present here are skipped
  - Actual summary writes are performed by the consumer, not this producer

Scalability:
  - Documents are discovered in **pages** (cursor-based pagination using
    ``documentID`` as a sort key) so the producer never loads all documents
    into memory.
  - Checkpoint stores the ``last_id`` (last processed ``documentID``) so
    restarts resume from exactly where they left off, regardless of how
    many documents exist.

Todo:
  - Batch chunk reads per page into a single
    ``WHERE documentID IN (...)`` query instead of one query per document,
    to reduce source DB round-trips from ~500 to 1 per page.

"""

from __future__ import annotations

import logging
import time

from sqlalchemy import Column, Integer, MetaData, Table, Text, func, select
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from migration.checkpoint import load_progress, mark_completed, save_progress
from migration.db import dispose_engine, get_engine
from migration.models import MigrationProgress, SummaryBatch, SummarySourceDocument
from migration.schema_manager import resolve_target_schema
from migration.settings import settings

logger = logging.getLogger(__name__)

_SUMMARY_MIGRATION_ID = "summary_backfill"

# ── Retry constants ───────────────────────────────────────────────────

_FETCH_MAX_RETRIES = 5
_FETCH_BASE_DELAY = 5  # seconds

# Page size for the discovery query — how many distinct documents to
# fetch from the source DB per round-trip.
_DISCOVER_PAGE_SIZE = 500


def _get_source_table() -> Table:
    """Return a SQLAlchemy :class:`Table` reflecting the source vector store table."""
    metadata_obj = MetaData(schema=settings.source_schema)
    return Table(
        settings.source_table,
        metadata_obj,
        Column("id", UUID(as_uuid=True), primary_key=True),
        Column("text", Text),
        Column("metadata", JSONB),
        autoload_with=None,
        extend_existing=True,
    )


def _get_summary_table(schema: str) -> Table:
    """Return a SQLAlchemy :class:`Table` for the target summary table."""
    from aviator.settings import settings as aviator_settings

    metadata_obj = MetaData(schema=schema)
    return Table(
        aviator_settings.summary_table_name,
        metadata_obj,
        Column("document_id", Text, primary_key=True),
        autoload_with=None,
        extend_existing=True,
    )


# ── Discovery (paginated) ────────────────────────────────────────────


def _count_source_documents() -> int:
    """Return the total number of distinct documents in the source table."""
    tbl = _get_source_table()
    doc_id_col = tbl.c.metadata["documentID"].astext

    stmt = select(func.count(func.distinct(doc_id_col))).where(doc_id_col.is_not(None))

    engine = get_engine(settings.source_dsn)
    with Session(engine) as session:
        return session.execute(stmt).scalar_one()


def _fetch_document_page(last_doc_id: str | None, page_size: int) -> list[dict]:
    """Fetch a page of distinct documents from the source table, ordered by documentID.

    Uses cursor-based pagination: returns documents with
    ``documentID > last_doc_id``, limited to *page_size*.
    """
    tbl = _get_source_table()

    doc_id_expr = tbl.c.metadata["documentID"].astext
    doc_id_col = doc_id_expr.label("document_id")
    ws_id_col = func.min(tbl.c.metadata["workspaceID"].astext).label("workspace_id")
    tenant_id_col = func.min(tbl.c.metadata["tenantID"].astext).label("tenant_id")

    stmt = (
        select(doc_id_col, ws_id_col, tenant_id_col)
        .where(doc_id_expr.is_not(None))
        .group_by(doc_id_col)
        .order_by(doc_id_col)
        .limit(page_size)
    )

    if last_doc_id is not None:
        stmt = stmt.where(doc_id_expr > last_doc_id)

    engine = get_engine(settings.source_dsn)
    with Session(engine) as session:
        rows = session.execute(stmt).all()

    return [
        {
            "document_id": row.document_id,
            "workspace_id": row.workspace_id or "",
            "tenant_id": row.tenant_id or "",
        }
        for row in rows
    ]


def _fetch_document_page_with_retry(last_doc_id: str | None, page_size: int) -> list[dict]:
    """Fetch a page of documents with retry on transient DB errors."""
    for attempt in range(1, _FETCH_MAX_RETRIES + 1):
        try:
            return _fetch_document_page(last_doc_id, page_size)
        except OperationalError:
            if attempt == _FETCH_MAX_RETRIES:
                raise
            delay = _FETCH_BASE_DELAY * (2 ** (attempt - 1))
            logger.warning(
                "Transient source DB error fetching documents after %s (attempt %d/%d). Retrying in %ds…",
                last_doc_id,
                attempt,
                _FETCH_MAX_RETRIES,
                delay,
                exc_info=True,
            )
            dispose_engine(settings.source_dsn)
            time.sleep(delay)
    return []  # unreachable, satisfies type checker


def _get_existing_summary_doc_ids(schema: str) -> set[str]:
    """Return the set of document_ids that already have summaries in the target schema."""
    tbl = _get_summary_table(schema)
    stmt = select(tbl.c.document_id)

    engine = get_engine(settings.target_dsn)
    try:
        with Session(engine) as session:
            rows = session.execute(stmt).all()
        return {row.document_id for row in rows}
    except Exception:
        # Table may not exist yet — no summaries to skip.
        logger.debug("Could not query existing summaries in '%s' (table may not exist yet).", schema)
        return set()


# ── Chunk aggregation ─────────────────────────────────────────────────


def _read_and_aggregate_chunks(document_id: str) -> str | None:
    """Read all chunks for *document_id* from the source DB and aggregate text.

    Chunks are ordered by ``metadata->'loc'->'lines'->>'from'`` (the old
    CSAI line-number ordering) and joined with newlines.

    Returns ``None`` if no chunks or all text is empty.
    """
    tbl = _get_source_table()

    stmt = (
        select(tbl.c.text)
        .where(tbl.c.metadata["documentID"].astext == document_id)
        .order_by(func.cast(tbl.c.metadata["loc"]["lines"]["from"].astext, Integer).nullslast())
    )

    engine = get_engine(settings.source_dsn)
    with Session(engine) as session:
        rows = session.execute(stmt).all()

    if not rows:
        return None

    aggregated = "\n".join(row[0] for row in rows if row[0])
    return aggregated if aggregated.strip() else None


def _read_and_aggregate_chunks_with_retry(document_id: str) -> str | None:
    """Fetch and aggregate chunks with retry on transient DB errors."""
    for attempt in range(1, _FETCH_MAX_RETRIES + 1):
        try:
            return _read_and_aggregate_chunks(document_id)
        except OperationalError:
            if attempt == _FETCH_MAX_RETRIES:
                raise
            delay = _FETCH_BASE_DELAY * (2 ** (attempt - 1))
            logger.warning(
                "Transient source DB error reading chunks for %s (attempt %d/%d). Retrying in %ds…",
                document_id,
                attempt,
                _FETCH_MAX_RETRIES,
                delay,
                exc_info=True,
            )
            dispose_engine(settings.source_dsn)
            time.sleep(delay)
    return None  # unreachable, satisfies type checker


# ── Publish ───────────────────────────────────────────────────────────


def _publish_batch(celery_app, batch: SummaryBatch) -> None:  # noqa: ANN001
    """Send a summary batch to the Celery summary queue."""
    celery_app.send_task(
        "migration.summary_consumer.process_summary_batch",
        args=[batch.model_dump(mode="json")],
        queue=settings.summary_queue,
    )
    logger.debug(
        "Published summary batch → schema=%s  docs=%d",
        batch.target_schema,
        len(batch.documents),
    )


# ── Main producer loop ────────────────────────────────────────────────


def run_summary_producer(celery_app) -> None:  # noqa: ANN001
    """Run the summary backfill producer.

    Uses cursor-based pagination to iterate source documents without
    loading them all into memory:

    1. Load checkpoint (``last_id`` = last processed ``documentID``).
    2. Page through source documents ordered by ``documentID``.
    3. For each page, filter already-summarised docs via the target DB.
    4. Read and aggregate chunks per document from the source DB.
    5. Batch by target schema and publish to queue.
    6. Save checkpoint after each page.

    This approach handles millions of documents with constant memory.
    """
    engine = get_engine(settings.target_dsn)
    with Session(engine) as target_session:
        progress = load_progress(session=target_session, migration_id=_SUMMARY_MIGRATION_ID)
        if progress.status == "completed":
            logger.info("Summary backfill already completed — nothing to do.")
            return

        # Count total distinct documents for progress logging.
        total_docs = _count_source_documents()
        logger.info("Source table has %d distinct documents.", total_docs)

        if total_docs == 0:
            mark_completed(0, session=target_session, migration_id=_SUMMARY_MIGRATION_ID)
            return

        # Resume cursor from checkpoint.
        last_doc_id: str | None = progress.last_id
        migrated = progress.migrated
        published = 0
        skipped = 0

        while True:
            # Fetch next page of documents from source.
            page = _fetch_document_page_with_retry(last_doc_id, _DISCOVER_PAGE_SIZE)
            if not page:
                break

            # Group page by target schema for filtering.
            page_by_schema: dict[str, list[dict]] = {}
            for doc in page:
                meta = {"tenantID": doc["tenant_id"], "workspaceID": doc["workspace_id"]}
                schema = resolve_target_schema(meta)
                page_by_schema.setdefault(schema, []).append(doc)

            # Filter already-summarised docs and collect pending.
            batch_buffer: dict[str, list[SummarySourceDocument]] = {}

            for schema, docs in page_by_schema.items():
                existing_ids = _get_existing_summary_doc_ids(schema)

                for doc_info in docs:
                    if doc_info["document_id"] in existing_ids:
                        skipped += 1
                        continue

                    aggregated_text = _read_and_aggregate_chunks_with_retry(doc_info["document_id"])
                    if aggregated_text is None:
                        logger.debug("Skipping document %s — no content in source.", doc_info["document_id"])
                        skipped += 1
                        continue

                    summary_doc = SummarySourceDocument(
                        document_id=doc_info["document_id"],
                        workspace_id=doc_info["workspace_id"],
                        aggregated_text=aggregated_text,
                    )
                    batch_buffer.setdefault(schema, []).append(summary_doc)

                    # Flush when a schema's buffer reaches batch_size.
                    if len(batch_buffer[schema]) >= settings.summary_batch_size:
                        batch = SummaryBatch(target_schema=schema, documents=batch_buffer.pop(schema))
                        if settings.dry_run:
                            logger.info("[DRY-RUN] Would publish %d docs → schema '%s'", len(batch.documents), schema)
                        else:
                            _publish_batch(celery_app, batch)
                        published += len(batch.documents)

            # Flush remaining documents from this page.
            for schema, docs in batch_buffer.items():
                if docs:
                    batch = SummaryBatch(target_schema=schema, documents=docs)
                    if settings.dry_run:
                        logger.info("[DRY-RUN] Would publish %d docs → schema '%s'", len(batch.documents), schema)
                    else:
                        _publish_batch(celery_app, batch)
                    published += len(batch.documents)

            # Advance cursor to last document in page.
            last_doc_id = page[-1]["document_id"]
            migrated += len(page)

            # Checkpoint after each page.
            progress = MigrationProgress(
                last_id=last_doc_id,
                status="in_progress",
                total_rows=total_docs,
                migrated=migrated,
            )
            save_progress(progress, session=target_session, migration_id=_SUMMARY_MIGRATION_ID)
            logger.info(
                "Summary backfill progress: %d / %d documents scanned, %d published, %d skipped",
                migrated,
                total_docs,
                published,
                skipped,
            )

        mark_completed(migrated, session=target_session, migration_id=_SUMMARY_MIGRATION_ID)

    logger.info("Summary backfill producer finished — %d published, %d skipped.", published, skipped)
