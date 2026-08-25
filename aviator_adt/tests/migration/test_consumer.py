"""Tests for the migration consumer task."""

import contextlib
import hashlib
import os
from unittest.mock import MagicMock, patch

from migration.models import MigrationBatch


class TestConvertLocToStartIndex:
    """Tests for _convert_loc_to_start_index helper."""

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "x"})
    def test_converts_loc_lines(self) -> None:
        from migration.consumer import _convert_loc_to_start_index

        meta = {"loc": {"lines": {"from": 10, "to": 20}}, "other": "val"}
        result = _convert_loc_to_start_index(meta)
        assert result["start_index"] == 10
        assert "loc" not in result
        assert result["other"] == "val"

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "x"})
    def test_no_loc_key(self) -> None:
        from migration.consumer import _convert_loc_to_start_index

        meta = {"workspaceID": "ws1"}
        result = _convert_loc_to_start_index(meta)
        assert "start_index" not in result
        assert result["workspaceID"] == "ws1"

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "x"})
    def test_loc_without_lines(self) -> None:
        from migration.consumer import _convert_loc_to_start_index

        meta = {"loc": {"pageNumber": 3}}
        result = _convert_loc_to_start_index(meta)
        assert "start_index" not in result
        assert "loc" not in result

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "x"})
    def test_does_not_overwrite_existing_start_index(self) -> None:
        from migration.consumer import _convert_loc_to_start_index

        meta = {"loc": {"lines": {"from": 10, "to": 20}}, "start_index": 99}
        result = _convert_loc_to_start_index(meta)
        assert result["start_index"] == 99
        assert "loc" not in result

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "x"})
    def test_loc_lines_from_zero(self) -> None:
        from migration.consumer import _convert_loc_to_start_index

        meta = {"loc": {"lines": {"from": 0, "to": 0}}}
        result = _convert_loc_to_start_index(meta)
        assert result["start_index"] == 0


class TestPrepareMetadata:
    """Tests for _prepare_metadata helper."""

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "x"})
    def test_strip_tenant_id(self) -> None:
        with patch("migration.consumer.settings") as mock_settings:
            mock_settings.strip_tenant_from_meta = True

            from migration.consumer import _prepare_metadata

            meta = {"tenantID": "acme", "workspaceID": "ws1", "documentID": "d1"}
            result = _prepare_metadata(meta)
            assert "tenantID" not in result
            assert result["workspaceID"] == "ws1"
            assert result["documentID"] == "d1"
            assert result["_source"] == "csai_legacy"

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "x"})
    def test_keep_tenant_id(self) -> None:
        with patch("migration.consumer.settings") as mock_settings:
            mock_settings.strip_tenant_from_meta = False

            from migration.consumer import _prepare_metadata

            meta = {"tenantID": "acme", "workspaceID": "ws1"}
            result = _prepare_metadata(meta)
            assert result["tenantID"] == "acme"
            assert result["_source"] == "csai_legacy"

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "x"})
    def test_converts_loc_and_strips_tenant(self) -> None:
        """_prepare_metadata both converts loc→start_index and strips tenantID."""
        with patch("migration.consumer.settings") as mock_settings:
            mock_settings.strip_tenant_from_meta = True

            from migration.consumer import _prepare_metadata

            meta = {
                "tenantID": "acme",
                "workspaceID": "ws1",
                "loc": {"lines": {"from": 443, "to": 444}},
            }
            result = _prepare_metadata(meta)
            assert result["start_index"] == 443
            assert "tenantID" not in result
            assert "loc" not in result
            assert result["_source"] == "csai_legacy"


class TestProcessMigrationBatch:
    """Tests for the Celery task logic."""

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "x"})
    def test_dry_run(self) -> None:
        """In DRY_RUN mode, no inserts or fetches happen."""
        with (
            patch("migration.consumer.settings") as mock_settings,
            patch("migration.consumer._fetch_rows_with_retry") as mock_fetch,
        ):
            mock_settings.dry_run = True
            mock_settings.strip_tenant_from_meta = True

            from migration.consumer import process_migration_batch

            batch = MigrationBatch(target_schema="public", ids=["id-1", "id-2", "id-3"])
            # Call the task function directly (not via Celery).
            result = process_migration_batch(batch.model_dump(mode="json"))
            assert result["target_schema"] == "public"
            assert result["inserted"] == 0
            assert result["processed"] == 0
            mock_fetch.assert_not_called()

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "x"})
    def test_fetches_and_inserts(self) -> None:
        """Consumer fetches rows from source and inserts to single target schema."""
        with (
            patch("migration.consumer.settings") as mock_settings,
            patch("migration.consumer._fetch_rows_with_retry") as mock_fetch,
            patch("migration.consumer.ensure_schema") as mock_ensure,
            patch("migration.consumer._insert_batch") as mock_insert,
        ):
            mock_settings.dry_run = False
            mock_settings.strip_tenant_from_meta = False

            # Mock fetched rows
            mock_fetch.return_value = [
                ("id-1", "text1", {"tenantID": "acme", "workspaceID": "ws1"}, [0.1, 0.2]),
                ("id-2", "text2", {"tenantID": "acme", "workspaceID": "ws2"}, [0.3, 0.4]),
            ]
            mock_insert.return_value = 2

            from migration.consumer import process_migration_batch

            batch = MigrationBatch(target_schema="tenant_acme", ids=["id-1", "id-2"])
            result = process_migration_batch(batch.model_dump(mode="json"))

            assert result["target_schema"] == "tenant_acme"
            assert result["processed"] == 2
            assert result["inserted"] == 2
            mock_fetch.assert_called_once_with(["id-1", "id-2"])
            mock_ensure.assert_called_once_with("tenant_acme")
            mock_insert.assert_called_once()
            # Verify insert was called with the correct schema
            call_args = mock_insert.call_args
            assert call_args[0][0] == "tenant_acme"
            assert len(call_args[0][1]) == 2


