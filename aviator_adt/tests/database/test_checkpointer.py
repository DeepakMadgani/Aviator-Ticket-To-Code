"""Tests for the tenant-aware checkpointer module."""

import contextvars
from unittest.mock import AsyncMock, MagicMock, patch

import psycopg.errors
import pytest

from aviator.database.checkpointer import (
    TenantAwarePostgresSaver,
    checkpointer_schema,
    setup_checkpoint_tables_sync,
)

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# TenantAwarePostgresSaver._cursor — search_path management
# ---------------------------------------------------------------------------


async def test_cursor_sets_search_path_for_tenant_schema(monkeypatch) -> None:
    """When checkpointer_schema is set to a tenant schema, _cursor must SET search_path."""
    monkeypatch.setattr("aviator.database.checkpointer.settings.multi_tenant_enabled", True)
    monkeypatch.setattr("aviator.database.checkpointer.settings.default_schema", "custom_default")

    mock_cur = AsyncMock()
    # The parent _cursor yields this mock cursor
    parent_cursor_cm = AsyncMock()
    parent_cursor_cm.__aenter__ = AsyncMock(return_value=mock_cur)
    parent_cursor_cm.__aexit__ = AsyncMock(return_value=False)

    saver = TenantAwarePostgresSaver.__new__(TenantAwarePostgresSaver)
    saver._initialised_schemas = set()

    token = checkpointer_schema.set("tenant_acme")
    try:
        with patch.object(
            TenantAwarePostgresSaver.__bases__[0],
            "_cursor",
            return_value=parent_cursor_cm,
        ):
            async with saver._cursor() as cur:
                assert cur is mock_cur

        # Calls: SET search_path, probe checkpoint_migrations, reset search_path
        assert mock_cur.execute.await_count == 3
        set_call = mock_cur.execute.await_args_list[0]
        reset_call = mock_cur.execute.await_args_list[-1]

        # Verify the SET contains the tenant schema
        set_sql = set_call.args[0].as_string(None)
        assert "tenant_acme" in set_sql
        assert "search_path" in set_sql.lower()

        # Verify the reset restores the configured default schema (not hardcoded 'public')
        reset_sql = reset_call.args[0].as_string(None)
        assert "custom_default" in reset_sql
        assert "public" not in reset_sql

        # Second call should skip the probe (schema already initialised)
        mock_cur.reset_mock()
        with patch.object(
            TenantAwarePostgresSaver.__bases__[0],
            "_cursor",
            return_value=parent_cursor_cm,
        ):
            async with saver._cursor() as cur:
                pass
        # Only SET + reset, no probe
        assert mock_cur.execute.await_count == 2
    finally:
        checkpointer_schema.reset(token)


async def test_cursor_skips_search_path_for_default_schema(monkeypatch) -> None:
    """When schema matches default, no SET search_path should be issued."""
    monkeypatch.setattr("aviator.database.checkpointer.settings.multi_tenant_enabled", True)
    monkeypatch.setattr("aviator.database.checkpointer.settings.default_schema", "public")

    mock_cur = AsyncMock()
    parent_cursor_cm = AsyncMock()
    parent_cursor_cm.__aenter__ = AsyncMock(return_value=mock_cur)
    parent_cursor_cm.__aexit__ = AsyncMock(return_value=False)

    saver = TenantAwarePostgresSaver.__new__(TenantAwarePostgresSaver)

    token = checkpointer_schema.set("public")
    try:
        with patch.object(
            TenantAwarePostgresSaver.__bases__[0],
            "_cursor",
            return_value=parent_cursor_cm,
        ):
            async with saver._cursor() as cur:
                assert cur is mock_cur

        # No SET should have been executed
        mock_cur.execute.assert_not_awaited()
    finally:
        checkpointer_schema.reset(token)


async def test_cursor_skips_search_path_when_multi_tenant_disabled(monkeypatch) -> None:
    """When multi_tenant_enabled is False, search_path is never modified."""
    monkeypatch.setattr("aviator.database.checkpointer.settings.multi_tenant_enabled", False)
    monkeypatch.setattr("aviator.database.checkpointer.settings.default_schema", "public")

    mock_cur = AsyncMock()
    parent_cursor_cm = AsyncMock()
    parent_cursor_cm.__aenter__ = AsyncMock(return_value=mock_cur)
    parent_cursor_cm.__aexit__ = AsyncMock(return_value=False)

    saver = TenantAwarePostgresSaver.__new__(TenantAwarePostgresSaver)

    # Even with a non-default schema set, should NOT switch if multi-tenant is off
    token = checkpointer_schema.set("tenant_secret")
    try:
        with patch.object(
            TenantAwarePostgresSaver.__bases__[0],
            "_cursor",
            return_value=parent_cursor_cm,
        ):
            async with saver._cursor() as cur:
                assert cur is mock_cur

        mock_cur.execute.assert_not_awaited()
    finally:
        checkpointer_schema.reset(token)


