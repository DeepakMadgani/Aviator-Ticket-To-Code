"""Tests for migration verification and repair."""

import os
from unittest.mock import MagicMock, patch


class TestFindMissingIds:
    """Tests for _find_missing_ids."""

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "x"})
    @patch("migration.verify._get_all_target_schemas", return_value=[])
    def test_no_target_schemas(self, mock_schemas: MagicMock) -> None:
        """Returns empty list when no target schemas exist."""
        from migration.verify import _find_missing_ids

        result = _find_missing_ids()
        assert result == []

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "x"})
    @patch("migration.verify._get_all_target_schemas", return_value=["public"])
    @patch("migration.verify._collect_target_ids", return_value={"id-1", "id-2", "id-3"})
    @patch("migration.verify.get_engine")
    def test_no_missing_rows(self, mock_engine: MagicMock, mock_target_ids: MagicMock, mock_schemas: MagicMock) -> None:
        """Returns empty list when all source IDs are in the target."""
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        # Source count = 3
        mock_session.execute.return_value.scalar_one.return_value = 3
        # Source IDs: all present in target
        mock_session.execute.return_value.fetchall.return_value = [("id-1",), ("id-2",), ("id-3",)]

        with patch("migration.verify.Session", return_value=mock_session):
            from migration.verify import _find_missing_ids

            result = _find_missing_ids()
            assert result == []

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "x"})
    @patch("migration.verify._get_all_target_schemas", return_value=["public"])
    @patch("migration.verify._collect_target_ids", return_value={"id-1"})
    @patch("migration.verify.get_engine")
    def test_finds_missing_rows(
        self, mock_engine: MagicMock, mock_target_ids: MagicMock, mock_schemas: MagicMock
    ) -> None:
        """Returns IDs present in source but not in target."""
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        # Source count = 3
        count_result = MagicMock()
        count_result.scalar_one.return_value = 3
        # Source ID rows
        fetch_result = MagicMock()
        fetch_result.fetchall.return_value = [("id-1",), ("id-2",), ("id-3",)]
        # Empty on second call (no more rows)
        empty_result = MagicMock()
        empty_result.fetchall.return_value = []

        mock_session.execute.side_effect = [count_result, fetch_result, empty_result]

        with patch("migration.verify.Session", return_value=mock_session):
            from migration.verify import _find_missing_ids

            result = _find_missing_ids()
            assert sorted(result) == ["id-2", "id-3"]


class TestReport:
    """Tests for report function (count-based)."""

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "x"})
    @patch("migration.verify._get_all_target_schemas", return_value=[])
    def test_report_no_target_schemas(self, mock_schemas: MagicMock) -> None:
        from migration.verify import report

        result = report()
        assert result == -1

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "x"})
    @patch("migration.verify._get_all_target_schemas", return_value=["public"])
    @patch("migration.verify._count_migrated_rows", return_value={"public": 1000})
    @patch("migration.verify.get_engine")
    def test_report_counts_match(self, mock_engine: MagicMock, mock_count: MagicMock, mock_schemas: MagicMock) -> None:
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.execute.return_value.scalar_one.return_value = 1000

        with patch("migration.verify.Session", return_value=mock_session):
            from migration.verify import report

            result = report()
            assert result == 0

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "x"})
    @patch("migration.verify._get_all_target_schemas", return_value=["public", "tenant_acme"])
    @patch("migration.verify._count_migrated_rows", return_value={"public": 500, "tenant_acme": 300})
    @patch("migration.verify.get_engine")
    def test_report_with_missing(self, mock_engine: MagicMock, mock_count: MagicMock, mock_schemas: MagicMock) -> None:
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.execute.return_value.scalar_one.return_value = 1000

        with patch("migration.verify.Session", return_value=mock_session):
            from migration.verify import report

            result = report()
            assert result == 200

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "x"})
    @patch("migration.verify._get_all_target_schemas", return_value=["public"])
    @patch("migration.verify._count_migrated_rows", return_value={"public": 1050})
    @patch("migration.verify.get_engine")
    def test_report_target_exceeds_source(
        self, mock_engine: MagicMock, mock_count: MagicMock, mock_schemas: MagicMock
    ) -> None:
        """When target has more rows than source, returns 0 (no missing)."""
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.execute.return_value.scalar_one.return_value = 1000

        with patch("migration.verify.Session", return_value=mock_session):
            from migration.verify import report

            result = report()
            assert result == 0


class TestRepair:
    """Tests for the repair function."""

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "x"})
    @patch("migration.verify._find_missing_ids", return_value=[])
    def test_repair_nothing_missing(self, mock_find: MagicMock) -> None:
        from migration.verify import repair

        result = repair()
        assert result == 0

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "x"})
    @patch("migration.verify._find_missing_ids", return_value=["id-1", "id-2"])
    @patch("migration.verify.get_engine")
    @patch("migration.verify.resolve_target_schema", return_value="public")
    @patch("migration.verify._parse_embedding", return_value=[0.1, 0.2])
    @patch("migration.verify._insert_batch", return_value=2)
    def test_repair_inserts_missing_rows(
        self,
        mock_insert: MagicMock,
        mock_parse: MagicMock,
        mock_resolve: MagicMock,
        mock_engine: MagicMock,
        mock_find: MagicMock,
    ) -> None:
        from migration.verify import repair

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.execute.return_value.fetchall.return_value = [
            ("id-1", "text1", {"workspaceID": "ws1"}, [0.1, 0.2]),
            ("id-2", "text2", {"workspaceID": "ws2"}, [0.1, 0.2]),
        ]

        with (
            patch("migration.verify.Session", return_value=mock_session),
            patch("migration.verify.settings") as mock_settings,
        ):
            mock_settings.dry_run = False
            mock_settings.batch_size = 5000
            mock_settings.source_dsn = "x"
            mock_settings.source_schema = "public"
            mock_settings.source_table = "testlangchain"

            result = repair()
            assert result == 2
            mock_insert.assert_called_once()
            # Verify called with public schema and 2 SourceRow objects
            call_args = mock_insert.call_args
            assert call_args[0][0] == "public"
            assert len(call_args[0][1]) == 2

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "x"})
    @patch("migration.verify._find_missing_ids", return_value=["id-1"])
    @patch("migration.verify.get_engine")
    @patch("migration.verify.resolve_target_schema", return_value="public")
    @patch("migration.verify._parse_embedding", return_value=[0.1])
    @patch("migration.verify._insert_batch")
    def test_repair_dry_run(
        self,
        mock_insert: MagicMock,
        mock_parse: MagicMock,
        mock_resolve: MagicMock,
        mock_engine: MagicMock,
        mock_find: MagicMock,
    ) -> None:
        """Dry run doesn't insert anything but counts rows."""
        from migration.verify import repair

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.execute.return_value.fetchall.return_value = [
            ("id-1", "text1", {}, [0.1]),
        ]

        with (
            patch("migration.verify.Session", return_value=mock_session),
            patch("migration.verify.settings") as mock_settings,
        ):
            mock_settings.dry_run = True
            mock_settings.batch_size = 5000
            mock_settings.source_dsn = "x"
            mock_settings.source_schema = "public"
            mock_settings.source_table = "testlangchain"

            result = repair()
            assert result == 1
            mock_insert.assert_not_called()
