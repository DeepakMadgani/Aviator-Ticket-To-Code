"""Tests for checkpoint module."""

import os
from unittest.mock import MagicMock, patch


class TestCheckpoint:
    """Tests for checkpoint read / write helpers."""

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "x"})
    @patch("migration.checkpoint.get_engine")
    @patch("migration.checkpoint.Base")
    def test_ensure_state_table(self, mock_base: MagicMock, mock_get_engine: MagicMock) -> None:
        """Ensure create_all is called for the migration_state table."""
        mock_engine = MagicMock()
        mock_get_engine.return_value = mock_engine

        from migration.checkpoint import ensure_state_table

        ensure_state_table()
        mock_base.metadata.create_all.assert_called_once()

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "x"})
    @patch("migration.checkpoint.get_engine")
    @patch("migration.checkpoint.ensure_state_table")
    def test_load_progress_empty(self, mock_ensure: MagicMock, mock_get_engine: MagicMock) -> None:
        """When no row exists, return default MigrationProgress."""
        mock_engine = MagicMock()
        mock_get_engine.return_value = mock_engine
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_engine.__class__ = MagicMock  # not a Session

        # Mock Session context manager
        with patch("migration.checkpoint.Session") as mock_session_cls:
            mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_session.execute.return_value.scalar_one_or_none.return_value = None

            from migration.checkpoint import load_progress

            p = load_progress()
            assert p.last_id is None
            assert p.status == "in_progress"

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "x"})
    @patch("migration.checkpoint.get_engine")
    @patch("migration.checkpoint.ensure_state_table")
    def test_load_progress_existing(self, mock_ensure: MagicMock, mock_get_engine: MagicMock) -> None:
        """When a row exists, return populated MigrationProgress."""
        import uuid

        row_id = uuid.uuid4()
        mock_engine = MagicMock()
        mock_get_engine.return_value = mock_engine

        mock_row = MagicMock()
        mock_row.last_id = str(row_id)
        mock_row.status = "in_progress"
        mock_row.total_rows = 1000
        mock_row.migrated = 500

        with patch("migration.checkpoint.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_session.execute.return_value.scalar_one_or_none.return_value = mock_row

            from migration.checkpoint import load_progress

            p = load_progress()
            assert p.last_id == str(row_id)
            assert p.status == "in_progress"
            assert p.total_rows == 1000
            assert p.migrated == 500
