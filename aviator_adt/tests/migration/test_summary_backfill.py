"""Tests for the summary backfill producer and consumer."""

from unittest.mock import MagicMock, patch

from aviator.models import DocumentSummary
from migration.models import SummaryBatch, SummarySourceDocument

# ── Consumer tests ────────────────────────────────────────────────────


class TestProcessSummaryBatch:
    """Tests for the process_summary_batch Celery task."""

    def _make_batch(self, documents=None, schema="public"):
        return SummaryBatch(
            target_schema=schema,
            documents=documents or [],
        ).model_dump(mode="json")

    def test_generates_summaries_for_batch(self):
        """Test that the consumer generates summaries for each document in the batch."""
        from migration.summary_consumer import process_summary_batch

        batch = self._make_batch(
            documents=[
                SummarySourceDocument(document_id="doc-1", workspace_id="ws-1", aggregated_text="Text one."),
                SummarySourceDocument(document_id="doc-2", workspace_id="ws-1", aggregated_text="Text two."),
            ],
        )

        summaries = [
            DocumentSummary(title="Title", summary="Summary text"),
            DocumentSummary(title="Title", summary="Summary text"),
        ]

        with (
            patch("migration.schema_manager.ensure_schema"),
            patch(
                "migration.summary_consumer.generate_summaries_batch",
                return_value=summaries,
            ) as mock_gen_batch,
            patch(
                "migration.summary_consumer.bulk_upsert_workspace_documents_with_summary",
                return_value=2,
            ) as mock_bulk_upsert,
            patch("migration.summary_consumer.EmbeddingsRegistry") as mock_embed_reg,
        ):
            mock_embed_svc = MagicMock()
            mock_embed_svc.embed_documents.return_value = [[0.1, 0.2, 0.3], [0.1, 0.2, 0.3]]
            mock_embed_reg.get_embeddings.return_value = mock_embed_svc

            result = process_summary_batch(batch)

            assert result["succeeded"] == 2
            assert result["failed"] == 0
            assert result["processed"] == 2
            assert result["schema"] == "public"
            mock_gen_batch.assert_called_once()
            mock_bulk_upsert.assert_called_once()
            # embed_documents called once with both titles
            mock_embed_svc.embed_documents.assert_called_once_with(["Title", "Title"])

    def test_empty_batch_returns_zero(self):
        """Test that an empty batch returns zero counts."""
        from migration.summary_consumer import process_summary_batch

        batch = self._make_batch(documents=[])
        result = process_summary_batch(batch)
        assert result == {"schema": "public", "processed": 0, "succeeded": 0, "failed": 0}

    def test_continues_on_per_document_failure(self):
        """Test that a failure on one document doesn't stop processing of others."""
        from migration.summary_consumer import process_summary_batch

        batch = self._make_batch(
            documents=[
                SummarySourceDocument(document_id="ok-1", workspace_id="ws", aggregated_text="Good."),
                SummarySourceDocument(document_id="fail", workspace_id="ws", aggregated_text="Bad."),
                SummarySourceDocument(document_id="ok-2", workspace_id="ws", aggregated_text="Also good."),
            ],
        )

        # Second doc fails (None from generate_summaries_batch)
        summaries = [
            DocumentSummary(title="T", summary="S"),
            None,
            DocumentSummary(title="T", summary="S"),
        ]

        with (
            patch("migration.schema_manager.ensure_schema"),
            patch("migration.summary_consumer.generate_summaries_batch", return_value=summaries),
            patch(
                "migration.summary_consumer.bulk_upsert_workspace_documents_with_summary",
                return_value=2,
            ),
            patch("migration.summary_consumer.EmbeddingsRegistry") as mock_embed_reg,
        ):
            mock_embed_svc = MagicMock()
            mock_embed_svc.embed_documents.return_value = [[0.1], [0.1]]
            mock_embed_reg.get_embeddings.return_value = mock_embed_svc

            result = process_summary_batch(batch)

            assert result["succeeded"] == 2
            assert result["failed"] == 1
            assert result["processed"] == 3

    def test_title_embedding_failure_still_stores_summaries(self):
        """Test that title embedding failure doesn't prevent summary storage."""
        from migration.summary_consumer import process_summary_batch

        batch = self._make_batch(
            documents=[
                SummarySourceDocument(document_id="doc-1", workspace_id="ws-1", aggregated_text="Content."),
            ],
        )

        summaries = [DocumentSummary(title="Title", summary="Summary")]

        with (
            patch("migration.schema_manager.ensure_schema"),
            patch("migration.summary_consumer.generate_summaries_batch", return_value=summaries),
            patch(
                "migration.summary_consumer.bulk_upsert_workspace_documents_with_summary",
                return_value=1,
            ) as mock_bulk_upsert,
            patch("migration.summary_consumer.EmbeddingsRegistry") as mock_embed_reg,
        ):
            mock_embed_reg.get_embeddings.side_effect = RuntimeError("Embedding service down")

            result = process_summary_batch(batch)

            assert result["succeeded"] == 1
            assert result["failed"] == 0
            mock_bulk_upsert.assert_called_once()
            # title_embeddings should be None since embedding failed
            upsert_rows = mock_bulk_upsert.call_args[1]["rows"]
            assert upsert_rows[0]["title_embeddings"] is None

    def test_bulk_upsert_failure_counts_all_as_failed(self):
        """Test that a bulk upsert failure marks all docs as failed."""
        from migration.summary_consumer import process_summary_batch

        batch = self._make_batch(
            documents=[
                SummarySourceDocument(document_id="doc-1", workspace_id="ws-1", aggregated_text="Text."),
                SummarySourceDocument(document_id="doc-2", workspace_id="ws-1", aggregated_text="Text."),
            ],
        )

        summaries = [
            DocumentSummary(title="T1", summary="S1"),
            DocumentSummary(title="T2", summary="S2"),
        ]

        with (
            patch("migration.schema_manager.ensure_schema"),
            patch("migration.summary_consumer.generate_summaries_batch", return_value=summaries),
            patch(
                "migration.summary_consumer.bulk_upsert_workspace_documents_with_summary",
                side_effect=RuntimeError("DB down"),
            ),
            patch("migration.summary_consumer.EmbeddingsRegistry") as mock_embed_reg,
        ):
            mock_embed_svc = MagicMock()
            mock_embed_svc.embed_documents.return_value = [[0.1], [0.2]]
            mock_embed_reg.get_embeddings.return_value = mock_embed_svc

            result = process_summary_batch(batch)

            assert result["succeeded"] == 0
            assert result["failed"] == 2

    def test_empty_title_skipped_for_embeddings(self):
        """Test that documents with empty titles are not sent to embed_documents."""
        from migration.summary_consumer import process_summary_batch

        batch = self._make_batch(
            documents=[
                SummarySourceDocument(document_id="doc-1", workspace_id="ws-1", aggregated_text="Text."),
                SummarySourceDocument(document_id="doc-2", workspace_id="ws-1", aggregated_text="Text."),
            ],
        )

        summaries = [
            DocumentSummary(title="Has Title", summary="S1"),
            DocumentSummary(title="", summary="S2"),
        ]

        with (
            patch("migration.schema_manager.ensure_schema"),
            patch("migration.summary_consumer.generate_summaries_batch", return_value=summaries),
            patch(
                "migration.summary_consumer.bulk_upsert_workspace_documents_with_summary",
                return_value=2,
            ),
            patch("migration.summary_consumer.EmbeddingsRegistry") as mock_embed_reg,
        ):
            mock_embed_svc = MagicMock()
            mock_embed_svc.embed_documents.return_value = [[0.1, 0.2]]
            mock_embed_reg.get_embeddings.return_value = mock_embed_svc

            result = process_summary_batch(batch)

            assert result["succeeded"] == 2
            # Only 1 title embedded (the non-empty one)
            mock_embed_svc.embed_documents.assert_called_once_with(["Has Title"])

    def test_all_summaries_fail_returns_all_failed(self):
        """Test that if all summaries fail, result shows all as failed."""
        from migration.summary_consumer import process_summary_batch

        batch = self._make_batch(
            documents=[
                SummarySourceDocument(document_id="doc-1", workspace_id="ws-1", aggregated_text="Text."),
                SummarySourceDocument(document_id="doc-2", workspace_id="ws-1", aggregated_text="Text."),
            ],
        )

        # All summaries failed
        summaries = [None, None]

        with (
            patch("migration.schema_manager.ensure_schema"),
            patch("migration.summary_consumer.generate_summaries_batch", return_value=summaries),
        ):
            result = process_summary_batch(batch)

            assert result["succeeded"] == 0
            assert result["failed"] == 2
            assert result["processed"] == 2


