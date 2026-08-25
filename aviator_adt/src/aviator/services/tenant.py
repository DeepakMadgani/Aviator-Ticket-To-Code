"""Tenant management service for multi-tenancy support.

Provides CRUD operations for tenant schemas in PostgreSQL. Each tenant gets
a dedicated schema containing its own vector store table and HNSW index.
Records without a tenant_id fall back to the ``public`` schema.
"""

import logging
import re

import psycopg
from pydantic import BaseModel, Field

from aviator.database import database_manager
from aviator.exceptions import TenantAlreadyExistsError, TenantNotFoundError
from aviator.settings import settings
from aviator.vector_store.schema import create_indexes, init_vector_table

logger = logging.getLogger(__name__)

# Database connection string prefixes
_POSTGRES_PREFIX = "postgresql://"
_PSYCOPG_PREFIX = "postgresql+psycopg://"


class TenantInfo(BaseModel):
    """Read-only representation of a tenant."""

    tenant_id: str = Field(..., description="Unique tenant identifier")


def tenant_id_to_schema_name(tenant_id: str | None) -> str:
    """Derive the PostgreSQL schema name from a tenant_id.

    When ``multi_tenant_enabled`` is ``False``, always returns the
    default schema regardless of the tenant_id value.

    Args:
        tenant_id: The tenant identifier. ``None`` maps to the default schema.

    Returns:
        The schema name string.

    """
    if not settings.multi_tenant_enabled or tenant_id is None:
        return settings.default_schema
    return f"{settings.tenant_schema_prefix}{tenant_id}"


def _get_connection_string() -> str:
    """Return the psycopg-style PostgreSQL connection string."""
    conn_str = settings.postgres_connection.encoded_string()
    return conn_str.replace(_POSTGRES_PREFIX, _PSYCOPG_PREFIX)


def _get_raw_connection_string() -> str:
    """Return the plain PostgreSQL connection string (for psycopg direct use)."""
    return _get_connection_string().replace(_PSYCOPG_PREFIX, _POSTGRES_PREFIX)


def _validate_tenant_id(tenant_id: str) -> None:
    """Validate that a tenant_id is safe for use in schema names.

    The tenant_id must match the configured ``tenant_id_pattern`` regex,
    which enforces allowed characters and length constraints.

    Raises:
        ValueError: If the tenant_id is invalid.

    """
    if not tenant_id:
        msg = "Invalid tenant_id: must not be empty."
        raise ValueError(msg)

    if not re.match(settings.tenant_id_pattern, tenant_id):
        msg = f"Invalid tenant_id: '{tenant_id}'. Must match pattern: {settings.tenant_id_pattern}"
        raise ValueError(msg)