class TestConvertRawRowsToSourceRows:
    """Tests for _convert_raw_rows_to_source_rows helper."""

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "x"})
    def test_conversion(self) -> None:
        from migration.consumer import _convert_raw_rows_to_source_rows

        raw_rows = [
            ("id-1", "text1", {"tenantID": "acme", "workspaceID": "ws1"}, [0.1]),
            ("id-2", "text2", {"tenantID": "acme", "workspaceID": "ws2"}, [0.2]),
            ("id-3", None, {}, [0.3]),
        ]

        result = _convert_raw_rows_to_source_rows(raw_rows)
        assert len(result) == 3
        assert result[0].id == "id-1"
        assert result[0].text == "text1"
        assert result[0].metadata == {"tenantID": "acme", "workspaceID": "ws1"}
        assert result[1].id == "id-2"
        assert result[2].text is None


class TestInsertBatchTextHash:
    """Tests for text_hash computation during _insert_batch."""

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "x"})
    @patch("migration.consumer.get_engine")
    @patch("migration.consumer.Session")
    def test_text_hash_computed_for_content(self, mock_session_cls: MagicMock, mock_engine: MagicMock) -> None:
        """_insert_batch computes text_hash from content using SHA256."""
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

        with patch("migration.consumer.settings") as mock_settings:
            mock_settings.target_table = "aviator"
            mock_settings.strip_tenant_from_meta = False

            from migration.consumer import SourceRow, _insert_batch

            rows = [SourceRow(id="abc", text="hello world", metadata={}, embedding=[0.1])]
            _insert_batch("public", rows)

            # Inspect the values passed to pg_insert
            call_args = mock_session.execute.call_args
            stmt = call_args[0][0]
            params = stmt.compile().params
            expected_hash = hashlib.sha256(" ".join(["hello", "world"]).encode("utf-8")).hexdigest()  # noqa: FLY002
            assert params["text_hash_m0"] == expected_hash

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "x"})
    @patch("migration.consumer.get_engine")
    @patch("migration.consumer.Session")
    def test_text_hash_none_for_null_content(self, mock_session_cls: MagicMock, mock_engine: MagicMock) -> None:
        """_insert_batch sets text_hash to None when content is None."""
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

        with patch("migration.consumer.settings") as mock_settings:
            mock_settings.target_table = "aviator"
            mock_settings.strip_tenant_from_meta = False

            from migration.consumer import SourceRow, _insert_batch

            rows = [SourceRow(id="abc", text=None, metadata={}, embedding=[0.1])]
            _insert_batch("public", rows)

            call_args = mock_session.execute.call_args
            stmt = call_args[0][0]
            params = stmt.compile().params
            assert params["text_hash_m0"] is None

            call_args = mock_session.execute.call_args
            stmt = call_args[0][0]
            params = stmt.compile().params
            assert params["text_hash_m0"] is None


class TestAdvisoryLockKey:
    """Tests for _advisory_lock_key helper."""

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "x"})
    def test_deterministic(self) -> None:
        from migration.consumer import _advisory_lock_key

        assert _advisory_lock_key("tenant_a") == _advisory_lock_key("tenant_a")

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "x"})
    def test_different_schemas_differ(self) -> None:
        from migration.consumer import _advisory_lock_key

        assert _advisory_lock_key("tenant_a") != _advisory_lock_key("tenant_b")

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "x"})
    def test_fits_bigint(self) -> None:
        from migration.consumer import _advisory_lock_key

        key = _advisory_lock_key("some_schema")
        assert -(2**63) <= key <= 2**63 - 1


