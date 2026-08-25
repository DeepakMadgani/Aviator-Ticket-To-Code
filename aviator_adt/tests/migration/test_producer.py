"""Tests for the migration producer."""

import os
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import OperationalError

from migration.models import MigrationProgress
from migration.producer import (
    _acquire_producer_lock,
    _fetch_all_ids_grouped_by_tenant,
    run_producer,
)


class TestFetchAllIdsGroupedByTenant:
    """Tests for _fetch_all_ids_grouped_by_tenant transient error handling."""

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "x"})
    @patch("migration.producer._shutdown_event")
    @patch("migration.producer.dispose_engine")
    @patch("migration.producer.get_engine")
    @patch("migration.producer._fetch_ids_with_tenant_page")
    def test_succeeds_on_first_attempt(
        self, mock_fetch_page: MagicMock, mock_engine: MagicMock, mock_dispose: MagicMock, mock_event: MagicMock
    ) -> None:
        """No retries needed when ID fetching succeeds immediately."""
        mock_event.is_set.return_value = False
        # Returns list of (id, schema) tuples
        mock_fetch_page.side_effect = [
            [("id-1", "public"), ("id-2", "tenant_acme"), ("id-3", "public")],
            [],
        ]

        result = _fetch_all_ids_grouped_by_tenant()
        assert result == {"public": ["id-1", "id-3"], "tenant_acme": ["id-2"]}
        mock_dispose.assert_not_called()

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "x"})
    @patch("migration.producer._shutdown_event")
    @patch("migration.producer.dispose_engine")
    @patch("migration.producer.get_engine")
    @patch("migration.producer._fetch_ids_with_tenant_page")
    def test_retries_on_transient_error(
        self, mock_fetch_page: MagicMock, mock_engine: MagicMock, mock_dispose: MagicMock, mock_event: MagicMock
    ) -> None:
        """Retries after OperationalError and succeeds on second attempt."""
        mock_event.is_set.return_value = False
        mock_event.wait.return_value = None

        op_err = OperationalError("SELECT ...", {}, Exception("connection lost"))
        # First attempt fails, second succeeds
        mock_fetch_page.side_effect = [op_err, [("id-1", "public"), ("id-2", "public")], []]

        result = _fetch_all_ids_grouped_by_tenant()
        assert result == {"public": ["id-1", "id-2"]}
        mock_dispose.assert_called_once()


class TestProducerPublishesTenantGroupedBatches:
    """Tests for producer publishing tenant-grouped ID batches."""

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "x"})
    @patch("migration.producer._shutdown_event")
    @patch("migration.producer._release_producer_lock")
    @patch("migration.producer._acquire_producer_lock")
    @patch("migration.index_manager._get_all_target_schemas", return_value=["public", "tenant_acme"])
    @patch("migration.producer.get_engine")
    @patch("migration.producer.Session")
    @patch("migration.producer.run_preflight")
    @patch("migration.producer.load_progress")
    @patch("migration.producer._fetch_all_ids_grouped_by_tenant")
    @patch("migration.producer.save_progress")
    @patch("migration.producer.mark_completed")
    @patch("migration.producer._publish_batch")
    @patch("migration.producer.settings")
    def test_publishes_tenant_grouped_batches(
        self,
        mock_settings: MagicMock,
        mock_publish: MagicMock,
        mock_complete: MagicMock,
        mock_save: MagicMock,
        mock_fetch_ids: MagicMock,
        mock_load: MagicMock,
        mock_preflight: MagicMock,
        mock_session_cls: MagicMock,
        mock_get_engine: MagicMock,
        mock_get_schemas: MagicMock,
        mock_acquire_lock: MagicMock,
        mock_release_lock: MagicMock,
        mock_shutdown_event: MagicMock,
    ) -> None:
        """Producer publishes correct tenant-grouped ID batches."""
        mock_shutdown_event.is_set.return_value = False
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_load.return_value = MigrationProgress()

        # 1500 IDs total: 1000 in public, 500 in tenant_acme, batch size 500 = 3 batches
        mock_fetch_ids.return_value = {
            "public": [f"id-pub-{i}" for i in range(1000)],
            "tenant_acme": [f"id-acme-{i}" for i in range(500)],
        }

        mock_settings.batch_size = 500
        mock_settings.dry_run = False
        mock_settings.defer_indexes = False
        mock_settings.migration_queue = "test-queue"
        mock_settings.target_dsn = "postgresql://..."

        mock_celery = MagicMock()
        run_producer(mock_celery)

        # Should publish 3 batches: 2 for public (500+500), 1 for tenant_acme (500)
        assert mock_publish.call_count == 3

        # Verify batch contents (tenants processed in sorted order)
        calls = mock_publish.call_args_list
        batch_0 = calls[0][0][1]  # Second arg is the MigrationBatch
        batch_1 = calls[1][0][1]
        batch_2 = calls[2][0][1]

        # public batches first (sorted order)
        assert batch_0.target_schema == "public"
        assert len(batch_0.ids) == 500
        assert batch_0.ids[0] == "id-pub-0"

        assert batch_1.target_schema == "public"
        assert len(batch_1.ids) == 500
        assert batch_1.ids[0] == "id-pub-500"

        # tenant_acme batch last
        assert batch_2.target_schema == "tenant_acme"
        assert len(batch_2.ids) == 500
        assert batch_2.ids[0] == "id-acme-0"

        mock_complete.assert_called_once()


