"""Database module with ORM support."""

import logging
from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy.orm import Session

from aviator.database.adapters.base import DatabaseAdapter
from aviator.database.factory import DatabaseAdapterFactory
from aviator.database.pg_client import PgConnectionPool, execute, execute_returning, fetch_all, fetch_one

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Generic manager for relational database access.

    * Uses ``DatabaseAdapterFactory`` to create the concrete adapter —
      ``DatabaseManager`` never imports a backend directly.
    * ``get()`` returns the raw adapter (legacy / low-level use).
    * ``session(schema_name)`` is a context manager that yields a ``Session``
      scoped to the requested schema and guarantees it is closed on exit.

    Only infrastructure concerns live here — no table-specific or domain logic.
    """

    def __init__(self) -> None:
        """Initialise the manager (no adapter created yet — lazy init)."""
        self._adapter: DatabaseAdapter | None = None

    def setup_database(self) -> None:
        """Initialise the database adapter and create ORM tables."""
        if self._adapter is not None:
            logger.info("Database already initialized")
            return
        logger.info("Setting up database...")
        self._adapter = DatabaseAdapterFactory.create_adapter()
        self._adapter.setup()

    def get(self) -> DatabaseAdapter:
        """Return the underlying ``DatabaseAdapter`` (legacy / low-level access).

        Prefer ``session()`` for all new code.
        """
        if self._adapter is None:
            logger.warning("Database adapter not initialized. Creating one now...")
            self._adapter = DatabaseAdapterFactory.create_adapter()
            self._adapter.setup()
        return self._adapter

    @contextmanager
    def session(self, schema_name: str | None = None) -> Generator[Session]:
        """Context manager that yields a ``Session`` scoped to *schema_name*.

        Callers specify the schema they need and receive a ready-to-use object; session lifecycle
        is handled here, not by the caller.

        Usage::

            with database_manager.session(schema_name) as db:
                result = MyRepository(db).some_operation()

        Args:
            schema_name: PostgreSQL schema name (e.g. ``"tenant_acme"``).
                         ``None`` uses the adapter default (public schema).

        Yields:
            A ``Session`` bound to the requested schema.

        """
        logger.info("[DB session] Opening session — schema=%s", schema_name)
        db: Session = self.get().get_session(schema_name=schema_name)
        try:
            yield db
        except Exception:
            db.rollback()
            raise
        finally:
            logger.info("[DB session] Closing session — schema=%s", schema_name)
            db.close()

    def reset(self) -> None:
        """Reset the database instance (for testing)."""
        if self._adapter:
            self._adapter.close()
        self._adapter = None


# Singleton instance
database_manager = DatabaseManager()

__all__ = [
    "DatabaseAdapter",
    "PgConnectionPool",
    "database_manager",
    "execute",
    "execute_returning",
    "fetch_all",
    "fetch_one",
]
