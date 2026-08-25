"""Tests for tenant service."""

from unittest.mock import ANY, MagicMock, patch

import pytest

from aviator.services.tenant import (
    _validate_tenant_id,
    tenant_id_to_schema_name,
)
from aviator.settings import settings


class TestTenantIdToSchemaName:
    """Tests for tenant_id_to_schema_name helper."""

    def test_none_returns_default_schema(self):
        assert tenant_id_to_schema_name(None) == settings.default_schema

    def test_disabled_always_returns_default_schema(self, monkeypatch):
        monkeypatch.setattr(settings, "multi_tenant_enabled", False)
        assert tenant_id_to_schema_name("acme") == settings.default_schema

    def test_string_returns_prefixed(self, monkeypatch):
        monkeypatch.setattr(settings, "multi_tenant_enabled", True)
        assert tenant_id_to_schema_name("acme") == f"{settings.tenant_schema_prefix}acme"

    def test_numeric_string(self, monkeypatch):
        monkeypatch.setattr(settings, "multi_tenant_enabled", True)
        assert tenant_id_to_schema_name("12345") == f"{settings.tenant_schema_prefix}12345"

    def test_hyphenated(self, monkeypatch):
        monkeypatch.setattr(settings, "multi_tenant_enabled", True)
        assert tenant_id_to_schema_name("my-org") == f"{settings.tenant_schema_prefix}my-org"

    def test_underscored(self, monkeypatch):
        monkeypatch.setattr(settings, "multi_tenant_enabled", True)
        assert tenant_id_to_schema_name("my_org") == f"{settings.tenant_schema_prefix}my_org"


class TestValidateTenantId:
    """Tests for _validate_tenant_id."""

    def test_valid_alphanumeric(self):
        _validate_tenant_id("acme123")

    def test_valid_with_hyphens(self):
        _validate_tenant_id("my-tenant")

    def test_valid_with_underscores(self):
        _validate_tenant_id("my_tenant")

    def test_valid_min_length(self):
        _validate_tenant_id("ab")

    def test_valid_max_length(self):
        _validate_tenant_id("a" * 63)

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="must not be empty"):
            _validate_tenant_id("")

    def test_single_char_raises(self):
        with pytest.raises(ValueError, match="Must match pattern"):
            _validate_tenant_id("a")

    def test_too_long_raises(self):
        with pytest.raises(ValueError, match="Must match pattern"):
            _validate_tenant_id("a" * 64)

    def test_special_chars_raises(self):
        with pytest.raises(ValueError, match="Must match pattern"):
            _validate_tenant_id("acme; DROP SCHEMA")

    def test_space_raises(self):
        with pytest.raises(ValueError, match="Must match pattern"):
            _validate_tenant_id("acme corp")

    def test_dot_raises(self):
        with pytest.raises(ValueError, match="Must match pattern"):
            _validate_tenant_id("acme.corp")


class TestEnsureTenant:
    """Tests for TenantService.ensure_tenant() race-condition handling."""

    def test_ensure_tenant_creates_when_missing(self):
        """Calls create_tenant when the tenant does not yet exist."""
        from aviator.services.tenant import TenantService

        svc = TenantService()
        with (
            patch.object(svc, "tenant_exists", return_value=False),
            patch.object(svc, "create_tenant") as mock_create,
        ):
            svc.ensure_tenant("new-tenant")
            mock_create.assert_called_once_with("new-tenant")

    def test_ensure_tenant_noop_when_already_exists(self):
        """Does nothing when the tenant already exists."""
        from aviator.services.tenant import TenantService

        svc = TenantService()
        with (
            patch.object(svc, "tenant_exists", return_value=True),
            patch.object(svc, "create_tenant") as mock_create,
        ):
            svc.ensure_tenant("existing-tenant")
            mock_create.assert_not_called()

    def test_ensure_tenant_handles_race_condition(self):
        """Swallows TenantAlreadyExistsError from concurrent create_tenant calls."""
        from aviator.exceptions import TenantAlreadyExistsError
        from aviator.services.tenant import TenantService

        svc = TenantService()
        with (
            patch.object(svc, "tenant_exists", return_value=False),
            patch.object(svc, "create_tenant", side_effect=TenantAlreadyExistsError("already exists")),
        ):
            # Should NOT raise — race condition is silently handled
            svc.ensure_tenant("race-tenant")

    def test_ensure_tenant_propagates_other_errors(self):
        """Propagates unexpected exceptions from create_tenant."""
        from aviator.services.tenant import TenantService

        svc = TenantService()
        with (
            patch.object(svc, "tenant_exists", return_value=False),
            patch.object(svc, "create_tenant", side_effect=RuntimeError("unexpected DB error")),
            pytest.raises(RuntimeError, match="unexpected DB error"),
        ):
            svc.ensure_tenant("bad-tenant")


