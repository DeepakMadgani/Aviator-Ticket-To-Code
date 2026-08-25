"""Database connection pool service for PostgreSQL operations."""

import logging

from psycopg_pool import AsyncConnectionPool

from aviator.settings import settings

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Singleton manager for PostgreSQL connection pool."""

    _pool: AsyncConnectionPool | None = None

    @classmethod
    async def get_pool(cls) -> AsyncConnectionPool:
        """Get or create the global PostgreSQL connection pool.

        The pool is created on first access and reused for the lifetime of the application.
        This ensures efficient resource usage with connection reuse across all services.

        Returns:
            AsyncConnectionPool: The shared database connection pool.

        Raises:
            Exception: If pool initialization fails.

        """
        if cls._pool is None:
            try:
                # connection_url = (
                #     "postgresql://"
                #     f"{quote_plus(settings.postgres_user)}:{quote_plus(settings.postgres_password)}"
                #     f"@{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_database}"
                # )
                cls._pool = AsyncConnectionPool(
                    conninfo=settings.postgres_connection.encoded_string(),
                    open=False,
                    max_size=settings.postgres_pool_size,
                    check=AsyncConnectionPool.check_connection,
                    kwargs={
                        "autocommit": True,
                        "connect_timeout": 5,
                        "prepare_threshold": None,
                    },
                )
                await cls._pool.open()
                logger.info("Database connection pool created with max_size=%s", settings.postgres_pool_size)
            except Exception as e:
                logger.error("Failed to create database connection pool: %s", e)
                raise

        return cls._pool

    @classmethod
    async def close_pool(cls) -> None:
        """Close and cleanup the global database connection pool.

        Should be called during application shutdown to properly close all connections.
        """
        if cls._pool is not None:
            await cls._pool.close()
            cls._pool = None
            logger.info("Database connection pool closed")