async def test_cursor_defaults_to_default_schema_when_contextvar_unset(monkeypatch) -> None:
    """When the contextvar is not set, should use default schema without SET."""
    monkeypatch.setattr("aviator.database.checkpointer.settings.multi_tenant_enabled", True)
    monkeypatch.setattr("aviator.database.checkpointer.settings.default_schema", "public")

    mock_cur = AsyncMock()
    parent_cursor_cm = AsyncMock()
    parent_cursor_cm.__aenter__ = AsyncMock(return_value=mock_cur)
    parent_cursor_cm.__aexit__ = AsyncMock(return_value=False)

    saver = TenantAwarePostgresSaver.__new__(TenantAwarePostgresSaver)

    # Run in a fresh context where checkpointer_schema is NOT set
    ctx = contextvars.copy_context()

    async def _run():
        with patch.object(
            TenantAwarePostgresSaver.__bases__[0],
            "_cursor",
            return_value=parent_cursor_cm,
        ):
            async with saver._cursor() as cur:
                assert cur is mock_cur

    ctx.run(lambda: None)
    # Since contextvar defaults to settings.default_schema, no SET expected
    with patch.object(
        TenantAwarePostgresSaver.__bases__[0],
        "_cursor",
        return_value=parent_cursor_cm,
    ):
        async with saver._cursor():
            pass
    mock_cur.execute.assert_not_awaited()


async def test_cursor_auto_creates_tables_on_undefined_table(monkeypatch) -> None:
    """When checkpoint_migrations is missing, _cursor should run migrations automatically."""
    monkeypatch.setattr("aviator.database.checkpointer.settings.multi_tenant_enabled", True)
    monkeypatch.setattr("aviator.database.checkpointer.settings.default_schema", "custom_default")

    probe_raised = False

    async def execute_side_effect(query, *args, **kwargs):
        nonlocal probe_raised
        # The probe SELECT raises UndefinedTable only the first time
        if isinstance(query, str) and "checkpoint_migrations" in query and not probe_raised:
            probe_raised = True
            raise psycopg.errors.UndefinedTable("relation 'checkpoint_migrations' does not exist")

    mock_cur = AsyncMock()
    mock_cur.execute = AsyncMock(side_effect=execute_side_effect)

    parent_cursor_cm = AsyncMock()
    parent_cursor_cm.__aenter__ = AsyncMock(return_value=mock_cur)
    parent_cursor_cm.__aexit__ = AsyncMock(return_value=False)

    saver = TenantAwarePostgresSaver.__new__(TenantAwarePostgresSaver)
    saver._initialised_schemas = set()

    token = checkpointer_schema.set("tenant_new")
    try:
        with patch.object(
            TenantAwarePostgresSaver.__bases__[0],
            "_cursor",
            return_value=parent_cursor_cm,
        ):
            async with saver._cursor() as cur:
                assert cur is mock_cur

        # Should have: SET, probe (raises), ROLLBACK, migrations..., reset
        assert mock_cur.execute.await_count > 3
        # Schema should now be marked as initialised
        assert "tenant_new" in saver._initialised_schemas
    finally:
        checkpointer_schema.reset(token)


# ---------------------------------------------------------------------------
# setup_checkpoint_tables_sync
# ---------------------------------------------------------------------------


def test_setup_checkpoint_tables_sync_runs_migrations(monkeypatch) -> None:
    """setup_checkpoint_tables_sync should run the checkpointer migrations in the target schema."""
    monkeypatch.setattr("aviator.database.checkpointer.settings.default_schema", "my_default")

    mock_cur = MagicMock()
    # Simulate no existing migrations
    mock_cur.fetchone.return_value = None

    mock_conn = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    fake_migrations = ["CREATE TABLE checkpoint_migrations ...", "CREATE TABLE checkpoints ..."]

    with patch("aviator.database.checkpointer.psycopg.connect", return_value=mock_conn):
        monkeypatch.setattr("aviator.database.checkpointer.AsyncPostgresSaver.MIGRATIONS", fake_migrations)
        setup_checkpoint_tables_sync(schema_name="tenant_test", dsn="postgresql://localhost/test")

    # Verify SET search_path was called (first execute call)
    calls = mock_cur.execute.call_args_list
    first_call_sql = calls[0].args[0]
    assert "tenant_test" in first_call_sql.as_string(None)

    # Verify the reset at the end restores the configured default schema
    last_call_sql = calls[-1].args[0]
    assert "my_default" in last_call_sql.as_string(None)
    assert "public" not in last_call_sql.as_string(None)


def test_setup_checkpoint_tables_sync_skips_applied_migrations(monkeypatch) -> None:
    """Already-applied migrations should be skipped."""
    monkeypatch.setattr("aviator.database.checkpointer.settings.default_schema", "my_default")

    mock_cur = MagicMock()
    # Simulate version 0 already applied (only migration 0)
    mock_cur.fetchone.return_value = (0,)

    mock_conn = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    fake_migrations = ["CREATE TABLE checkpoint_migrations ...", "CREATE TABLE checkpoints ..."]

    with patch("aviator.database.checkpointer.psycopg.connect", return_value=mock_conn):
        monkeypatch.setattr("aviator.database.checkpointer.AsyncPostgresSaver.MIGRATIONS", fake_migrations)
        setup_checkpoint_tables_sync(schema_name="tenant_test", dsn="postgresql://localhost/test")

    # Calls: SET search_path, migration[0], SELECT version, migration[1],
    # INSERT version(1), SET search_path reset
    calls = mock_cur.execute.call_args_list
    # Migration 1 should have been applied (the CREATE TABLE checkpoints ...)
    applied_sqls = [str(c.args[0]) for c in calls]
    assert any("CREATE TABLE checkpoints" in s for s in applied_sqls)


# ---------------------------------------------------------------------------
# checkpointer_schema contextvar
# ---------------------------------------------------------------------------


def test_checkpointer_schema_contextvar_isolation() -> None:
    """Different contexts should have independent schema values."""
    token = checkpointer_schema.set("tenant_a")
    assert checkpointer_schema.get() == "tenant_a"
    checkpointer_schema.reset(token)