# ── Producer tests ────────────────────────────────────────────────────


class TestSummaryProducer:
    """Tests for summary_producer discovery and aggregation helpers."""

    def test_fetch_document_page(self):
        """Test that _fetch_document_page returns docs from old metadata format."""
        from migration.summary_producer import _fetch_document_page

        mock_rows = [
            MagicMock(document_id="doc-1", workspace_id="ws-1", tenant_id="t1"),
            MagicMock(document_id="doc-2", workspace_id="ws-2", tenant_id=""),
        ]

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.execute.return_value.all.return_value = mock_rows

        with (
            patch("migration.summary_producer.get_engine"),
            patch("migration.summary_producer.Session", return_value=mock_session),
        ):
            docs = _fetch_document_page(None, 500)

        assert len(docs) == 2
        assert docs[0]["document_id"] == "doc-1"
        assert docs[0]["workspace_id"] == "ws-1"
        assert docs[0]["tenant_id"] == "t1"
        assert docs[1]["tenant_id"] == ""

    def test_fetch_document_page_with_cursor(self):
        """Test that pagination resumes from last_doc_id."""
        from migration.summary_producer import _fetch_document_page

        mock_rows = [
            MagicMock(document_id="doc-3", workspace_id="ws-1", tenant_id=""),
        ]

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.execute.return_value.all.return_value = mock_rows

        with (
            patch("migration.summary_producer.get_engine"),
            patch("migration.summary_producer.Session", return_value=mock_session),
        ):
            docs = _fetch_document_page("doc-2", 500)

        assert len(docs) == 1
        assert docs[0]["document_id"] == "doc-3"

    def test_count_source_documents(self):
        """Test that _count_source_documents returns count of distinct documents."""
        from migration.summary_producer import _count_source_documents

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.execute.return_value.scalar_one.return_value = 42

        with (
            patch("migration.summary_producer.get_engine"),
            patch("migration.summary_producer.Session", return_value=mock_session),
        ):
            count = _count_source_documents()

        assert count == 42

    def test_read_and_aggregate_chunks(self):
        """Test that chunks are read and aggregated from the old DB format."""
        from migration.summary_producer import _read_and_aggregate_chunks

        mock_rows = [("Line one.",), ("Line two.",), ("Line three.",)]

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.execute.return_value.all.return_value = mock_rows

        with (
            patch("migration.summary_producer.get_engine"),
            patch("migration.summary_producer.Session", return_value=mock_session),
        ):
            result = _read_and_aggregate_chunks("doc-1")

        assert result == "Line one.\nLine two.\nLine three."

    def test_read_and_aggregate_chunks_empty(self):
        """Test that None is returned for empty chunks."""
        from migration.summary_producer import _read_and_aggregate_chunks

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.execute.return_value.all.return_value = []

        with (
            patch("migration.summary_producer.get_engine"),
            patch("migration.summary_producer.Session", return_value=mock_session),
        ):
            result = _read_and_aggregate_chunks("doc-1")

        assert result is None

    def test_read_and_aggregate_chunks_whitespace_only(self):
        """Test that None is returned when all chunks are whitespace."""
        from migration.summary_producer import _read_and_aggregate_chunks

        mock_rows = [("  ",), ("",), (None,)]

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.execute.return_value.all.return_value = mock_rows

        with (
            patch("migration.summary_producer.get_engine"),
            patch("migration.summary_producer.Session", return_value=mock_session),
        ):
            result = _read_and_aggregate_chunks("doc-1")

        assert result is None

    def test_get_existing_summary_doc_ids(self):
        """Test that existing summary doc IDs are fetched from target DB."""
        from migration.summary_producer import _get_existing_summary_doc_ids

        mock_rows = [MagicMock(document_id="doc-1"), MagicMock(document_id="doc-2")]

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.execute.return_value.all.return_value = mock_rows

        with (
            patch("migration.summary_producer.get_engine"),
            patch("migration.summary_producer.Session", return_value=mock_session),
        ):
            ids = _get_existing_summary_doc_ids("public")

        assert ids == {"doc-1", "doc-2"}

    def test_get_existing_summary_doc_ids_table_missing(self):
        """Test that missing table returns empty set (not an error)."""
        from migration.summary_producer import _get_existing_summary_doc_ids

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.execute.side_effect = Exception("relation does not exist")

        with (
            patch("migration.summary_producer.get_engine"),
            patch("migration.summary_producer.Session", return_value=mock_session),
        ):
            ids = _get_existing_summary_doc_ids("public")

        assert ids == set()

    def test_run_summary_producer_already_completed(self):
        """Test that producer exits early when checkpoint says completed."""
        from migration.models import MigrationProgress
        from migration.summary_producer import run_summary_producer

        mock_celery = MagicMock()
        completed_progress = MigrationProgress(status="completed", total_rows=10, migrated=10)

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        with (
            patch("migration.summary_producer.get_engine"),
            patch("migration.summary_producer.Session", return_value=mock_session),
            patch("migration.summary_producer.load_progress", return_value=completed_progress),
        ):
            run_summary_producer(mock_celery)

        mock_celery.send_task.assert_not_called()

    def test_run_summary_producer_no_documents(self):
        """Test that producer completes when no documents found in source."""
        from migration.models import MigrationProgress
        from migration.summary_producer import run_summary_producer

        mock_celery = MagicMock()
        fresh_progress = MigrationProgress()

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        with (
            patch("migration.summary_producer.get_engine"),
            patch("migration.summary_producer.Session", return_value=mock_session),
            patch("migration.summary_producer.load_progress", return_value=fresh_progress),
            patch("migration.summary_producer._count_source_documents", return_value=0),
            patch("migration.summary_producer.mark_completed") as mock_mark,
        ):
            run_summary_producer(mock_celery)

        mock_mark.assert_called_once_with(0, session=mock_session, migration_id="summary_backfill")

    def test_run_summary_producer_paginates_and_publishes(self):
        """Test that producer pages through source docs and publishes batches."""
        from migration.models import MigrationProgress
        from migration.summary_producer import run_summary_producer

        mock_celery = MagicMock()
        fresh_progress = MigrationProgress()

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        # Two documents in a single page, then empty page to stop.
        page1 = [
            {"document_id": "doc-1", "workspace_id": "ws-1", "tenant_id": ""},
            {"document_id": "doc-2", "workspace_id": "ws-1", "tenant_id": ""},
        ]

        with (
            patch("migration.summary_producer.get_engine"),
            patch("migration.summary_producer.Session", return_value=mock_session),
            patch("migration.summary_producer.load_progress", return_value=fresh_progress),
            patch("migration.summary_producer._count_source_documents", return_value=2),
            patch("migration.summary_producer._fetch_document_page_with_retry", side_effect=[page1, []]),
            patch("migration.summary_producer._get_existing_summary_doc_ids", return_value=set()),
            patch("migration.summary_producer._read_and_aggregate_chunks_with_retry", return_value="Content."),
            patch("migration.summary_producer.resolve_target_schema", return_value="public"),
            patch("migration.summary_producer.save_progress"),
            patch("migration.summary_producer.mark_completed") as mock_mark,
        ):
            run_summary_producer(mock_celery)

        # Should have published at least one batch.
        assert mock_celery.send_task.call_count >= 1
        mock_mark.assert_called_once()

    def test_run_summary_producer_skips_existing_summaries(self):
        """Test that documents with existing summaries are skipped."""
        from migration.models import MigrationProgress
        from migration.summary_producer import run_summary_producer

        mock_celery = MagicMock()
        fresh_progress = MigrationProgress()

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        page1 = [
            {"document_id": "doc-done", "workspace_id": "ws-1", "tenant_id": ""},
            {"document_id": "doc-new", "workspace_id": "ws-1", "tenant_id": ""},
        ]

        with (
            patch("migration.summary_producer.get_engine"),
            patch("migration.summary_producer.Session", return_value=mock_session),
            patch("migration.summary_producer.load_progress", return_value=fresh_progress),
            patch("migration.summary_producer._count_source_documents", return_value=2),
            patch("migration.summary_producer._fetch_document_page_with_retry", side_effect=[page1, []]),
            patch("migration.summary_producer._get_existing_summary_doc_ids", return_value={"doc-done"}),
            patch("migration.summary_producer._read_and_aggregate_chunks_with_retry", return_value="Content."),
            patch("migration.summary_producer.resolve_target_schema", return_value="public"),
            patch("migration.summary_producer.save_progress"),
            patch("migration.summary_producer.mark_completed"),
        ):
            run_summary_producer(mock_celery)

        # Only doc-new should be published (1 doc in batch).
        assert mock_celery.send_task.call_count == 1
        batch_arg = (
            mock_celery.send_task.call_args[1]["args"][0]
            if "args" in mock_celery.send_task.call_args[1]
            else mock_celery.send_task.call_args[0][1][0]
        )
        assert len(batch_arg["documents"]) == 1
        assert batch_arg["documents"][0]["document_id"] == "doc-new"

    def test_run_summary_producer_resumes_from_checkpoint(self):
        """Test that producer resumes from last_id checkpoint."""
        from migration.models import MigrationProgress
        from migration.summary_producer import run_summary_producer

        mock_celery = MagicMock()
        # Resumed progress with last_id set.
        resumed_progress = MigrationProgress(last_id="doc-5", migrated=5, total_rows=10)

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        page1 = [
            {"document_id": "doc-6", "workspace_id": "ws-1", "tenant_id": ""},
        ]

        with (
            patch("migration.summary_producer.get_engine"),
            patch("migration.summary_producer.Session", return_value=mock_session),
            patch("migration.summary_producer.load_progress", return_value=resumed_progress),
            patch("migration.summary_producer._count_source_documents", return_value=10),
            patch("migration.summary_producer._fetch_document_page_with_retry", side_effect=[page1, []]) as mock_fetch,
            patch("migration.summary_producer._get_existing_summary_doc_ids", return_value=set()),
            patch("migration.summary_producer._read_and_aggregate_chunks_with_retry", return_value="Content."),
            patch("migration.summary_producer.resolve_target_schema", return_value="public"),
            patch("migration.summary_producer.save_progress"),
            patch("migration.summary_producer.mark_completed"),
        ):
            run_summary_producer(mock_celery)

        # First fetch should start from "doc-5" (the checkpoint last_id).
        first_call_args = mock_fetch.call_args_list[0]
        assert first_call_args[0][0] == "doc-5"


