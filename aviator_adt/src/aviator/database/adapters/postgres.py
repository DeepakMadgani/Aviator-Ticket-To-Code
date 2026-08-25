"""PostgreSQL database adapter."""

import logging

from sqlalchemy.orm import Session

from aviator.database.adapters.base import DatabaseAdapter
from aviator.database.postgres_adapter import PostgresAdapter
from aviator.settings import settings

logger = logging.getLogger(__name__)


class PostgresDatabaseAdapter(DatabaseAdapter):
    """Concrete database adapter backed by PostgreSQL (via SQLAlchemy).

    Thin wrapper around the existing ``PostgresAdapter`` so that
    ``DatabaseManager`` never imports ``PostgresAdapter`` directly —
    it only ever sees the ``DatabaseAdapter`` interface.
    """

    def __init__(self) -> None:
        """Initialize the PostgreSQL database adapter."""
        connection_string = settings.postgres_connection.unicode_string()
        self._adapter = PostgresAdapter(connection_string)

    def setup(self) -> None:
        """Initialise PostgreSQL engine and create ORM tables."""
        self._adapter.setup()

    def get_session(self, schema_name: str | None = None) -> Session:
        """Return a SQLAlchemy session, optionally scoped to *schema_name*."""
        return self._adapter.get_session(schema_name=schema_name)

    def create_tables(self, schema_name: str | None = None) -> None:
        """Create ORM tables, optionally routed to *schema_name*."""
        self._adapter.create_tables(schema_name=schema_name)

    def close(self) -> None:
        """Dispose the connection pool."""
        self._adapter.close()
