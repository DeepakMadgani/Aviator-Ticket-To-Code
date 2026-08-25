"""Abstract base adapter for database backends."""

from abc import ABC, abstractmethod

from sqlalchemy.orm import Session


class DatabaseAdapter(ABC):
    """Abstract base for all database adapters.

    Concrete subclasses encapsulate *how*
    to talk to a specific backend (Postgres etc.).
    ``DatabaseManager`` always works against this interface — it never
    imports a concrete adapter directly.
    """

    @abstractmethod
    def setup(self) -> None:
        """Initialise the backend (create engine, tables, etc.)."""

    @abstractmethod
    def get_session(self, schema_name: str | None = None) -> Session:
        """Return a new ``Session`` scoped to *schema_name*.

        The caller is responsible for closing the session (or use
        ``DatabaseManager.session()`` which handles that automatically).

        Args:
            schema_name: Optional PostgreSQL schema.  ``None`` uses the
                backend default.

        Returns:
            An open SQLAlchemy ``Session``.

        """

    @abstractmethod
    def create_tables(self, schema_name: str | None = None) -> None:
        """Create ORM tables, optionally routed to *schema_name*."""

    @abstractmethod
    def close(self) -> None:
        """Release all backend resources (connection pool, engine, etc.)."""