# ── Checkpoint parameterisation tests ─────────────────────────────────


class TestCheckpointParameterisation:
    """Test that checkpoint functions accept a custom migration_id."""

    def test_save_and_load_with_custom_id(self):
        """Test that custom migration_id is forwarded to DB operations."""
        from migration.checkpoint import load_progress

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.execute.return_value.scalar_one_or_none.return_value = None

        with (
            patch("migration.checkpoint.get_engine"),
            patch("migration.checkpoint.ensure_state_table"),
        ):
            progress = load_progress(session=mock_session, migration_id="summary_backfill")

        assert progress.status == "in_progress"
        assert progress.migrated == 0

    def test_mark_completed_with_custom_id(self):
        """Test that mark_completed passes migration_id through."""
        from migration.checkpoint import mark_completed

        with patch("migration.checkpoint.save_progress") as mock_save:
            mark_completed(42, migration_id="summary_backfill")

        mock_save.assert_called_once()
        call_kwargs = mock_save.call_args[1]
        assert call_kwargs["migration_id"] == "summary_backfill"


# ── Model tests ───────────────────────────────────────────────────────


class TestSummaryModels:
    """Tests for the summary backfill Pydantic models."""

    def test_summary_source_document_serialisation(self):
        doc = SummarySourceDocument(
            document_id="doc-1",
            workspace_id="ws-1",
            aggregated_text="Hello world.",
        )
        data = doc.model_dump()
        assert data["document_id"] == "doc-1"
        assert data["aggregated_text"] == "Hello world."

    def test_summary_batch_serialisation(self):
        batch = SummaryBatch(
            target_schema="tenant_acme",
            documents=[
                SummarySourceDocument(document_id="d1", workspace_id="w1", aggregated_text="Text."),
            ],
        )
        data = batch.model_dump(mode="json")
        assert data["target_schema"] == "tenant_acme"
        assert len(data["documents"]) == 1

    def test_summary_batch_round_trip(self):
        batch = SummaryBatch(
            target_schema="public",
            documents=[
                SummarySourceDocument(document_id="d1", workspace_id="w1", aggregated_text="A."),
                SummarySourceDocument(document_id="d2", workspace_id="w1", aggregated_text="B."),
            ],
        )
        data = batch.model_dump(mode="json")
        restored = SummaryBatch.model_validate(data)
        assert len(restored.documents) == 2
        assert restored.documents[0].document_id == "d1"
