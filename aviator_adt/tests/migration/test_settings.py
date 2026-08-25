"""Tests for migration settings."""

import os
from unittest.mock import MagicMock, patch

from migration.settings import MigrationSettings


class TestMigrationSettings:
    """Tests for MigrationSettings loading."""

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "postgresql://src:5432/csai"}, clear=True)
    def test_defaults(self) -> None:
        """Verify default values are applied except for required fields."""
        s = MigrationSettings(_env_file=None)
        assert s.source_table == "testlangchain"
        assert s.source_schema == "public"
        assert s.migration_queue == "csai-adt-migration"
        assert s.strip_tenant_from_meta is False
        assert s.dry_run is False
        assert s.log_level == "INFO"

    @patch.dict(os.environ, {"MIGRATION_SOURCE_DSN": "postgresql://src:5432/csai"}, clear=False)
    def test_target_fields_delegate_to_aviator(self) -> None:
        """Target properties read from aviator settings (single source of truth)."""
        from aviator.settings import settings as aviator_settings

        s = MigrationSettings()
        assert s.target_table == aviator_settings.vector_store_table_name
        assert s.target_default_schema == aviator_settings.default_schema
        assert s.target_tenant_prefix == aviator_settings.tenant_schema_prefix
        assert s.target_dsn == str(aviator_settings.postgres_connection)
        assert s.broker_url == str(aviator_settings.broker_url)
        assert s.vector_size == aviator_settings.vector_size
        assert s.metadata_json_column == aviator_settings.vector_store_metadata_column

    def test_empty_source_dsn_when_no_components(self) -> None:
        """source_dsn defaults to empty when neither DSN nor source_host is set."""
        with patch.dict(os.environ, {}, clear=True):
            s = MigrationSettings(_env_file=None)
            assert s.source_dsn == ""

    @patch.dict(
        os.environ, {"MIGRATION_SOURCE_DSN": "postgresql://src:5432/csai", "MIGRATION_BATCH_SIZE": "100"}, clear=False
    )
    def test_custom_batch_size(self) -> None:
        """MIGRATION_BATCH_SIZE can be overridden via env."""
        s = MigrationSettings()
        assert s.batch_size == 100


class TestSourceDsnConstruction:
    """Tests for source_dsn built from individual components."""

    def test_explicit_dsn_takes_precedence(self) -> None:
        """When MIGRATION_SOURCE_DSN is provided, component fields are ignored."""
        env = {
            "MIGRATION_SOURCE_DSN": "postgresql://explicit:5432/db",
            "MIGRATION_SOURCE_HOST": "other-host",
        }
        with patch.dict(os.environ, env, clear=True):
            s = MigrationSettings(_env_file=None)
            assert s.source_dsn == "postgresql://explicit:5432/db"

    def test_dsn_built_from_components(self) -> None:
        """source_dsn is constructed from individual fields when not set directly."""
        env = {
            "MIGRATION_SOURCE_HOST": "my-source-host",
            "MIGRATION_SOURCE_PORT": "5433",
            "MIGRATION_SOURCE_USER": "admin",
            "MIGRATION_SOURCE_PASSWORD": "secret",
            "MIGRATION_SOURCE_DATABASE": "CSAI",
        }
        with patch.dict(os.environ, env, clear=True):
            s = MigrationSettings(_env_file=None)
            assert s.source_dsn == "postgresql://admin:secret@my-source-host:5433/CSAI"

    def test_dsn_built_with_defaults(self) -> None:
        """Missing optional component fields use defaults."""
        env = {
            "MIGRATION_SOURCE_HOST": "src-host",
        }
        with patch.dict(os.environ, env, clear=True):
            s = MigrationSettings(_env_file=None)
            assert s.source_dsn == "postgresql://postgres:postgres@src-host:5432/postgres"

    def test_no_dsn_without_host(self) -> None:
        """Without source_dsn or source_host, source_dsn stays empty."""
        with patch.dict(os.environ, {}, clear=True):
            s = MigrationSettings(_env_file=None)
            assert s.source_dsn == ""

    @patch("migration.settings.aviator_settings")
    def test_source_password_from_secret_manager(self, mock_aviator: MagicMock) -> None:
        """source_password_key triggers Secret Manager lookup."""
        mock_aviator.secrets_manager = "google"
        mock_aviator.google_cloud_project = "my-project"

        mock_sm = MagicMock()
        mock_sm.get_secret.return_value = "sm-password"

        env = {
            "MIGRATION_SOURCE_HOST": "src-host",
            "MIGRATION_SOURCE_PASSWORD_KEY": "projects/p/secrets/src-pass/versions/latest",
        }
        with (
            patch.dict(os.environ, env, clear=True),
            patch("aviator.services.secrets.create_secrets_manager", return_value=mock_sm) as mock_create,
        ):
            s = MigrationSettings(_env_file=None)

        mock_create.assert_called_once_with(provider="google", project_id="my-project")
        mock_sm.get_secret.assert_called_once_with("projects/p/secrets/src-pass/versions/latest")
        assert "sm-password" in s.source_dsn
        assert s.source_dsn == "postgresql://postgres:sm-password@src-host:5432/postgres"

    @patch("migration.settings.aviator_settings")
    def test_source_password_key_fallback_on_failure(self, mock_aviator: MagicMock) -> None:
        """Falls back to source_password when Secret Manager fails."""
        mock_aviator.secrets_manager = "google"
        mock_aviator.google_cloud_project = "my-project"

        mock_sm = MagicMock()
        mock_sm.get_secret.return_value = None

        env = {
            "MIGRATION_SOURCE_HOST": "src-host",
            "MIGRATION_SOURCE_PASSWORD": "env-password",
            "MIGRATION_SOURCE_PASSWORD_KEY": "projects/p/secrets/src-pass/versions/latest",
        }
        with (
            patch.dict(os.environ, env, clear=True),
            patch("aviator.services.secrets.create_secrets_manager", return_value=mock_sm),
        ):
            s = MigrationSettings(_env_file=None)

        assert s.source_dsn == "postgresql://postgres:env-password@src-host:5432/postgres"

    @patch("migration.settings.aviator_settings")
    def test_source_password_key_ignored_when_not_google(self, mock_aviator: MagicMock) -> None:
        """source_password_key is ignored when secrets_manager != google."""
        mock_aviator.secrets_manager = "environment"

        env = {
            "MIGRATION_SOURCE_HOST": "src-host",
            "MIGRATION_SOURCE_PASSWORD": "env-pass",
            "MIGRATION_SOURCE_PASSWORD_KEY": "projects/p/secrets/src-pass/versions/latest",
        }
        with patch.dict(os.environ, env, clear=True):
            s = MigrationSettings(_env_file=None)

        assert s.source_dsn == "postgresql://postgres:env-pass@src-host:5432/postgres"
