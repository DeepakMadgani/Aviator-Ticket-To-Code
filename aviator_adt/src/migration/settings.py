"""Migration settings loaded from environment variables.

Target-related settings (database connection, table name, schema config,
message broker) are delegated to ``aviator.settings`` so there is a single
source of truth shared with the main ADT application.

Only **source** and **migration-specific** settings are defined here.
All environment variables use a ``MIGRATION_`` prefix (e.g.
``MIGRATION_SOURCE_DSN``, ``MIGRATION_BATCH_SIZE``).
"""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from aviator.settings import settings as aviator_settings

logger = logging.getLogger(__name__)


class MigrationSettings(BaseSettings):
    """Configuration for the CSAI → ADT vector store migration.

    All migration-specific environment variables use the ``MIGRATION_``
    prefix (e.g. ``MIGRATION_SOURCE_DSN``, ``MIGRATION_DRY_RUN``).

    Target database, broker, and vector store settings are read from
    ``aviator.settings.settings`` (controlled by the same environment
    variables as the main ADT app, e.g. ``POSTGRES_CONNECTION``,
    ``BROKER_URL``, ``VECTOR_STORE_TABLE_NAME``).
    """

    model_config = SettingsConfigDict(
        env_prefix="MIGRATION_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Source (CSAI) ──────────────────────────────────────────────────
    source_dsn: str = Field(
        default="",
        description=(
            "Full source PostgreSQL connection string (e.g. postgresql://user:pass@host:5432/db). "
            "When set, takes precedence over individual source_* fields. "
            "Required by both producer AND workers (workers fetch rows directly from source)."
        ),
    )
    source_host: str | None = Field(
        default=None,
        description="Source PostgreSQL hostname. Used to build source_dsn when source_dsn is not set directly.",
    )
    source_port: int = Field(
        default=5432,
        description="Source PostgreSQL port.",
    )
    source_user: str = Field(
        default="postgres",
        description="Source PostgreSQL user.",
    )
    source_password: str | None = Field(
        default=None,
        description="Source PostgreSQL password. Ignored when source_password_key is set.",
    )
    source_database: str = Field(
        default="postgres",
        description="Source PostgreSQL database name.",
    )
    source_password_key: str | None = Field(
        default=None,
        description=(
            "Google Cloud Secret Manager key for the source database password "
            "(e.g. 'projects/<project>/secrets/<name>/versions/latest'). "
            "Requires SECRETS_MANAGER=google in the main aviator settings."
        ),
    )
    source_table: str = Field(
        default="testlangchain",
        description="Source table name in the CSAI database.",
    )
    source_schema: str = Field(
        default="public",
        description="Source PostgreSQL schema.",
    )

    # ── Migration behaviour ───────────────────────────────────────────
    batch_size: int = Field(
        default=500,
        ge=1,
        description=(
            "Number of rows per batch (offset, limit) message. "
            "Workers fetch this many rows from the source DB per task."
        ),
    )
    migration_queue: str = Field(
        default="csai-adt-migration",
        description="Queue / topic name for migration batches.",
    )
    strip_tenant_from_meta: bool = Field(
        default=False,
        description="Remove 'tenantID' key from target metadata after routing.",
    )
    dry_run: bool = Field(
        default=False,
        description="Log what would be done without writing to the target.",
    )
    defer_indexes: bool = Field(
        default=True,
        description=(
            "Skip creating secondary indexes during migration. "
            "Indexes are rebuilt automatically after all batches complete."
        ),
    )
    drop_indexes_before_migration: bool = Field(
        default=False,
        description=(
            "Drop non-PK indexes on the target table before migration starts. "
            "Only effective if migration has not started (migrated=0). "
            "Indexes are dropped per-tenant (or default schema if no tenantID). "
            "Combine with defer_indexes=True to rebuild them after migration completes."
        ),
    )
    maintenance_work_mem: str = Field(
        default="512MB",
        description=(
            "PostgreSQL maintenance_work_mem for index rebuilds. Must not exceed the db container's shm_size."
        ),
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        description="Logging level.",
    )

    # ── Summary backfill ──────────────────────────────────────────────
    summary_batch_size: int = Field(
        default=10,
        ge=1,
        description="Number of documents per summary backfill batch / queue message.",
    )
    summary_queue: str = Field(
        default="csai-adt-summary",
        description="Queue / topic name for summary backfill batches.",
    )

    # ── Source DSN construction ───────────────────────────────────────

    def model_post_init(self, __context) -> None:  # noqa: PYI063, ANN001
        """Build source_dsn from individual components when not set directly."""
        if not self.source_dsn and self.source_host:
            password = self._get_source_password()
            self.source_dsn = (
                f"postgresql://{self.source_user}:{password}"
                f"@{self.source_host}:{self.source_port}/{self.source_database}"
            )

    def _get_source_password(self) -> str:
        """Resolve source DB password from Secret Manager or env var."""
        if aviator_settings.secrets_manager == "google" and self.source_password_key:
            try:
                from aviator.services.secrets import create_secrets_manager

                sm = create_secrets_manager(
                    provider="google",
                    project_id=aviator_settings.google_cloud_project,
                )
                password = sm.get_secret(self.source_password_key)
                if password is not None:
                    logger.info("Retrieved source DB password from Google Cloud Secret Manager")
                    return password

                logger.error(
                    "Failed to retrieve source DB password from Secret Manager "
                    "using key '%s'. Falling back to env var.",
                    self.source_password_key,
                )
            except Exception:
                logger.exception("Error retrieving source DB password from Secret Manager. Falling back to env var.")

        return self.source_password or "postgres"

    # ── Target settings (delegated to aviator.settings) ───────────────

    @property
    def target_dsn(self) -> str:
        """Target PostgreSQL DSN — reads ``POSTGRES_CONNECTION`` via aviator."""
        return str(aviator_settings.postgres_connection)

    @property
    def target_table(self) -> str:
        """Target table name — reads ``VECTOR_STORE_TABLE_NAME`` via aviator."""
        return aviator_settings.vector_store_table_name

    @property
    def target_default_schema(self) -> str:
        """Default target schema — reads ``DEFAULT_SCHEMA`` via aviator."""
        return aviator_settings.default_schema

    @property
    def target_tenant_prefix(self) -> str:
        """Tenant schema prefix — reads ``TENANT_SCHEMA_PREFIX`` via aviator."""
        return aviator_settings.tenant_schema_prefix

    @property
    def broker_url(self) -> str:
        """Message broker URL — reads ``BROKER_URL`` via aviator."""
        return str(aviator_settings.broker_url)

    @property
    def vector_size(self) -> int:
        """Embedding vector dimension — reads ``VECTOR_SIZE`` via aviator."""
        return aviator_settings.vector_size

    @property
    def metadata_json_column(self) -> str:
        """JSONB metadata column name — reads ``VECTOR_STORE_METADATA_COLUMN`` via aviator."""
        return aviator_settings.vector_store_metadata_column


settings = MigrationSettings()  # type: ignore[call-arg]
