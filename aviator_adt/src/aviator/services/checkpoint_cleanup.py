"""Retention cleanup for LangGraph checkpoint tables.

This module identifies stale threads from ``checkpoints`` and deletes matching
rows from related checkpoint tables in batches.  When multi-tenant mode is
enabled, the cleanup iterates over every schema that contains checkpoint
tables so no tenant is missed.
"""

import datetime
import logging

from opentelemetry import trace
from psycopg import sql
from psycopg.rows import dict_row

from aviator.database.pg_client import PgConnectionPool, execute_batch, fetch_all
from aviator.settings import settings

tracer = trace.get_tracer(__name__)
logger = logging.getLogger(__name__)

_BATCH_SIZE = 5000
_CHECKPOINT_TABLES = ("checkpoint_writes", "checkpoint_blobs", "checkpoints")


async def _get_checkpoint_schemas() -> list[str]:
    """Return all schemas that contain a ``checkpoints`` table.

    Always includes the default schema.  When ``multi_tenant_enabled`` is True,
    also discovers tenant schemas by querying ``information_schema.tables``.
    """
    schemas = [settings.default_schema]
    if settings.multi_tenant_enabled:
        rows = await fetch_all(
            "SELECT DISTINCT table_schema FROM information_schema.tables "
            "WHERE table_name = 'checkpoints' AND table_schema LIKE %s ORDER BY table_schema",
            (f"{settings.tenant_schema_prefix}%",),
        )
        schemas.extend(row["table_schema"] for row in rows)
    return schemas


@tracer.start_as_current_span("get_stale_thread_ids")
async def _get_stale_thread_ids(cutoff: datetime.datetime) -> list[str]:
    """Return thread IDs whose latest checkpoint timestamp is older than ``cutoff``.

    Operates on the current ``search_path`` (default schema when called
    from :func:`cleanup_old_checkpoints`).
    """
    rows = await fetch_all(
        """
        SELECT thread_id
        FROM checkpoints
        GROUP BY thread_id
        HAVING MAX((checkpoint->>'ts')::timestamptz) < %s
        """,
        (cutoff,),
    )
    return [row["thread_id"] for row in rows]


@tracer.start_as_current_span("delete_checkpoint_batch")
async def _delete_checkpoint_batch(thread_ids: list[str]) -> int:
    """Delete all checkpoint rows for the given batch of thread_ids.

    Deletes dependent rows first (``checkpoint_writes``, then
    ``checkpoint_blobs``) and finally ``checkpoints`` using one connection to
    minimize pool overhead.
    Returns the total number of rows deleted across all three tables.
    """
    ids = list(thread_ids)  # ensure it is a plain list for psycopg array adaption
    deleted = await execute_batch(
        [
            ("DELETE FROM checkpoint_writes WHERE thread_id = ANY(%s)", (ids,)),
            ("DELETE FROM checkpoint_blobs WHERE thread_id = ANY(%s)", (ids,)),
            ("DELETE FROM checkpoints WHERE thread_id = ANY(%s)", (ids,)),
        ]
    )
    return deleted


@tracer.start_as_current_span("cleanup_schema_checkpoints")
async def _cleanup_schema(schema: str, cutoff: datetime.datetime) -> int:
    """Delete stale checkpoint rows in a single PostgreSQL schema.

    Acquires a pooled connection, sets ``search_path`` to the target schema,
    finds stale threads, batch-deletes them, and resets the path.
    """
    pool = await PgConnectionPool.get_pool()
    total_deleted = 0

    async with pool.connection() as conn:
        await conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        try:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """SELECT thread_id
                    FROM checkpoints
                    GROUP BY thread_id
                    HAVING MAX((checkpoint->>'ts')::timestamptz) < %s""",
                    (cutoff,),
                )
                rows = await cur.fetchall()

            thread_ids = [row["thread_id"] for row in rows]
            if not thread_ids:
                logger.debug("Checkpointer cleanup: no stale threads in schema '%s'", schema)
                return 0

            logger.info(
                "Checkpointer cleanup: %d stale thread(s) in schema '%s'",
                len(thread_ids),
                schema,
            )

            for i in range(0, len(thread_ids), _BATCH_SIZE):
                batch = list(thread_ids[i : i + _BATCH_SIZE])
                async with conn.cursor() as cur:
                    for table in _CHECKPOINT_TABLES:
                        await cur.execute(f"DELETE FROM {table} WHERE thread_id = ANY(%s)", (batch,))
                        total_deleted += cur.rowcount
                logger.debug(
                    "Checkpointer cleanup: batch %d-%d processed in schema '%s'",
                    i,
                    i + len(batch),
                    schema,
                )

        finally:
            await conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(settings.default_schema)))

    return total_deleted


@tracer.start_as_current_span("cleanup_old_checkpoints")
async def cleanup_old_checkpoints() -> int:
    """Delete checkpoint rows for threads idle longer than retention policy.

    1. Discovers all schemas containing checkpoint tables.
    2. For each schema, queries ``checkpoints`` for thread_ids whose latest
       ``checkpoint->>'ts'`` is older than ``settings.checkpointer_retention_hours``.
    3. Batches the result into groups of :data:`_BATCH_SIZE` to avoid issuing
       huge single-statement deletes.
    4. Deletes matching rows from ``checkpoints``, ``checkpoint_blobs``, and
       ``checkpoint_writes``.

    Returns:
        Total number of rows deleted across all schemas, tables, and batches.

    """
    retention_hours = settings.checkpointer_retention_hours
    cutoff = datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(hours=retention_hours)

    schemas = await _get_checkpoint_schemas()
    logger.info(
        "Checkpointer cleanup: scanning %d schema(s) (retention=%d hours, cutoff=%s)",
        len(schemas),
        retention_hours,
        cutoff.isoformat(),
    )

    total_deleted = 0
    for schema in schemas:
        deleted = await _cleanup_schema(schema, cutoff)
        total_deleted += deleted

    if total_deleted:
        logger.info("Checkpointer cleanup: deleted %d total rows across %d schema(s)", total_deleted, len(schemas))
    else:
        logger.debug("Checkpointer cleanup: no stale threads found in any schema")

    return total_deleted