class TestProducerDryRun:
    """Test producer flow in DRY_RUN mode."""

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "x", "MIGRATION_DRY_RUN": "true"})
    @patch("migration.producer._shutdown_event")
    @patch("migration.producer._release_producer_lock")
    @patch("migration.producer._acquire_producer_lock")
    @patch("migration.index_manager._get_all_target_schemas", return_value=[])
    @patch("migration.producer.get_engine")
    @patch("migration.producer.Session")
    @patch("migration.producer.run_preflight")
    @patch("migration.producer.load_progress")
    @patch("migration.producer._fetch_all_ids_grouped_by_tenant", return_value={})
    @patch("migration.producer.mark_completed")
    @patch("migration.producer.save_progress")
    def test_empty_source(
        self,
        mock_save: MagicMock,
        mock_complete: MagicMock,
        mock_fetch_ids: MagicMock,
        mock_load: MagicMock,
        mock_preflight: MagicMock,
        mock_session_cls: MagicMock,
        mock_get_engine: MagicMock,
        mock_get_schemas: MagicMock,
        mock_acquire_lock: MagicMock,
        mock_release_lock: MagicMock,
        mock_shutdown_event: MagicMock,
    ) -> None:
        """Producer with empty source completes immediately."""
        mock_shutdown_event.is_set.return_value = False
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_load.return_value = MigrationProgress()

        mock_celery = MagicMock()
        run_producer(mock_celery)
        mock_complete.assert_called_once()
        mock_acquire_lock.assert_called_once()
        mock_release_lock.assert_called_once()


class TestProducerResumption:
    """Tests for producer resumption from checkpoint."""

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "x"})
    @patch("migration.producer._shutdown_event")
    @patch("migration.producer._release_producer_lock")
    @patch("migration.producer._acquire_producer_lock")
    @patch("migration.index_manager._get_all_target_schemas", return_value=["public"])
    @patch("migration.producer.get_engine")
    @patch("migration.producer.Session")
    @patch("migration.producer.run_preflight")
    @patch("migration.producer.load_progress")
    @patch("migration.producer._fetch_all_ids_grouped_by_tenant")
    @patch("migration.producer.save_progress")
    @patch("migration.producer.mark_completed")
    @patch("migration.producer._publish_batch")
    @patch("migration.producer.settings")
    def test_resumes_from_checkpoint(
        self,
        mock_settings: MagicMock,
        mock_publish: MagicMock,
        mock_complete: MagicMock,
        mock_save: MagicMock,
        mock_fetch_ids: MagicMock,
        mock_load: MagicMock,
        mock_preflight: MagicMock,
        mock_session_cls: MagicMock,
        mock_get_engine: MagicMock,
        mock_get_schemas: MagicMock,
        mock_acquire_lock: MagicMock,
        mock_release_lock: MagicMock,
        mock_shutdown_event: MagicMock,
    ) -> None:
        """Producer resumes from last checkpoint."""
        mock_shutdown_event.is_set.return_value = False
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

        # Resume from index 500 (already processed 500 IDs)
        mock_load.return_value = MigrationProgress(migrated=500, total_rows=1000)

        # 1000 IDs total in public schema
        mock_fetch_ids.return_value = {"public": [f"id-{i}" for i in range(1000)]}

        mock_settings.batch_size = 500
        mock_settings.dry_run = False
        mock_settings.defer_indexes = False
        mock_settings.migration_queue = "test-queue"
        mock_settings.target_dsn = "postgresql://..."

        mock_celery = MagicMock()
        run_producer(mock_celery)

        # Should only publish remaining 1 batch with IDs 500-999
        assert mock_publish.call_count == 1

        batch = mock_publish.call_args_list[0][0][1]
        assert batch.target_schema == "public"
        assert len(batch.ids) == 500
        assert batch.ids[0] == "id-500"

        mock_complete.assert_called_once()


