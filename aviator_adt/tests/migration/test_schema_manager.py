"""Tests for schema_manager module."""

import os
from unittest.mock import MagicMock, patch

from migration.schema_manager import _initialised_schemas, ensure_schema, resolve_target_schema


class TestResolveTargetSchema:
    """Tests for resolve_target_schema."""

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "x"}, clear=False)
    def test_with_tenant_id(self) -> None:
        mock_settings = MagicMock()
        mock_settings.target_tenant_prefix = "tenant_"
        mock_settings.target_default_schema = "public"
        with patch("migration.schema_manager.settings", mock_settings):
            result = resolve_target_schema({"tenantID": "acme"})
            assert result == "tenant_acme"

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "x"}, clear=False)
    def test_without_tenant_id(self) -> None:
        mock_settings = MagicMock()
        mock_settings.target_tenant_prefix = "tenant_"
        mock_settings.target_default_schema = "public"
        with patch("migration.schema_manager.settings", mock_settings):
            result = resolve_target_schema({})
            assert result == "public"

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "x"}, clear=False)
    def test_empty_tenant_id(self) -> None:
        mock_settings = MagicMock()
        mock_settings.target_tenant_prefix = "tenant_"
        mock_settings.target_default_schema = "public"
        with patch("migration.schema_manager.settings", mock_settings):
            result = resolve_target_schema({"tenantID": ""})
            assert result == "public"

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "x"}, clear=False)
    def test_whitespace_tenant_id(self) -> None:
        mock_settings = MagicMock()
        mock_settings.target_tenant_prefix = "tenant_"
        mock_settings.target_default_schema = "public"
        with patch("migration.schema_manager.settings", mock_settings):
            result = resolve_target_schema({"tenantID": "  "})
            assert result == "public"

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "x"}, clear=False)
    def test_custom_prefix(self) -> None:
        mock_settings = MagicMock()
        mock_settings.target_tenant_prefix = "t_"
        mock_settings.target_default_schema = "public"
        with patch("migration.schema_manager.settings", mock_settings):
            result = resolve_target_schema({"tenantID": "globex"})
            assert result == "t_globex"


class TestEnsureSchema:
    """Tests for ensure_schema."""

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "x"}, clear=False)
    @patch("migration.schema_manager.create_indexes")
    @patch("migration.schema_manager.init_vector_table")
    @patch("migration.schema_manager.create_schema_if_not_exists")
    def test_creates_orm_tables(
        self,
        mock_create_schema: MagicMock,
        mock_init_table: MagicMock,
        mock_create_indexes: MagicMock,
    ) -> None:
        """ensure_schema creates ORM tables (e.g. workspace_document_summaries)."""
        _initialised_schemas.discard("tenant_test")
        mock_settings = MagicMock()
        mock_settings.target_dsn = "postgresql://localhost/test"
        mock_settings.target_table = "aviator"
        mock_settings.vector_size = 768
        mock_settings.metadata_json_column = "langchain_metadata"
        mock_settings.defer_indexes = False

        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_mapped = MagicMock()
        mock_conn.execution_options.return_value = mock_mapped
        mock_engine.begin.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.begin.return_value.__exit__ = MagicMock(return_value=False)

        mock_base = MagicMock()

        with (
            patch("migration.schema_manager.settings", mock_settings),
            patch("sqlalchemy.create_engine", return_value=mock_engine),
            patch("aviator.database.models.Base", mock_base),
        ):
            ensure_schema("tenant_test")

        mock_create_schema.assert_called_once_with(mock_settings.target_dsn, "tenant_test")
        mock_init_table.assert_called_once()
        mock_create_indexes.assert_called_once()
        mock_conn.execution_options.assert_called_once_with(schema_translate_map={None: "tenant_test"})
        mock_base.metadata.create_all.assert_called_once_with(bind=mock_mapped, checkfirst=True)
        mock_engine.dispose.assert_called_once()
        _initialised_schemas.discard("tenant_test")

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "x"}, clear=False)
    @patch("migration.schema_manager.create_indexes")
    @patch("migration.schema_manager.init_vector_table")
    @patch("migration.schema_manager.create_schema_if_not_exists")
    def test_orm_table_failure_raises(
        self,
        mock_create_schema: MagicMock,
        mock_init_table: MagicMock,
        mock_create_indexes: MagicMock,
    ) -> None:
        """ensure_schema propagates errors from ORM table creation."""
        _initialised_schemas.discard("tenant_fail")
        mock_settings = MagicMock()
        mock_settings.target_dsn = "postgresql://localhost/test"
        mock_settings.target_table = "aviator"
        mock_settings.vector_size = 768
        mock_settings.metadata_json_column = "langchain_metadata"
        mock_settings.defer_indexes = True

        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_mapped = MagicMock()
        mock_conn.execution_options.return_value = mock_mapped
        mock_engine.begin.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.begin.return_value.__exit__ = MagicMock(return_value=False)

        mock_base = MagicMock()
        mock_base.metadata.create_all.side_effect = RuntimeError("DB down")

        import pytest

        with (
            patch("migration.schema_manager.settings", mock_settings),
            patch("sqlalchemy.create_engine", return_value=mock_engine),
            patch("aviator.database.models.Base", mock_base),
            pytest.raises(RuntimeError, match="DB down"),
        ):
            ensure_schema("tenant_fail")

        # Schema should NOT be cached on failure.
        assert "tenant_fail" not in _initialised_schemas

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "x"}, clear=False)
    @patch("migration.schema_manager.create_indexes")
    @patch("migration.schema_manager.init_vector_table")
    @patch("migration.schema_manager.create_schema_if_not_exists")
    def test_cached_schema_skips_creation(
        self,
        mock_create_schema: MagicMock,
        mock_init_table: MagicMock,
        mock_create_indexes: MagicMock,
    ) -> None:
        """ensure_schema skips all work for already-initialised schemas."""
        _initialised_schemas.add("tenant_cached")
        try:
            ensure_schema("tenant_cached")
            mock_create_schema.assert_not_called()
            mock_init_table.assert_not_called()
        finally:
            _initialised_schemas.discard("tenant_cached")
