"""Database engine and session management for usage tracking.

Provides a singleton :class:`UsageTrackingDB` that wraps SQLAlchemy engines
(async **and** sync) and exposes context-managed sessions with automatic
``schema_translate_map`` support for multi-tenant schemas.
"""

import asyncio
import logging
import sys
import threading
from contextlib import asynccontextmanager, contextmanager

from opentelemetry import trace
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema

from aviator.services.tenant import tenant_id_to_schema_name
from aviator.services.usage_tracking.orm import (
    Base,
    UsageDailyTally,
    UsageTransaction,
)
from aviator.settings import settings

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

# Database connection string prefixes
_POSTGRES_PREFIX = "postgresql://"
_PSYCOPG_PREFIX = "postgresql+psycopg://"

# Tables that exist per-tenant schema (schema=None, translated at runtime)
_TENANT_TABLES = [UsageTransaction.__table__, UsageDailyTally.__table__]


def _get_connection_url() -> str:
    """Return a SQLAlchemy-compatible connection URL.

    Replaces the standard ``postgresql://`` prefix with ``postgresql+psycopg://``
    so that SQLAlchemy uses the *psycopg 3* driver for both sync and async I/O.
    """
    return settings.postgres_connection.encoded_string().replace(_POSTGRES_PREFIX, _PSYCOPG_PREFIX)


def sanitize_schema_name(tenant_id: str) -> str:
    """Derive a database schema name from a tenant ID.

    Delegates to :func:`tenant_id_to_schema_name` so that schema naming
    is consistent across the entire application.

    Args:
        tenant_id: The raw tenant identifier.

    Returns:
        The schema name string.

    """
    return tenant_id_to_schema_name(tenant_id)


# ---------------------------------------------------------------------------
# Helpers for create_all inside run_sync / sync contexts
# ---------------------------------------------------------------------------


def _create_all_default_schema(sync_conn) -> None:  # noqa: ANN001
    """Create **all** usage-tracking tables in the default schema."""
    sync_conn.execute(CreateSchema(settings.default_schema, if_not_exists=True))
    mapped = sync_conn.execution_options(schema_translate_map={None: settings.default_schema})
    Base.metadata.create_all(mapped, checkfirst=True)


def _create_tenant_tables(sync_conn, schema: str) -> None:  # noqa: ANN001
    """Create only the per-tenant tables in *schema*."""
    mapped = sync_conn.execution_options(schema_translate_map={None: schema})
    Base.metadata.create_all(mapped, tables=_TENANT_TABLES, checkfirst=True)