class TestGracefulShutdown:
    """Tests for advisory lock and SIGTERM graceful shutdown."""

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "x"})
    @patch("migration.producer._shutdown_event")
    @patch("migration.producer._release_producer_lock")
    @patch("migration.producer._acquire_producer_lock")
    @patch("migration.producer.get_engine")
    @patch("migration.producer.Session")
    @patch("migration.producer.run_preflight")
    @patch("migration.producer.load_progress")
    @patch("migration.producer._fetch_all_ids_grouped_by_tenant")
    @patch("migration.producer.mark_completed")
    @patch("migration.producer.save_progress")
    @patch("migration.producer.settings")
    def test_stops_on_shutdown_event(
        self,
        mock_settings: MagicMock,
        mock_save: MagicMock,
        mock_complete: MagicMock,
        mock_fetch_ids: MagicMock,
        mock_load: MagicMock,
        mock_preflight: MagicMock,
        mock_session_cls: MagicMock,
        mock_get_engine: MagicMock,
        mock_acquire_lock: MagicMock,
        mock_release_lock: MagicMock,
        mock_shutdown_event: MagicMock,
    ) -> None:
        """Producer stops gracefully when shutdown event is set."""
        mock_shutdown_event.is_set.return_value = True
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_load.return_value = MigrationProgress(migrated=50, total_rows=100)
        mock_fetch_ids.return_value = {"public": [f"id-{i}" for i in range(100)]}
        mock_settings.batch_size = 50

        mock_celery = MagicMock()
        run_producer(mock_celery)

        # Should not mark completed since shutdown was requested
        mock_complete.assert_not_called()
        # Lock must still be released in finally
        mock_release_lock.assert_called_once()

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "x"})
    @patch("migration.producer._shutdown_event")
    @patch("migration.producer.get_engine")
    def test_acquire_lock_fails_when_held(
        self,
        mock_get_engine: MagicMock,
        mock_shutdown_event: MagicMock,
    ) -> None:
        """Lock acquisition raises RuntimeError when another producer holds it."""
        mock_shutdown_event.is_set.return_value = False
        mock_shutdown_event.wait.return_value = False
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_conn.execute.return_value.scalar.return_value = False
        mock_engine.connect.return_value = mock_conn

        with pytest.raises(RuntimeError, match="Could not acquire producer lock"):
            _acquire_producer_lock(mock_engine)
        # Connection should be closed on each failed attempt
        assert mock_conn.close.call_count == 12  # _LOCK_ACQUIRE_ATTEMPTS


