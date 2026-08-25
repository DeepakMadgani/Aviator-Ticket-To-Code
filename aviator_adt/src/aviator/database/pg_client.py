"""Async PostgreSQL pool manager and reusable query helpers."""

import asyncio
import logging
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from aviator.settings import settings

logger = logging.getLogger(__name__)

Params = tuple[Any, ...] | dict[str, Any] | None
_pool_lock = asyncio.Lock()


class PgConnectionPool:
    """Singleton manager for the PostgreSQL async connection pool."""

    _pool: AsyncConnectionPool | None = None

    @classmethod
    async def get_pool(cls) -> AsyncConnectionPool:
        """Return the shared pool, creating it on first call."""
        if cls._pool is None:
            async with _pool_lock:
                if cls._pool is None:  # Double-check after acquiring lock
                    try:
                        pool_size = settings.postgres_connection_settings.get("pool_size", 10)
                        pool = AsyncConnectionPool(
                            conninfo=settings.postgres_connection.encoded_string(),
                            open=False,
                            max_size=pool_size,
                            check=AsyncConnectionPool.check_connection,
                            kwargs={
                                "autocommit": True,
                                "connect_timeout": 5,
                                "prepare_threshold": None,
                            },
                        )
                        await pool.open()
                        cls._pool = pool
                        logger.info("PgConnectionPool: created with max_size=%s", pool_size)
                    except Exception as e:
                        logger.error("PgConnectionPool: failed to initialise — %s", e)
                        raise

        return cls._pool

    @classmethod
    async def close_pool(cls) -> None:
        """Close all connections and discard the pool."""
        if cls._pool is not None:
            await cls._pool.close()
            cls._pool = None
            logger.info("PgConnectionPool: closed")


async def fetch_all(query: str, params: Params = None) -> list[dict[str, Any]]:
    """Execute a SELECT and return all matching rows as dicts.

    Parameters
    ----------
    query:
        Parameterised SQL string (use ``%s`` or ``%(name)s`` placeholders).
    params:
        Positional tuple or named dict of bind values, or ``None``.

    Returns
    -------
    list[dict[str, Any]]
        List of rows; empty list when no rows match.

    """
    pool = await PgConnectionPool.get_pool()
    async with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(query, params)
        return await cur.fetchall()


async def fetch_one(query: str, params: Params = None) -> dict[str, Any] | None:
    """Execute a SELECT and return the first matching row as a dict.

    Parameters
    ----------
    query:
        Parameterised SQL string.
    params:
        Bind values, or ``None``.

    Returns
    -------
    dict[str, Any] | None
        First row, or ``None`` when no rows match.

    """
    pool = await PgConnectionPool.get_pool()
    async with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(query, params)
        return await cur.fetchone()


async def execute(query: str, params: Params = None) -> int:
    """Execute an INSERT, UPDATE, or DELETE statement.

    Parameters
    ----------
    query:
        Parameterised SQL string.
    params:
        Bind values, or ``None``.

    Returns
    -------
    int
        Number of rows affected.

    """
    pool = await PgConnectionPool.get_pool()
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(query, params)
        return cur.rowcount


async def execute_batch(statements: list[tuple[str, Params]]) -> int:
    """Execute multiple DML statements sequentially on one pooled connection.

    Parameters
    ----------
    statements:
        List of ``(query, params)`` pairs executed in order. All statements
        share one connection and are not wrapped in an explicit transaction;
        with ``autocommit=True``, each statement commits independently.

    Returns
    -------
    int
        Total number of rows affected across all statements.

    """
    pool = await PgConnectionPool.get_pool()
    total = 0
    async with pool.connection() as conn, conn.cursor() as cur:
        for query, params in statements:
            await cur.execute(query, params)
            total += cur.rowcount
    return total


async def execute_returning(query: str, params: Params = None) -> list[dict[str, Any]]:
    """Execute an INSERT / UPDATE / DELETE … RETURNING and return the rows.

    Parameters
    ----------
    query:
        Parameterised SQL string with a ``RETURNING`` clause.
    params:
        Bind values, or ``None``.

    Returns
    -------
    list[dict[str, Any]]
        Rows produced by the ``RETURNING`` clause.

    """
    pool = await PgConnectionPool.get_pool()
    async with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(query, params)
        return await cur.fetchall()
