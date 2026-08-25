"""Tests for checkpoint cleanup service."""

import datetime
from unittest.mock import AsyncMock, patch

import pytest

from aviator.services import checkpoint_cleanup as service

pytestmark = pytest.mark.anyio


async def test_get_stale_thread_ids_returns_ids() -> None:
    """It should map query rows to a thread_id list."""
    cutoff = datetime.datetime.now(tz=datetime.UTC)
    rows = [{"thread_id": "t-1"}, {"thread_id": "t-2"}]

    with patch("aviator.services.checkpoint_cleanup.fetch_all", new=AsyncMock(return_value=rows)) as mock_fetch_all:
        result = await service._get_stale_thread_ids(cutoff)

    assert result == ["t-1", "t-2"]
    mock_fetch_all.assert_awaited_once()


async def test_delete_checkpoint_batch_executes_all_three_deletes() -> None:
    """It should delete writes, blobs and checkpoints for the same thread-id batch."""
    ids = ["thread-a", "thread-b"]

    with patch("aviator.services.checkpoint_cleanup.execute_batch", new=AsyncMock(return_value=12)) as mock_exec:
        deleted = await service._delete_checkpoint_batch(ids)

    assert deleted == 12
    mock_exec.assert_awaited_once()
    statements = mock_exec.await_args.args[0]
    assert len(statements) == 3
    assert statements[0][0].startswith("DELETE FROM checkpoint_writes")
    assert statements[1][0].startswith("DELETE FROM checkpoint_blobs")
    assert statements[2][0].startswith("DELETE FROM checkpoints")
    assert statements[0][1] == (ids,)


async def test_cleanup_old_checkpoints_returns_zero_when_no_stale_threads(monkeypatch) -> None:
    """It should short-circuit when no schemas contain stale threads."""
    monkeypatch.setattr(service.settings, "checkpointer_retention_hours", 720)

    with (
        patch(
            "aviator.services.checkpoint_cleanup._get_checkpoint_schemas",
            new=AsyncMock(return_value=["public"]),
        ),
        patch(
            "aviator.services.checkpoint_cleanup._cleanup_schema",
            new=AsyncMock(return_value=0),
        ) as mock_cleanup,
    ):
        deleted = await service.cleanup_old_checkpoints()

    assert deleted == 0
    mock_cleanup.assert_awaited_once()


async def test_cleanup_old_checkpoints_iterates_all_schemas(monkeypatch) -> None:
    """It should iterate over all schemas returned by _get_checkpoint_schemas."""
    monkeypatch.setattr(service.settings, "checkpointer_retention_hours", 360)

    with (
        patch(
            "aviator.services.checkpoint_cleanup._get_checkpoint_schemas",
            new=AsyncMock(return_value=["public", "tenant_a", "tenant_b"]),
        ),
        patch(
            "aviator.services.checkpoint_cleanup._cleanup_schema",
            new=AsyncMock(side_effect=[3, 6, 0]),
        ) as mock_cleanup,
    ):
        deleted = await service.cleanup_old_checkpoints()

    assert deleted == 9
    assert mock_cleanup.await_count == 3
    schemas_called = [call.args[0] for call in mock_cleanup.await_args_list]
    assert schemas_called == ["public", "tenant_a", "tenant_b"]


async def test_get_checkpoint_schemas_default_only_when_single_tenant(monkeypatch) -> None:
    """When multi_tenant_enabled is False, only the default schema is returned."""
    monkeypatch.setattr(service.settings, "multi_tenant_enabled", False)
    monkeypatch.setattr(service.settings, "default_schema", "public")

    schemas = await service._get_checkpoint_schemas()

    assert schemas == ["public"]


async def test_get_checkpoint_schemas_includes_tenant_schemas(monkeypatch) -> None:
    """When multi_tenant_enabled is True, tenant schemas with checkpoints are included."""
    monkeypatch.setattr(service.settings, "multi_tenant_enabled", True)
    monkeypatch.setattr(service.settings, "default_schema", "public")
    monkeypatch.setattr(service.settings, "tenant_schema_prefix", "tenant_")

    with patch(
        "aviator.services.checkpoint_cleanup.fetch_all",
        new=AsyncMock(return_value=[{"table_schema": "tenant_x"}, {"table_schema": "tenant_y"}]),
    ):
        schemas = await service._get_checkpoint_schemas()

    assert schemas == ["public", "tenant_x", "tenant_y"]


async def test_cleanup_schema_returns_zero_when_no_stale_threads() -> None:
    """_cleanup_schema should return 0 when no stale threads exist in that schema."""
    cutoff = datetime.datetime.now(tz=datetime.UTC)

    mock_cur = AsyncMock()
    mock_cur.fetchall = AsyncMock(return_value=[])
    mock_cur.__aenter__ = AsyncMock(return_value=mock_cur)
    mock_cur.__aexit__ = AsyncMock(return_value=False)

    mock_conn = AsyncMock()
    mock_conn.cursor = lambda **kwargs: mock_cur
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)

    mock_pool = AsyncMock()
    mock_pool.connection = lambda: mock_conn

    with patch(
        "aviator.services.checkpoint_cleanup.PgConnectionPool.get_pool",
        new=AsyncMock(return_value=mock_pool),
    ):
        result = await service._cleanup_schema("public", cutoff)

    assert result == 0


# Legacy tests preserved for backwards compatibility with the original helper functions
async def test_cleanup_old_checkpoints_batches_thread_ids(monkeypatch) -> None:
    """It should aggregate deleted row counts across schemas."""
    monkeypatch.setattr(service.settings, "checkpointer_retention_hours", 360)

    with (
        patch(
            "aviator.services.checkpoint_cleanup._get_checkpoint_schemas",
            new=AsyncMock(return_value=["public"]),
        ),
        patch(
            "aviator.services.checkpoint_cleanup._cleanup_schema",
            new=AsyncMock(return_value=15),
        ) as mock_cleanup,
    ):
        deleted = await service.cleanup_old_checkpoints()

    assert deleted == 15
    mock_cleanup.assert_awaited_once()