class UsageTrackingDB:
    """Manages SQLAlchemy engines and session factories for usage tracking.

    The class maintains separate async and sync engines so that it can be
    used from both the FastAPI request path (async) and the Celery worker
    path (sync).  The sync engine is created lazily on first use.
    """

    def __init__(self) -> None:
        """Initialise the DB manager (engines are created later)."""
        self._async_engine = None
        self._sync_engine = None
        self._schema_cache: dict[str, str] = {}
        self._schema_cache_sync: dict[str, str] = {}
        self._lock = threading.Lock()

    def use_sync_fallback(self) -> bool:
        """Return whether async psycopg should be avoided in this process.

        psycopg async connections are not compatible with the Windows Proactor
        event loop that is commonly used by local dev launches, so on Windows we
        route usage-tracking operations through the existing sync engine.
        """
        return sys.platform == "win32"

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Create the async engine and bootstrap the ``public`` schema tables."""
        if self.use_sync_fallback():
            await asyncio.to_thread(self._get_sync_engine)
            logger.info("Usage tracking initialized using sync engine fallback")
            return

        url = _get_connection_url()
        self._async_engine = create_async_engine(
            url,
            pool_size=settings.usage_tracking_pool_size,
            max_overflow=2,
        )
        logger.info("Usage tracking async engine created")

        async with self._async_engine.begin() as conn:
            await conn.run_sync(_create_all_default_schema)

    async def close(self) -> None:
        """Dispose of all engines and release connection pools."""
        if self._async_engine:
            await self._async_engine.dispose()
            logger.info("Usage tracking async engine disposed")
        if self._sync_engine:
            self._sync_engine.dispose()
            logger.info("Usage tracking sync engine disposed")

    def _get_sync_engine(self):  # noqa: ANN202
        """Lazily create and return the synchronous engine.

        On first invocation the default schema tables are also created
        (``checkfirst=True`` makes this a no-op when they already exist).
        """
        if self._sync_engine is None:
            url = _get_connection_url()
            self._sync_engine = create_engine(
                url,
                pool_size=settings.usage_tracking_pool_size,
                max_overflow=2,
            )
            with self._sync_engine.begin() as conn:
                _create_all_default_schema(conn)
            logger.info("Usage tracking sync engine created")
        return self._sync_engine

    # ------------------------------------------------------------------
    # Session factories
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def async_session(self, schema: str | None = None):  # noqa: ANN201
        """Provide an async session scoped to *schema*.

        Uses ``schema_translate_map`` so that ORM models with
        ``schema=None`` are directed to the requested schema.
        """
        if self._async_engine is None:
            msg = "Usage tracking DB not initialized"
            raise RuntimeError(msg)
        schema_name = schema or settings.default_schema
        engine = self._async_engine.execution_options(
            schema_translate_map={None: schema_name},
        )
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session

    @contextmanager
    def sync_session(self, schema: str | None = None):  # noqa: ANN201
        """Provide a sync session scoped to *schema*."""
        schema_name = schema or settings.default_schema
        engine = self._get_sync_engine().execution_options(
            schema_translate_map={None: schema_name},
        )
        with Session(engine, expire_on_commit=False) as session:
            yield session

    # ------------------------------------------------------------------
    # Tenant schema management
    # ------------------------------------------------------------------

    @tracer.start_as_current_span("ensure_tenant_schema")
    async def ensure_tenant_schema(self, tenant_id: str | None) -> str:
        """Resolve or create the schema for a tenant (async).

        Derives the schema name deterministically from the tenant ID via
        :func:`~aviator.services.tenant.tenant_id_to_schema_name` and
        creates the schema and per-tenant tables if they do not yet exist.

        Args:
            tenant_id: The tenant identifier.  ``None`` / empty → default schema.

        Returns:
            The database schema name to use for this tenant.

        """
        if not tenant_id:
            return settings.default_schema

        if tenant_id in self._schema_cache:
            return self._schema_cache[tenant_id]

        if self._async_engine is None:
            msg = "Usage tracking DB not initialized"
            raise RuntimeError(msg)

        schema_name = sanitize_schema_name(tenant_id)

        # Create the schema and its tables (idempotent)
        async with self._async_engine.begin() as conn:
            await conn.execute(CreateSchema(schema_name, if_not_exists=True))
            await conn.run_sync(_create_tenant_tables, schema_name)

        logger.info("Ensured tenant schema %s for tenant %s", schema_name, tenant_id)
        self._schema_cache[tenant_id] = schema_name
        return schema_name

    @tracer.start_as_current_span("ensure_tenant_schema_sync")
    def ensure_tenant_schema_sync(self, tenant_id: str | None) -> str:
        """Resolve or create the schema for a tenant (sync, for Celery)."""
        if not tenant_id:
            return settings.default_schema

        with self._lock:
            if tenant_id in self._schema_cache_sync:
                return self._schema_cache_sync[tenant_id]

        schema_name = sanitize_schema_name(tenant_id)
        engine = self._get_sync_engine()

        # Create the schema and its tables (idempotent)
        with engine.begin() as conn:
            conn.execute(CreateSchema(schema_name, if_not_exists=True))
            _create_tenant_tables(conn, schema_name)

        logger.info(
            "Ensured tenant schema (sync) %s for tenant %s",
            schema_name,
            tenant_id,
        )
        with self._lock:
            self._schema_cache_sync[tenant_id] = schema_name
        return schema_name


# Module-level singleton
usage_tracking_db = UsageTrackingDB()
