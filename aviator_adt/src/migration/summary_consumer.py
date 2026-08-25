"""Summary backfill consumer — Celery tasks that generate summaries via LLM.

Tasks are registered on the main aviator Celery app so the standard
aviator worker can process summary batches.  The producer sends tasks
to the ``summary_queue`` (default ``csai-adt-summary``).

Processing is batched across three phases for efficiency:

1. **LLM summaries** — concurrent via ``.batch()`` (thread-pool).
2. **Title embeddings** — single ``embed_documents()`` call for all titles.
3. **Database upsert** — single ``INSERT … ON CONFLICT DO UPDATE`` transaction.
"""

from __future__ import annotations

import logging

from celery import Task

from aviator.celery import celery
from aviator.database.summary_operations import bulk_upsert_workspace_documents_with_summary
from aviator.services.embeddings import EmbeddingsRegistry
from aviator.services.summary import generate_summaries_batch
from migration.models import SummaryBatch

logger = logging.getLogger(__name__)


class SummaryTaskWithRetry(Task):
    """Celery task base class with exponential-backoff retries for summary tasks."""

    autoretry_for = (Exception,)
    retry_kwargs = {"max_retries": 7}
    retry_backoff = True


@celery.task(bind=True, base=SummaryTaskWithRetry, name="migration.summary_consumer.process_summary_batch")
def process_summary_batch(self: Task, batch_dict: dict) -> dict:  # noqa: ARG001
    """Generate summaries for a batch of pre-aggregated documents.

    Each document in the batch already has its chunks aggregated by the
    producer.  Processing is split into three batched phases:

    1. Generate summaries concurrently via :func:`generate_summaries_batch`.
    2. Embed all titles in a single API call.
    3. Bulk-upsert all results into the database in one transaction.

    Returns a dict with processing counts.
    """
    batch = SummaryBatch.model_validate(batch_dict)

    logger.info(
        "Processing summary batch: schema=%s  docs=%d",
        batch.target_schema,
        len(batch.documents),
    )

    if not batch.documents:
        return {"schema": batch.target_schema, "processed": 0, "succeeded": 0, "failed": 0}

    # Ensure target schema and tables exist.
    from migration.schema_manager import ensure_schema

    ensure_schema(batch.target_schema)

    # ── Phase 1: Concurrent LLM summary generation ────────────────────
    contents = [doc.aggregated_text for doc in batch.documents]
    summaries = generate_summaries_batch(contents)

    # Collect successful results with their document metadata.
    succeeded_rows: list[dict] = []
    failed = 0
    for doc, summary in zip(batch.documents, summaries, strict=True):
        if summary is None:
            failed += 1
            logger.error("Summary generation failed for document_id=%s", doc.document_id)
            continue
        title = summary.title.strip() if summary.title else ""
        succeeded_rows.append(
            {
                "document_id": doc.document_id,
                "summary": summary.summary,
                "title": title,
                "title_embeddings": None,
            }
        )

    # ── Phase 2: Batch title embeddings ───────────────────────────────
    titles_with_indices = [(i, row["title"]) for i, row in enumerate(succeeded_rows) if row["title"]]
    if titles_with_indices:
        try:
            embeddings_service = EmbeddingsRegistry.get_embeddings()
            title_texts = [t for _, t in titles_with_indices]
            embeddings_list = embeddings_service.embed_documents(title_texts)
            for (row_idx, _), embedding in zip(titles_with_indices, embeddings_list, strict=True):
                succeeded_rows[row_idx]["title_embeddings"] = embedding
        except Exception:
            logger.exception("Batch title embedding failed — summaries will be stored without title embeddings.")

    # ── Phase 3: Bulk database upsert ─────────────────────────────────
    if succeeded_rows:
        try:
            bulk_upsert_workspace_documents_with_summary(
                rows=succeeded_rows,
                schema_name=batch.target_schema,
            )
        except Exception:
            # If bulk upsert fails entirely, count all as failed.
            logger.exception("Bulk upsert failed for schema=%s", batch.target_schema)
            failed += len(succeeded_rows)
            succeeded_rows = []

    succeeded = len(succeeded_rows)

    logger.info(
        "Summary batch complete: schema=%s  succeeded=%d  failed=%d",
        batch.target_schema,
        succeeded,
        failed,
    )

    return {
        "schema": batch.target_schema,
        "processed": len(batch.documents),
        "succeeded": succeeded,
        "failed": failed,
    }
