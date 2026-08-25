"""Tests for usage_tracking.db module."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aviator.services.usage_tracking.db import UsageTrackingDB


class TestUsageTrackingDB:
    """Tests for UsageTrackingDB lifecycle behavior."""

    @pytest.mark.asyncio
    @patch("aviator.services.usage_tracking.db.asyncio.to_thread", new_callable=AsyncMock)
    async def test_initialize_uses_sync_fallback(self, mock_to_thread):
        db = UsageTrackingDB()
        db.use_sync_fallback = MagicMock(return_value=True)
        db._get_sync_engine = MagicMock()

        await db.initialize()

        mock_to_thread.assert_awaited_once_with(db._get_sync_engine)

    @pytest.mark.asyncio
    async def test_initialize_creates_async_engine_when_no_fallback(self):
        db = UsageTrackingDB()
        db.use_sync_fallback = MagicMock(return_value=False)
        mock_engine = MagicMock()
        mock_begin = MagicMock()
        mock_conn = MagicMock()
        mock_begin.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_begin.__aexit__ = AsyncMock(return_value=False)
        mock_engine.begin.return_value = mock_begin
        mock_conn.run_sync = AsyncMock()

        with patch("aviator.services.usage_tracking.db.create_async_engine", return_value=mock_engine):
            await db.initialize()

        assert db._async_engine is mock_engine
        mock_conn.run_sync.assert_awaited_once()


def _make_async_cm(value):
    """Create a minimal async context manager returning *value*."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=value)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _make_sync_cm(value):
    """Create a minimal sync context manager returning *value*."""
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=value)
    cm.__exit__ = MagicMock(return_value=False)
    return cm


class TestTenantSchemaResolution:
    """Tests that ensure_tenant_schema uses deterministic schema naming."""

    @pytest.mark.asyncio
    async def test_ensure_tenant_schema_derives_name_and_creates_schema(self, monkeypatch):
        db = UsageTrackingDB()
        db._async_engine = MagicMock()

        begin_conn = MagicMock()
        begin_conn.execute = AsyncMock()
        begin_conn.run_sync = AsyncMock()
        db._async_engine.begin.return_value = _make_async_cm(begin_conn)

        monkeypatch.setattr(
            "aviator.services.usage_tracking.db.sanitize_schema_name",
            lambda tenant_id: "tenant_acme",
        )

        schema = await db.ensure_tenant_schema("acme")

        assert schema == "tenant_acme"
        # Verify CreateSchema was called
        begin_conn.execute.assert_awaited_once()
        begin_conn.run_sync.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ensure_tenant_schema_returns_default_for_none(self):
        db = UsageTrackingDB()
        with patch("aviator.services.usage_tracking.db.settings") as mock_settings:
            mock_settings.default_schema = "public"
            schema = await db.ensure_tenant_schema(None)
        assert schema == "public"

    @pytest.mark.asyncio
    async def test_ensure_tenant_schema_caches_result(self, monkeypatch):
        db = UsageTrackingDB()
        db._async_engine = MagicMock()

        begin_conn = MagicMock()
        begin_conn.execute = AsyncMock()
        begin_conn.run_sync = AsyncMock()
        db._async_engine.begin.return_value = _make_async_cm(begin_conn)

        monkeypatch.setattr(
            "aviator.services.usage_tracking.db.sanitize_schema_name",
            lambda tenant_id: "tenant_acme",
        )

        await db.ensure_tenant_schema("acme")
        # Second call should use cache, not create again
        schema = await db.ensure_tenant_schema("acme")

        assert schema == "tenant_acme"
        assert db._async_engine.begin.call_count == 1

    def test_ensure_tenant_schema_sync_derives_name_and_creates_schema(self, monkeypatch):
        db = UsageTrackingDB()

        engine = MagicMock()
        db._sync_engine = engine

        begin_conn = MagicMock()
        begin_conn.execute = MagicMock()
        engine.begin.return_value = _make_sync_cm(begin_conn)

        monkeypatch.setattr(
            "aviator.services.usage_tracking.db.sanitize_schema_name",
            lambda tenant_id: "tenant_acme",
        )

        schema = db.ensure_tenant_schema_sync("acme")

        assert schema == "tenant_acme"
        begin_conn.execute.assert_called_once()

    def test_ensure_tenant_schema_sync_returns_default_for_none(self):
        db = UsageTrackingDB()
        with patch("aviator.services.usage_tracking.db.settings") as mock_settings:
            mock_settings.default_schema = "public"
            schema = db.ensure_tenant_schema_sync(None)
        assert schema == "public"
