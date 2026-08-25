"""Tests for usage_tracking.recorder module."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aviator.services.usage_tracking.metrics import usage_transactions_total
from aviator.services.usage_tracking.recorder import record_transaction, record_transaction_sync


class TestRecordTransaction:
    """Tests for async record_transaction."""

    @pytest.mark.asyncio
    @patch("aviator.services.usage_tracking.recorder.settings")
    async def test_noop_when_disabled(self, mock_settings):
        mock_settings.usage_tracking_enabled = False

        labels = {"tenant_id": "t1", "transaction_type": "chat"}
        before = usage_transactions_total.labels(**labels)._value.get()

        await record_transaction(tenant_id="t1", transaction_type="chat")

        # Counter must not change when tracking is disabled
        assert usage_transactions_total.labels(**labels)._value.get() == before

    @pytest.mark.asyncio
    @patch("aviator.services.usage_tracking.recorder.settings")
    @patch("aviator.services.usage_tracking.recorder.usage_tracking_db")
    async def test_records_when_enabled(self, mock_db, mock_settings):
        mock_settings.usage_tracking_enabled = True
        mock_db.use_sync_fallback.return_value = False
        mock_db.ensure_tenant_schema = AsyncMock(return_value="public")

        # Set up mock session — must be MagicMock so that .begin() returns
        # a sync value (not a coroutine) that can be used as a context manager.
        mock_session = MagicMock()
        mock_session.add = MagicMock()
        mock_session.flush = AsyncMock()

        # execute() returns a result whose .scalar_one_or_none() returns None
        # (no existing tally row — triggers INSERT path)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        # begin() must return an async context manager
        mock_begin = MagicMock()
        mock_begin.__aenter__ = AsyncMock(return_value=None)
        mock_begin.__aexit__ = AsyncMock(return_value=False)
        mock_session.begin = MagicMock(return_value=mock_begin)

        # async_session() returns an async context manager yielding mock_session
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_db.async_session = MagicMock(return_value=mock_ctx)

        await record_transaction(
            tenant_id=None,
            transaction_type="chat",
            document_count=0,
            chunk_count=0,
        )

        mock_db.ensure_tenant_schema.assert_awaited_once_with(None)
        mock_db.async_session.assert_called_once_with("public")
        # Two add() calls: one for UsageTransaction, one for UsageDailyTally
        assert mock_session.add.call_count == 2
        # Prometheus counter must have been incremented
        assert usage_transactions_total.labels(tenant_id="default", transaction_type="chat")._value.get() > 0

    @pytest.mark.asyncio
    @patch("aviator.services.usage_tracking.recorder.settings")
    @patch("aviator.services.usage_tracking.recorder.usage_tracking_db")
    async def test_does_not_raise_on_error(self, mock_db, mock_settings):
        mock_settings.usage_tracking_enabled = True
        mock_db.use_sync_fallback.return_value = False
        mock_db.ensure_tenant_schema = AsyncMock(side_effect=RuntimeError("boom"))

        labels = {"tenant_id": "t1", "transaction_type": "chat"}
        before = usage_transactions_total.labels(**labels)._value.get()

        # Should not raise - errors are logged only
        await record_transaction(tenant_id="t1", transaction_type="chat")

        # Counter must not change when DB errors occur
        assert usage_transactions_total.labels(**labels)._value.get() == before

    @pytest.mark.asyncio
    @patch("aviator.services.usage_tracking.recorder.asyncio.to_thread", new_callable=AsyncMock)
    @patch("aviator.services.usage_tracking.recorder.settings")
    @patch("aviator.services.usage_tracking.recorder.usage_tracking_db")
    async def test_uses_sync_fallback_on_windows(self, mock_db, mock_settings, mock_to_thread):
        mock_settings.usage_tracking_enabled = True
        mock_db.use_sync_fallback.return_value = True

        await record_transaction(
            tenant_id="t1",
            transaction_type="chat",
            document_count=1,
            chunk_count=2,
            input_tokens=0,
            output_tokens=0,
            llm_total_requests=0,
            metadata={"source": "test"},
        )

        mock_to_thread.assert_awaited_once_with(
            record_transaction_sync,
            "t1",
            "chat",
            1,
            2,
            0,
            0,
            0,
            {"source": "test"},
        )


class TestRecordTransactionSync:
    """Tests for sync record_transaction_sync."""

    @patch("aviator.services.usage_tracking.recorder.settings")
    def test_noop_when_disabled(self, mock_settings):
        mock_settings.usage_tracking_enabled = False
        record_transaction_sync(tenant_id="t1", transaction_type="embedding_add")

    @patch("aviator.services.usage_tracking.recorder.settings")
    @patch("aviator.services.usage_tracking.recorder.usage_tracking_db")
    def test_records_when_enabled(self, mock_db, mock_settings):
        mock_settings.usage_tracking_enabled = True
        mock_db.ensure_tenant_schema_sync.return_value = "public"

        # Set up mock session as a sync context manager
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        mock_db.sync_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.sync_session.return_value.__exit__ = MagicMock(return_value=False)

        record_transaction_sync(
            tenant_id=None,
            transaction_type="embedding_add",
            document_count=1,
            chunk_count=10,
        )

        mock_db.ensure_tenant_schema_sync.assert_called_once_with(None)
        mock_db.sync_session.assert_called_once_with("public")
        # Two add() calls: one for UsageTransaction, one for UsageDailyTally
        assert mock_session.add.call_count == 2
        # Prometheus counter must have been incremented
        assert usage_transactions_total.labels(tenant_id="default", transaction_type="embedding_add")._value.get() > 0

    @patch("aviator.services.usage_tracking.recorder.settings")
    @patch("aviator.services.usage_tracking.recorder.usage_tracking_db")
    def test_does_not_raise_on_error(self, mock_db, mock_settings):
        mock_settings.usage_tracking_enabled = True
        mock_db.ensure_tenant_schema_sync.side_effect = RuntimeError("boom")

        # Should not raise
        record_transaction_sync(tenant_id="t1", transaction_type="chat")
