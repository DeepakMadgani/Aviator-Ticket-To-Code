"""Tenant-aware LangGraph checkpointer.

Extends ``AsyncPostgresSaver`` to route checkpoint operations to the
correct PostgreSQL schema based on a :class:`contextvars.ContextVar`.

API endpoints set :data:`checkpointer_schema` before invoking the graph;
the overridden ``_cursor`` context manager transparently sets ``search_path``
so all checkpoint SQL operates in the target tenant schema.
"""

import contextvars
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import psycopg
import psycopg.errors
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import sql

from aviator.settings import settings

logger = logging.getLogger(__name__)

# Context variable holding the current tenant schema for checkpoint operations.
# Set by API endpoints before invoking the graph; unset falls back to
# ``settings.default_schema``.
checkpointer_schema: contextvars.ContextVar[str] = contextvars.ContextVar("checkpointer_schema")


class TenantAwarePostgresSaver(AsyncPostgresSaver):
    """AsyncPostgresSaver subclass that routes to tenant schemas via ``search_path``.

    Before each cursor operation, the PostgreSQL ``search_path`` is set to the
    schema stored in :data:`checkpointer_schema`.  The path is reset after the
    operation completes so pooled connections are returned in a clean state.

    When ``multi_tenant_enabled`` is ``False`` (or the context variable is not
    set), the checkpointer falls back to ``settings.default_schema`` and no
    ``SET`` is executed — behaviour is identical to the vanilla
    ``AsyncPostgresSaver``.
    """

    # Tracks schemas where checkpoint tables have already been verified/created
    # so we only pay the setup cost once per schema per process lifetime.
    _initialised_schemas: set[str] = set()

    @asynccontextmanager
    async def _cursor(self, *, pipeline: bool = False) -> AsyncIterator:
        """Wrap the parent cursor with per-tenant ``search_path`` management."""
        schema = checkpointer_schema.get(settings.default_schema)
        needs_switch = settings.multi_tenant_enabled and schema != settings.default_schema

        async with super()._cursor(pipeline=pipeline) as cur:
            if needs_switch:
                await cur.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))

                # Lazily ensure checkpoint tables exist in this tenant schema.
                if schema not in self._initialised_schemas:
                    try:
                        await cur.execute("SELECT 1 FROM checkpoint_migrations LIMIT 1")
                    except psycopg.errors.UndefinedTable:
                        # Table doesn't exist yet — run the full migration suite.
                        logger.info("Checkpoint tables missing in schema '%s'; running setup…", schema)
                        # Reset the connection error state before running DDL.
                        await cur.execute("ROLLBACK")
                        for migration in self.MIGRATIONS:
                            await cur.execute(migration)
                    self._initialised_schemas.add(schema)

            try:
                yield cur
            finally:
                if needs_switch:
                    await cur.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(settings.default_schema)))


def setup_checkpoint_tables_sync(schema_name: str, dsn: str) -> None:
    """Create LangGraph checkpoint tables in a tenant schema (synchronous).

    Mirrors the ``AsyncPostgresSaver.setup()`` migration logic using a
    synchronous ``psycopg`` connection so it can be called from
    ``TenantService.create_tenant()``.

    Args:
        schema_name: PostgreSQL schema to initialise checkpoint tables in.
        dsn: Database connection string (``postgresql://…``).

    """
    migrations = AsyncPostgresSaver.MIGRATIONS

    with psycopg.connect(dsn) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            # Route all DDL into the target schema
            cur.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema_name)))

            # Migration 0: checkpoint_migrations tracking table
            cur.execute(migrations[0])

            # Determine current migration version
            cur.execute("SELECT v FROM checkpoint_migrations ORDER BY v DESC LIMIT 1")
            row = cur.fetchone()
            version = row[0] if row else -1

            # Apply pending migrations
            for v, migration in zip(
                range(version + 1, len(migrations)),
                migrations[version + 1 :],
                strict=False,
            ):
                cur.execute(migration)
                cur.execute("INSERT INTO checkpoint_migrations (v) VALUES (%s)", (v,))

            # Reset search_path so the connection is returned clean
            cur.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(settings.default_schema)))

    logger.info("Checkpoint tables created in schema '%s'", schema_name)
