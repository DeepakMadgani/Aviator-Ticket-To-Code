"""Tests for preflight checks."""

import os
from unittest.mock import MagicMock, patch

import pytest

from migration.models import MigrationProgress


class TestPreflight:
    """Tests for individual preflight check functions."""

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "postgresql://src/csai"})
    @patch("migration.preflight.get_engine")
    def test_check_source_db_success(self, mock_get_engine: MagicMock) -> None:
        """Source table exists → no error."""
        mock_engine = MagicMock()
        mock_get_engine.return_value = mock_engine

        with patch("migration.preflight.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_session.execute.return_value.fetchone.return_value = (1,)

            from migration.preflight import _check_source_db

            _check_source_db()  # should not raise

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "postgresql://src/csai"})
    @patch("migration.preflight.get_engine")
    def test_check_source_db_missing_table(self, mock_get_engine: MagicMock) -> None:
        """Source table missing → PreflightError."""
        mock_engine = MagicMock()
        mock_get_engine.return_value = mock_engine

        with patch("migration.preflight.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_session.execute.return_value.fetchone.return_value = None

            from migration.preflight import PreflightError, _check_source_db

            with pytest.raises(PreflightError, match="does not exist"):
                _check_source_db()

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "postgresql://src/csai"})
    @patch("migration.preflight.load_progress")
    def test_check_not_completed_already_done(self, mock_load: MagicMock) -> None:
        """If migration is completed, raise PreflightError."""
        mock_load.return_value = MigrationProgress(status="completed", total_rows=100, migrated=100)

        from migration.preflight import PreflightError, _check_not_completed

        with pytest.raises(PreflightError, match="already completed"):
            _check_not_completed()

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "postgresql://src/csai"})
    @patch("migration.preflight.load_progress")
    def test_check_not_completed_in_progress(self, mock_load: MagicMock) -> None:
        """If migration is in_progress, no error."""
        mock_load.return_value = MigrationProgress(status="in_progress")

        from migration.preflight import _check_not_completed

        _check_not_completed()  # should not raise

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "postgresql://src/csai"})
    @patch("migration.preflight.get_engine")
    def test_check_dimensions_match(self, mock_get_engine: MagicMock) -> None:
        """Matching dimensions → no error."""
        mock_engine = MagicMock()
        mock_get_engine.return_value = mock_engine

        with patch("migration.preflight.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)
            # First call → source dim, second call → target dim
            mock_session.execute.return_value.fetchone.side_effect = [(768,), (768,)]

            from migration.preflight import _check_dimensions

            _check_dimensions()

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "postgresql://src/csai"})
    @patch("migration.preflight.get_engine")
    def test_check_dimensions_mismatch(self, mock_get_engine: MagicMock) -> None:
        """Mismatching dimensions → PreflightError."""
        mock_engine = MagicMock()
        mock_get_engine.return_value = mock_engine

        with patch("migration.preflight.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_session.execute.return_value.fetchone.side_effect = [(768,), (1536,)]

            from migration.preflight import PreflightError, _check_dimensions

            with pytest.raises(PreflightError, match="dimension mismatch"):
                _check_dimensions()