def _make_engine_mock(lock_acquired: bool = True) -> tuple[MagicMock, MagicMock]:
    """Create a mock engine whose connect() context manager yields a usable connection mock.

    Returns (mock_engine, mock_conn) where mock_conn tracks execute/commit calls.
    """
    mock_conn = MagicMock()
    mock_conn.execute.return_value.scalar.return_value = lock_acquired
    # engine.connect() returns a context manager; __enter__ must yield mock_conn
    mock_cm = MagicMock()
    mock_cm.__enter__ = MagicMock(return_value=mock_conn)
    mock_cm.__exit__ = MagicMock(return_value=False)
    mock_engine = MagicMock()
    mock_engine.connect.return_value = mock_cm
    return mock_engine, mock_conn


class TestRebuildSchemaIndexesTask:
    """Tests for advisory lock in rebuild_schema_indexes_task."""

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "x"})
    def test_skips_when_lock_not_acquired(self) -> None:
        """When another worker holds the lock the task returns skipped."""
        mock_engine, _mock_conn = _make_engine_mock(lock_acquired=False)

        with (
            patch("migration.consumer.get_engine", return_value=mock_engine),
            patch("migration.index_manager.rebuild_schema_indexes") as mock_rebuild,
        ):
            from migration.consumer import rebuild_schema_indexes_task

            result = rebuild_schema_indexes_task("tenant_a")

        assert result == {"schema": "tenant_a", "status": "skipped_concurrent"}
        mock_rebuild.assert_not_called()

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "x"})
    def test_builds_indexes_when_lock_acquired(self) -> None:
        """When the lock is acquired, indexes are built and lock is released."""
        mock_engine, mock_conn = _make_engine_mock(lock_acquired=True)

        with (
            patch("migration.consumer.get_engine", return_value=mock_engine),
            patch("migration.index_manager.rebuild_schema_indexes") as mock_rebuild,
        ):
            from migration.consumer import rebuild_schema_indexes_task

            result = rebuild_schema_indexes_task("tenant_a")

        assert result == {"schema": "tenant_a", "status": "indexes_rebuilt"}
        mock_rebuild.assert_called_once_with("tenant_a")
        # lock + unlock = at least 2 execute calls, plus a commit
        assert mock_conn.execute.call_count >= 2
        assert mock_conn.commit.called

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "x"})
    def test_unlock_called_on_failure(self) -> None:
        """Advisory lock is released even when rebuild_schema_indexes raises."""
        mock_engine, mock_conn = _make_engine_mock(lock_acquired=True)

        with (
            patch("migration.consumer.get_engine", return_value=mock_engine),
            patch(
                "migration.index_manager.rebuild_schema_indexes",
                side_effect=RuntimeError("boom"),
            ),
        ):
            from migration.consumer import rebuild_schema_indexes_task

            with contextlib.suppress(RuntimeError):
                rebuild_schema_indexes_task("tenant_a")

        # lock + unlock = at least 2 execute calls even on failure
        assert mock_conn.execute.call_count >= 2
        assert mock_conn.commit.called


class TestDropSchemaIndexesTask:
    """Tests for advisory lock in drop_schema_indexes_task."""

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "x"})
    def test_skips_when_lock_not_acquired(self) -> None:
        """When another worker holds the lock the task returns skipped."""
        mock_engine, _mock_conn = _make_engine_mock(lock_acquired=False)

        with (
            patch("migration.consumer.get_engine", return_value=mock_engine),
            patch("migration.index_manager.drop_schema_indexes") as mock_drop,
        ):
            from migration.consumer import drop_schema_indexes_task

            result = drop_schema_indexes_task("tenant_a")

        assert result == {"schema": "tenant_a", "status": "skipped_concurrent"}
        mock_drop.assert_not_called()

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "x"})
    def test_drops_indexes_when_lock_acquired(self) -> None:
        """When the lock is acquired, indexes are dropped and lock is released."""
        mock_engine, mock_conn = _make_engine_mock(lock_acquired=True)

        with (
            patch("migration.consumer.get_engine", return_value=mock_engine),
            patch("migration.index_manager.drop_schema_indexes", return_value=3) as mock_drop,
        ):
            from migration.consumer import drop_schema_indexes_task

            result = drop_schema_indexes_task("tenant_a")

        assert result == {"schema": "tenant_a", "status": "indexes_dropped", "count": 3}
        mock_drop.assert_called_once_with("tenant_a")
        # lock + unlock = at least 2 execute calls, plus a commit
        assert mock_conn.execute.call_count >= 2
        assert mock_conn.commit.called

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "x"})
    def test_unlock_called_on_failure(self) -> None:
        """Advisory lock is released even when drop_schema_indexes raises."""
        mock_engine, mock_conn = _make_engine_mock(lock_acquired=True)

        with (
            patch("migration.consumer.get_engine", return_value=mock_engine),
            patch(
                "migration.index_manager.drop_schema_indexes",
                side_effect=RuntimeError("boom"),
            ),
        ):
            from migration.consumer import drop_schema_indexes_task

            with contextlib.suppress(RuntimeError):
                drop_schema_indexes_task("tenant_a")

        # lock + unlock = at least 2 execute calls even on failure
        assert mock_conn.execute.call_count >= 2
        assert mock_conn.commit.called