class TestCreateTenant:
    """Tests for TenantService.create_tenant()."""

    @staticmethod
    def _mock_engine_context() -> tuple[MagicMock, MagicMock, MagicMock]:
        """Return mocked SQLAlchemy engine, begin context, and mapped connection."""
        mock_engine = MagicMock()
        mock_begin = MagicMock()
        mock_conn = MagicMock()
        mock_mapped = MagicMock()

        mock_engine.begin.return_value = mock_begin
        mock_begin.__enter__.return_value = mock_conn
        mock_begin.__exit__.return_value = False
        mock_conn.execution_options.return_value = mock_mapped

        return mock_engine, mock_conn, mock_mapped

    def test_create_tenant_creates_summary_table_for_schema(self):
        """Creates both the vector store and ORM summary table in the tenant schema."""
        from aviator.services.tenant import TenantService

        svc = TenantService()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        mock_conn.cursor.return_value.__exit__.return_value = False
        mock_psycopg = MagicMock()
        mock_psycopg.__enter__.return_value = mock_conn
        mock_psycopg.__exit__.return_value = False
        mock_db = MagicMock()
        mock_identifier = MagicMock()
        mock_identifier.as_string.return_value = '"tenant_acme"'
        mock_engine, mock_sql_conn, mock_mapped = self._mock_engine_context()

        with (
            patch.object(svc, "_schema_exists", return_value=False),
            patch("aviator.settings.settings.checkpointer", "memory"),
            patch("aviator.services.tenant.psycopg.connect", return_value=mock_psycopg),
            patch("aviator.services.tenant.psycopg.sql.Identifier", return_value=mock_identifier),
            patch("aviator.services.tenant.init_vector_table") as mock_init_table,
            patch("aviator.services.tenant.create_indexes") as mock_create_indexes,
            patch("sqlalchemy.create_engine", return_value=mock_engine),
            patch("aviator.database.models.Base.metadata.create_all") as mock_create_all,
            patch("aviator.services.tenant.database_manager.get", return_value=mock_db),
        ):
            result = svc.create_tenant("acme")

        assert result.tenant_id == "acme"
        mock_init_table.assert_called_once_with(
            dsn=ANY,
            schema="tenant_acme",
            table=settings.vector_store_table_name,
            vector_size=settings.vector_size,
            metadata_json_column=settings.vector_store_metadata_column,
        )
        mock_create_indexes.assert_called_once_with(
            dsn=ANY,
            schema="tenant_acme",
            table=settings.vector_store_table_name,
            metadata_json_column=settings.vector_store_metadata_column,
            index_type=settings.vector_index_type,
            hnsw_m=settings.hnsw_m,
            hnsw_ef_construction=settings.hnsw_ef_construction,
            ivfflat_lists=settings.ivfflat_lists,
        )
        mock_sql_conn.execution_options.assert_called_once_with(schema_translate_map={None: "tenant_acme"})
        mock_create_all.assert_called_once_with(mock_mapped, checkfirst=True)
        mock_engine.dispose.assert_called_once_with()
        mock_db.create_tables.assert_called_once_with("tenant_acme")

    def test_create_tenant_rolls_back_schema_when_summary_table_create_fails(self):
        """Drops the tenant schema when ORM table creation fails after schema creation."""
        from aviator.services.tenant import TenantService

        svc = TenantService()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        mock_conn.cursor.return_value.__exit__.return_value = False
        mock_psycopg = MagicMock()
        mock_psycopg.__enter__.return_value = mock_conn
        mock_psycopg.__exit__.return_value = False
        mock_db = MagicMock()
        mock_db.create_tables.side_effect = RuntimeError("summary create failed")
        mock_identifier = MagicMock()
        mock_identifier.as_string.return_value = '"tenant_acme"'
        mock_engine, mock_sql_conn, mock_mapped = self._mock_engine_context()

        with (
            patch.object(svc, "_schema_exists", return_value=False),
            patch("aviator.settings.settings.checkpointer", "memory"),
            patch.object(svc, "delete_tenant") as mock_delete,
            patch("aviator.services.tenant.psycopg.connect", return_value=mock_psycopg),
            patch("aviator.services.tenant.psycopg.sql.Identifier", return_value=mock_identifier),
            patch("aviator.services.tenant.init_vector_table"),
            patch("aviator.services.tenant.create_indexes"),
            patch("sqlalchemy.create_engine", return_value=mock_engine),
            patch("aviator.database.models.Base.metadata.create_all") as mock_create_all,
            patch("aviator.services.tenant.database_manager.get", return_value=mock_db),
            pytest.raises(RuntimeError, match="Failed to create summary table"),
        ):
            svc.create_tenant("acme")

        mock_sql_conn.execution_options.assert_called_once_with(schema_translate_map={None: "tenant_acme"})
        mock_create_all.assert_called_once_with(mock_mapped, checkfirst=True)
        mock_engine.dispose.assert_called_once_with()
        mock_delete.assert_called_once_with("acme")
