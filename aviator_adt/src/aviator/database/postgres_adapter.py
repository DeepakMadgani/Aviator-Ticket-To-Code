"""PostgreSQL database adapter."""

import logging

import sqlalchemy
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from aviator.database.models import Base
from aviator.settings import settings

logger = logging.getLogger(__name__)


class PostgresAdapter:
    """PostgreSQL adapter - provides session access."""

    def __init__(self, connection_string: str) -> None:
        """Initialize the PostgreSQL adapter.

        Args:
            connection_string (str):
                Database connection string.

        """
        self.connection_string = connection_string
        self._engine = None
        self._SessionLocal = None
        # Single source of truth — applied to every session (public and tenant)
        self._session_kwargs: dict = {
            "autoflush": False,
            "expire_on_commit": False,
        }

    def create_tables(self, schema_name: str | None = None) -> None:
        """Create tables in a specific schema."""

        if not self._engine:
            msg = "Database not initialized."
            raise RuntimeError(msg)

        with self._engine.begin() as conn:
            if schema_name:
                conn.execute(sqlalchemy.text(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"'))
                conn = conn.execution_options(schema_translate_map={None: schema_name})

            Base.metadata.create_all(bind=conn)

    def setup(self) -> None:
        """Set up PostgreSQL connection and create tables."""
        logger.info("Setting up database tables...")

        try:
            # Create engine
            conn_settings = settings.postgres_connection_settings
            self._engine = create_engine(
                self.connection_string,
                pool_size=conn_settings.get("pool_size", 10),
                max_overflow=conn_settings.get("max_overflow", 20),
                pool_timeout=conn_settings.get("pool_timeout", 10),
                pool_recycle=conn_settings.get("pool_recycle", 1800),
                pool_pre_ping=conn_settings.get("pool_pre_ping", True),
                pool_use_lifo=conn_settings.get("pool_use_lifo", True),
                connect_args=conn_settings.get("connect_args", {}),
                echo=False,  # Set to True for SQL debugging
            )

            # Create session factory — engine as positional arg (SA 2.0 compatible)
            self._SessionLocal = sessionmaker(self._engine, **self._session_kwargs)

            # Create tables if they don't exist
            self.create_tables(settings.default_schema)

            logger.info("Database setup completed successfully")

        except Exception as e:
            logger.error("Error setting up database: %s", e)
            raise

    def get_session(self, schema_name: str | None = None) -> Session:
        """Get a new database session.

        When *schema_name* is provided the session is scoped to that PostgreSQL
        schema via ``schema_translate_map``, routing all ORM queries for tables
        with no explicit schema to *schema_name* instead of the default.

        Args:
            schema_name:
                Optional PostgreSQL schema name.  Pass a tenant schema (e.g.
                ``"tenant_acme"``) to read/write tenant-specific tables.

        Returns:
            Session:
                SQLAlchemy session instance.

        Raises:
            RuntimeError:
                If database not initialized.

        """
        if not self._SessionLocal:
            msg = "Database not initialized. Call setup() first."
            raise RuntimeError(msg)
        if schema_name:
            logger.info(
                "[PostgresAdapter] Tenant session — schema_translate_map={None: '%s'}",
                schema_name,
            )
            engine = self._engine.execution_options(schema_translate_map={None: schema_name})
            return Session(engine, **self._session_kwargs)
        logger.info("[PostgresAdapter] Public session — no schema_translate_map")
        return self._SessionLocal()

    def close(self) -> None:
        """Close database connections."""
        if self._engine:
            self._engine.dispose()
            logger.info("Database connections closed")