class TenantService:
    """Service for managing tenant schemas in PostgreSQL.

    Each tenant receives a dedicated PostgreSQL schema containing its own
    ``aviator`` table and HNSW index.
    """

    @staticmethod
    def _schema_exists(schema_name: str) -> bool:
        """Check whether a PostgreSQL schema exists."""
        raw_conn = _get_raw_connection_string()
        with psycopg.connect(raw_conn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s",
                (schema_name,),
            )
            return cur.fetchone() is not None

    def tenant_exists(self, tenant_id: str) -> bool:
        """Check whether a tenant schema exists.

        Args:
            tenant_id: The tenant identifier.

        Returns:
            ``True`` if the schema exists, ``False`` otherwise.

        """
        schema_name = tenant_id_to_schema_name(tenant_id)
        return self._schema_exists(schema_name)

    def require_tenant(self, tenant_id: str) -> None:
        """Validate tenant_id format and verify the schema exists.

        Args:
            tenant_id: The tenant identifier to check.

        Raises:
            ValueError: If the tenant_id format is invalid.
            TenantNotFoundError: If the tenant schema does not exist.

        """
        _validate_tenant_id(tenant_id)
        if not self.tenant_exists(tenant_id):
            msg = f"Tenant '{tenant_id}' not found"
            raise TenantNotFoundError(msg)

    def ensure_tenant(self, tenant_id: str) -> None:
        """Ensure a tenant schema exists, creating it if necessary.

        Safe to call concurrently - handles the race condition where two
        workers try to create the same schema simultaneously.

        Args:
            tenant_id: The tenant identifier.

        """
        if self.tenant_exists(tenant_id):
            return
        try:
            self.create_tenant(tenant_id)
            logger.info("Auto-created tenant schema for tenant '%s'", tenant_id)
        except TenantAlreadyExistsError:
            # Another worker created it between our exists-check and create call
            logger.debug("Tenant '%s' already exists (race condition handled)", tenant_id)

    def create_tenant(self, tenant_id: str) -> TenantInfo:
        """Create a new tenant schema with a vector store table.

        1. Validates the tenant_id
        2. Creates the PostgreSQL schema (``tenant_{tenant_id}``)
        3. Initialises the vector store table
        4. Creates tenant-scoped ORM tables
        5. Creates tenant-scoped summary tables

        Args:
            tenant_id: Unique identifier for the tenant.

        Returns:
            TenantInfo with the created schema details.

        Raises:
            TenantAlreadyExistsError: If the schema already exists.
            ValueError: If tenant_id is invalid.

        """
        _validate_tenant_id(tenant_id)
        schema_name = tenant_id_to_schema_name(tenant_id)

        logger.info("Creating tenant schema '%s' for tenant '%s'", schema_name, tenant_id)

        raw_conn = _get_raw_connection_string()

        # 1. Create the schema
        if self._schema_exists(schema_name):
            msg = f"Tenant '{tenant_id}' already exists"
            raise TenantAlreadyExistsError(msg)

        try:
            with psycopg.connect(raw_conn) as conn:
                conn.autocommit = True
                with conn.cursor() as cur:
                    cur.execute(f"CREATE SCHEMA {psycopg.sql.Identifier(schema_name).as_string(conn)}")
        except Exception as e:
            msg = f"Failed to create schema '{schema_name}': {e}"
            logger.error(msg)
            raise TenantAlreadyExistsError(msg) from e

        # 2. Initialise vector store table inside the schema
        try:
            dsn = _get_raw_connection_string()
            init_vector_table(
                dsn=dsn,
                schema=schema_name,
                table=settings.vector_store_table_name,
                vector_size=settings.vector_size,
                metadata_json_column=settings.vector_store_metadata_column,
            )
            create_indexes(
                dsn=dsn,
                schema=schema_name,
                table=settings.vector_store_table_name,
                metadata_json_column=settings.vector_store_metadata_column,
                index_type=settings.vector_index_type,
                hnsw_m=settings.hnsw_m,
                hnsw_ef_construction=settings.hnsw_ef_construction,
                ivfflat_lists=settings.ivfflat_lists,
            )

            # 3. Create database model tables (tenant_lookup_config, mcp_server_configurations)
            from sqlalchemy import create_engine

            from aviator.database.models import Base as DatabaseBase

            conn_str = _get_connection_string()
            engine = create_engine(conn_str)
            with engine.begin() as conn:
                mapped = conn.execution_options(schema_translate_map={None: schema_name})
                DatabaseBase.metadata.create_all(mapped, checkfirst=True)
            engine.dispose()

            # 4. Create LangGraph checkpoint tables in the tenant schema
            if settings.checkpointer == "postgres":
                from aviator.database.checkpointer import setup_checkpoint_tables_sync

                setup_checkpoint_tables_sync(schema_name=schema_name, dsn=dsn)

            logger.info("Tenant '%s' created successfully", tenant_id)
        except Exception as e:
            # Rollback: drop the schema if table init failed
            logger.error("Failed to init vector store for tenant '%s': %s. Rolling back schema.", tenant_id, e)
            try:
                self.delete_tenant(tenant_id)
            except Exception:
                logger.exception("Rollback failed for tenant '%s'", tenant_id)
            msg = f"Failed to initialise vector store for tenant '{tenant_id}': {e}"
            raise RuntimeError(msg) from e

        # 5. Create summary tables in the tenant schema via the shared database adapter
        try:
            db = database_manager.get()
            db.create_tables(schema_name)
            logger.info("Summary table created in schema '%s'", schema_name)
        except Exception as e:
            logger.error("Failed to create summary table for tenant '%s': %s. Rolling back schema.", tenant_id, e)
            try:
                self.delete_tenant(tenant_id)
            except Exception:
                logger.exception("Rollback failed for tenant '%s'", tenant_id)
            msg = f"Failed to create summary table for tenant '{tenant_id}': {e}"
            raise RuntimeError(msg) from e

        return TenantInfo(tenant_id=tenant_id)

    def delete_tenant(self, tenant_id: str) -> None:
        """Delete a tenant schema and all its data.

        Args:
            tenant_id: The tenant to delete.

        Raises:
            TenantNotFoundError: If the schema does not exist.
            ValueError: If tenant_id is invalid.

        """
        _validate_tenant_id(tenant_id)
        schema_name = tenant_id_to_schema_name(tenant_id)

        logger.info("Deleting tenant schema '%s' for tenant '%s'", schema_name, tenant_id)

        raw_conn = _get_raw_connection_string()
        if not self._schema_exists(schema_name):
            msg = f"Tenant '{tenant_id}' not found"
            raise TenantNotFoundError(msg)

        try:
            with psycopg.connect(raw_conn) as conn:
                conn.autocommit = True
                with conn.cursor() as cur:
                    cur.execute(f"DROP SCHEMA {psycopg.sql.Identifier(schema_name).as_string(conn)} CASCADE")
        except Exception as e:
            msg = f"Failed to delete tenant '{tenant_id}': {e}"
            logger.error(msg)
            raise RuntimeError(msg) from e

        logger.info("Tenant '%s' deleted successfully", tenant_id)

    def get_tenant(self, tenant_id: str) -> TenantInfo:
        """Get information about a tenant.

        Args:
            tenant_id: The tenant identifier.

        Returns:
            TenantInfo for the tenant.

        Raises:
            TenantNotFoundError: If the schema does not exist.

        """
        _validate_tenant_id(tenant_id)
        schema_name = tenant_id_to_schema_name(tenant_id)

        raw_conn = _get_raw_connection_string()
        with psycopg.connect(raw_conn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s",
                (schema_name,),
            )
            if not cur.fetchone():
                msg = f"Tenant '{tenant_id}' not found"
                raise TenantNotFoundError(msg)

        return TenantInfo(tenant_id=tenant_id)

    def list_tenants(self) -> list[TenantInfo]:
        """List all tenant schemas.

        Returns:
            List of TenantInfo for every schema matching the tenant prefix.

        """
        raw_conn = _get_raw_connection_string()
        tenants: list[TenantInfo] = []

        with psycopg.connect(raw_conn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT schema_name FROM information_schema.schemata WHERE schema_name LIKE %s ORDER BY schema_name",
                (f"{settings.tenant_schema_prefix}%",),
            )
            for (schema_name,) in cur.fetchall():
                tenant_id = schema_name.removeprefix(settings.tenant_schema_prefix)
                tenants.append(TenantInfo(tenant_id=tenant_id))

        return tenants


# Module-level singleton
tenant_service = TenantService()