class TestDropIndexesBeforeMigration:
    """Tests for drop_indexes_before_migration flag."""

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "x"})
    @patch("migration.producer._shutdown_event")
    @patch("migration.producer._release_producer_lock")
    @patch("migration.producer._acquire_producer_lock")
    @patch("migration.index_manager._get_all_target_schemas", return_value=["public", "tenant_acme"])
    @patch("migration.producer.get_engine")
    @patch("migration.producer.Session")
    @patch("migration.producer.run_preflight")
    @patch("migration.producer.load_progress")
    @patch("migration.producer._fetch_all_ids_grouped_by_tenant")
    @patch("migration.producer.save_progress")
    @patch("migration.producer.mark_completed")
    @patch("migration.producer._publish_batch")
    @patch("migration.producer.settings")
    def test_sends_drop_tasks_when_enabled_and_not_started(
        self,
        mock_settings: MagicMock,
        mock_publish: MagicMock,
        mock_complete: MagicMock,
        mock_save: MagicMock,
        mock_fetch_ids: MagicMock,
        mock_load: MagicMock,
        mock_preflight: MagicMock,
        mock_session_cls: MagicMock,
        mock_get_engine: MagicMock,
        mock_get_schemas: MagicMock,
        mock_acquire_lock: MagicMock,
        mock_release_lock: MagicMock,
        mock_shutdown_event: MagicMock,
    ) -> None:
        """When drop_indexes_before_migration=True and migrated=0, drop tasks are sent."""
        mock_shutdown_event.is_set.return_value = False
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

        # Migration has not started yet (migrated=0)
        mock_load.return_value = MigrationProgress()

        mock_fetch_ids.return_value = {
            "public": [f"id-{i}" for i in range(100)],
            "tenant_acme": [f"id-{i}" for i in range(100)],
        }

        mock_settings.batch_size = 100
        mock_settings.dry_run = False
        mock_settings.defer_indexes = False
        mock_settings.drop_indexes_before_migration = True
        mock_settings.migration_queue = "test-queue"
        mock_settings.target_dsn = "postgresql://..."

        mock_celery = MagicMock()
        run_producer(mock_celery)

        # Should send drop tasks for each schema
        drop_calls = [c for c in mock_celery.send_task.call_args_list if "drop_schema_indexes" in str(c)]
        assert len(drop_calls) == 2

        # Verify correct schemas
        schemas = [c[1]["args"][0] for c in drop_calls]
        assert sorted(schemas) == ["public", "tenant_acme"]

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "x"})
    @patch("migration.producer._shutdown_event")
    @patch("migration.producer._release_producer_lock")
    @patch("migration.producer._acquire_producer_lock")
    @patch("migration.index_manager._get_all_target_schemas", return_value=["public"])
    @patch("migration.producer.get_engine")
    @patch("migration.producer.Session")
    @patch("migration.producer.run_preflight")
    @patch("migration.producer.load_progress")
    @patch("migration.producer._fetch_all_ids_grouped_by_tenant")
    @patch("migration.producer.save_progress")
    @patch("migration.producer.mark_completed")
    @patch("migration.producer._publish_batch")
    @patch("migration.producer.settings")
    def test_skips_drop_when_migration_already_started(
        self,
        mock_settings: MagicMock,
        mock_publish: MagicMock,
        mock_complete: MagicMock,
        mock_save: MagicMock,
        mock_fetch_ids: MagicMock,
        mock_load: MagicMock,
        mock_preflight: MagicMock,
        mock_session_cls: MagicMock,
        mock_get_engine: MagicMock,
        mock_get_schemas: MagicMock,
        mock_acquire_lock: MagicMock,
        mock_release_lock: MagicMock,
        mock_shutdown_event: MagicMock,
    ) -> None:
        """When drop_indexes_before_migration=True but migrated>0, skip drop."""
        mock_shutdown_event.is_set.return_value = False
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

        # Migration already started (migrated=50)
        mock_load.return_value = MigrationProgress(migrated=50, total_rows=100)

        mock_fetch_ids.return_value = {"public": [f"id-{i}" for i in range(100)]}

        mock_settings.batch_size = 100
        mock_settings.dry_run = False
        mock_settings.defer_indexes = False
        mock_settings.drop_indexes_before_migration = True
        mock_settings.migration_queue = "test-queue"
        mock_settings.target_dsn = "postgresql://..."

        mock_celery = MagicMock()
        run_producer(mock_celery)

        # Should NOT send drop tasks since migration already started
        drop_calls = [c for c in mock_celery.send_task.call_args_list if "drop_schema_indexes" in str(c)]
        assert len(drop_calls) == 0

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "x"})
    @patch("migration.producer._shutdown_event")
    @patch("migration.producer._release_producer_lock")
    @patch("migration.producer._acquire_producer_lock")
    @patch("migration.index_manager._get_all_target_schemas", return_value=["public"])
    @patch("migration.producer.get_engine")
    @patch("migration.producer.Session")
    @patch("migration.producer.run_preflight")
    @patch("migration.producer.load_progress")
    @patch("migration.producer._fetch_all_ids_grouped_by_tenant")
    @patch("migration.producer.save_progress")
    @patch("migration.producer.mark_completed")
    @patch("migration.producer._publish_batch")
    @patch("migration.producer.settings")
    def test_skips_drop_when_flag_disabled(
        self,
        mock_settings: MagicMock,
        mock_publish: MagicMock,
        mock_complete: MagicMock,
        mock_save: MagicMock,
        mock_fetch_ids: MagicMock,
        mock_load: MagicMock,
        mock_preflight: MagicMock,
        mock_session_cls: MagicMock,
        mock_get_engine: MagicMock,
        mock_get_schemas: MagicMock,
        mock_acquire_lock: MagicMock,
        mock_release_lock: MagicMock,
        mock_shutdown_event: MagicMock,
    ) -> None:
        """When drop_indexes_before_migration=False, no drop tasks sent."""
        mock_shutdown_event.is_set.return_value = False
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

        mock_load.return_value = MigrationProgress()

        mock_fetch_ids.return_value = {"public": [f"id-{i}" for i in range(100)]}

        mock_settings.batch_size = 100
        mock_settings.dry_run = False
        mock_settings.defer_indexes = False
        mock_settings.drop_indexes_before_migration = False
        mock_settings.migration_queue = "test-queue"
        mock_settings.target_dsn = "postgresql://..."

        mock_celery = MagicMock()
        run_producer(mock_celery)

        # Should NOT send drop tasks since flag is disabled
        drop_calls = [c for c in mock_celery.send_task.call_args_list if "drop_schema_indexes" in str(c)]
        assert len(drop_calls) == 0
