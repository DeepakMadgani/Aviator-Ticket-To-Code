"""Shared vector store schema and index utilities.

Single source of truth for DDL used by the main aviator app and the
migration service.  Both ``PGVectorStoreAdapter`` and
``migration.schema_manager`` import from here.
"""

from __future__ import annotations

import logging
import time

import psycopg

from aviator.settings import settings

logger = logging.getLogger(__name__)

# Standard additional metadata columns used by the vector store table.
METADATA_COLUMNS: list[str] = ["workspace_id", "document_id", "text_hash"]
HNSW_INDEX_TYPE = "hnsw"
IVFFLAT_INDEX_TYPE = "ivfflat"
SUPPORTED_ANN_INDEX_TYPES = {HNSW_INDEX_TYPE, IVFFLAT_INDEX_TYPE}
VECTOR_STORAGE_TYPE = "vector"
HALFVEC_STORAGE_TYPE = "halfvec"
VECTOR_COSINE_OPCLASS = "vector_cosine_ops"
HALFVEC_COSINE_OPCLASS = "halfvec_cosine_ops"


def quote_ident(name: str) -> str:
    """Quote a PostgreSQL identifier (schema, table, column, index name).

    Wraps the name in double-quotes and escapes any embedded double-quote
    characters, matching PostgreSQL's standard identifier quoting rules.
    This ensures mixed-case names like ``tenant_T75`` are preserved.
    """
    return '"' + name.replace('"', '""') + '"'


# ── Schema creation ─────────────────────────────────────────────────


def create_schema_if_not_exists(dsn: str, schema_name: str) -> None:
    """Ensure a PostgreSQL schema exists, creating it if necessary.

    Also enables the ``vector`` extension (idempotent).  Uses quoted
    identifiers so mixed-case names like ``tenant_T75`` are preserved.

    Args:
        dsn: PostgreSQL connection string (plain ``postgresql://`` format).
        schema_name: Name of the schema to create.

    """
    with psycopg.connect(dsn) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cur.execute(psycopg.sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(psycopg.sql.Identifier(schema_name)))


# ── Index DDL generation ──────────────────────────────────────────────


def get_embedding_ann_index_name(table: str, index_type: str | None) -> str:
    """Return the managed ANN index name for the embedding column."""
    prefix = f"idx_{table}_embedding"

    if index_type is None:
        return prefix

    normalized_index_type = index_type.lower()
    if normalized_index_type in SUPPORTED_ANN_INDEX_TYPES:
        return f"{prefix}_{normalized_index_type}"

    return prefix


def get_embedding_storage_type(vector_storage_type: str | None) -> str:
    """Return the normalized managed embedding storage type."""
    if vector_storage_type == HALFVEC_STORAGE_TYPE:
        return HALFVEC_STORAGE_TYPE

    return VECTOR_STORAGE_TYPE


def get_embedding_opclass(vector_storage_type: str | None) -> str:
    """Return the pgvector operator class for the configured storage type."""
    if get_embedding_storage_type(vector_storage_type) == HALFVEC_STORAGE_TYPE:
        return HALFVEC_COSINE_OPCLASS

    return VECTOR_COSINE_OPCLASS


def generate_index_ddl(
    schema: str,
    table: str,
    metadata_columns: list[str] | None = None,
    metadata_json_column: str | None = None,
    index_type: str | None = None,
    hnsw_m: int | None = None,
    hnsw_ef_construction: int | None = None,
    ivfflat_lists: int | None = None,
    vector_storage_type: str | None = None,
) -> list[str]:
    """Return ``CREATE INDEX IF NOT EXISTS`` DDL statements for a vector table.

    Generates:
    - HNSW or IVFFlat index on ``embedding`` (cosine distance) based on index_type.
    - GIN index on the JSONB metadata column.
    - BTREE index on each additional metadata column.

    Args:
        schema: PostgreSQL schema name.
        table: Table name.
        metadata_columns: Extra columns to create BTREE indexes for.
            Defaults to :data:`METADATA_COLUMNS`.
        metadata_json_column: Name of the JSONB metadata column.
        index_type: Type of vector index ('hnsw' or 'ivfflat').
        hnsw_m: HNSW parameter m (max connections per layer).
        hnsw_ef_construction: HNSW parameter ef_construction (candidate list size during construction).
        ivfflat_lists: IVFFlat parameter lists (number of clusters).
        vector_storage_type: Vector column type ('vector' or 'halfvec'); affects the operator class used.

    Returns:
        A list of DDL strings ready to be executed.

    """
    if metadata_columns is None:
        metadata_columns = METADATA_COLUMNS
    if metadata_json_column is None:
        metadata_json_column = "langchain_metadata"
    if index_type is None:
        index_type = settings.vector_index_type
    if hnsw_m is None:
        hnsw_m = settings.hnsw_m
    if hnsw_ef_construction is None:
        hnsw_ef_construction = settings.hnsw_ef_construction
    if ivfflat_lists is None:
        ivfflat_lists = settings.ivfflat_lists
    if vector_storage_type is None:
        vector_storage_type = settings.vector_storage_type

    opclass = get_embedding_opclass(vector_storage_type)

    qs = quote_ident(schema)
    qt = quote_ident(table)
    prefix = f"idx_{table}"

    # Create vector index based on type
    stmts: list[str] = []

    if index_type is None:
        # No vector index requested
        pass
    elif index_type.lower() == HNSW_INDEX_TYPE:
        vector_index_ddl = (
            f"CREATE INDEX IF NOT EXISTS {quote_ident(get_embedding_ann_index_name(table, index_type))} "
            f"ON {qs}.{qt} USING {HNSW_INDEX_TYPE} (embedding {opclass}) "
            f"WITH (m = {hnsw_m}, ef_construction = {hnsw_ef_construction})"
        )
        stmts.append(vector_index_ddl)
    elif index_type.lower() == IVFFLAT_INDEX_TYPE:
        vector_index_ddl = (
            f"CREATE INDEX IF NOT EXISTS {quote_ident(get_embedding_ann_index_name(table, index_type))} "
            f"ON {qs}.{qt} USING {IVFFLAT_INDEX_TYPE} (embedding {opclass}) "
            f"WITH (lists = {ivfflat_lists})"
        )
        stmts.append(vector_index_ddl)
    else:
        logger.warning("Unknown vector index type '%s', skipping vector index creation", index_type)

    # Add metadata indexes
    stmts.append(
        f"CREATE INDEX IF NOT EXISTS {quote_ident(prefix + '_' + metadata_json_column)} "
        f"ON {qs}.{qt} USING GIN (({quote_ident(metadata_json_column)}::jsonb))"
    )
    stmts.extend(
        f"CREATE INDEX IF NOT EXISTS {quote_ident(prefix + '_' + col)} ON {qs}.{qt} USING BTREE ({quote_ident(col)})"
        for col in metadata_columns
    )
    return stmts


# Escalation steps for maintenance_work_mem when ProgramLimitExceeded is hit.
_MEM_ESCALATION_STEPS = ["1GB", "2GB", "4GB", "8GB"]


def _set_maintenance_work_mem(cur: psycopg.Cursor, value: str) -> None:
    """Set ``maintenance_work_mem`` using a safely-quoted literal.

    PostgreSQL ``SET`` does not accept parameter placeholders (``$1``),
    so the value is embedded via :class:`psycopg.sql.Literal`.
    """
    cur.execute(psycopg.sql.SQL("SET maintenance_work_mem = {}").format(psycopg.sql.Literal(value)))


def execute_with_mem_escalation(
    cur: psycopg.Cursor,
    sql: psycopg.sql.Composed | str,
    label: str,
) -> None:
    """Execute a DDL statement, escalating ``maintenance_work_mem`` on OOM.

    Tries the statement once.  If a ``ProgramLimitExceeded`` error is
    raised, retries with progressively larger ``maintenance_work_mem``
    values (1 GB → 8 GB).  ``DuplicateTable`` / ``UniqueViolation``
    errors are silently ignored (the object already exists).

    Args:
        cur: An open psycopg cursor (connection should have autocommit).
        sql: The SQL statement to execute.
        label: Human-readable label used in log messages (e.g.
            ``"HNSW index on 'public.embeddings'"`` or
            ``"JSON→JSONB upgrade on 'public.embeddings.langchain_metadata'"``.

    Raises:
        psycopg.errors.ProgramLimitExceeded: If all escalation steps are
            exhausted.

    """
    t0 = time.perf_counter()
    try:
        cur.execute(sql)
    except (psycopg.errors.DuplicateTable, psycopg.errors.UniqueViolation):
        logger.info("%s already exists — skipping.", label)
        return
    except psycopg.errors.ProgramLimitExceeded:
        logger.warning("%s hit memory limit — escalating maintenance_work_mem", label)
        for step in _MEM_ESCALATION_STEPS:
            logger.info("Retrying %s with maintenance_work_mem = %s", label, step)
            _set_maintenance_work_mem(cur, step)
            t0 = time.perf_counter()
            try:
                cur.execute(sql)
            except (psycopg.errors.DuplicateTable, psycopg.errors.UniqueViolation):
                logger.info("%s already exists — skipping.", label)
                return
            except psycopg.errors.ProgramLimitExceeded:
                logger.warning("Still insufficient at %s", step)
                continue
            elapsed = time.perf_counter() - t0
            logger.info("%s completed in %.1fs (after escalation to %s)", label, elapsed, step)
            return
        raise  # all escalation steps exhausted
    elapsed = time.perf_counter() - t0
    logger.info("%s completed in %.1fs", label, elapsed)


def _classify_index(ddl: str) -> str:
    """Return a human-friendly index type label for a DDL statement."""
    lower = ddl.lower()
    if HNSW_INDEX_TYPE in lower:
        return "HNSW"
    if IVFFLAT_INDEX_TYPE in lower:
        return "IVFFlat"
    if "gin" in lower:
        return "GIN"
    return "BTREE"


def _execute_index_ddl_with_escalation(
    cur: psycopg.Cursor,
    ddl: str,
    idx_type: str,
    schema: str,
) -> None:
    """Execute a single index DDL, escalating ``maintenance_work_mem`` on OOM.

    If the initial attempt raises ``ProgramLimitExceeded`` (memory required
    exceeds current ``maintenance_work_mem``), the function retries with
    progressively larger values.
    """
    execute_with_mem_escalation(cur, ddl, f"{idx_type} index on '{schema}'")


def create_indexes(
    dsn: str,
    schema: str,
    table: str,
    metadata_columns: list[str] | None = None,
    metadata_json_column: str | None = None,
    index_type: str | None = None,
    hnsw_m: int | None = None,
    hnsw_ef_construction: int | None = None,
    ivfflat_lists: int | None = None,
    maintenance_work_mem: str | None = None,
    vector_storage_type: str | None = None,
) -> None:
    """Create vector store indexes using a direct psycopg connection.

    Sets ``maintenance_work_mem`` before creating indexes and automatically
    escalates it if a ``ProgramLimitExceeded`` error is raised.

    Args:
        dsn: PostgreSQL connection string (plain ``postgresql://`` format).
        schema: Target schema name.
        table: Target table name.
        metadata_columns: Additional metadata columns to index.
        metadata_json_column: Name of the JSONB metadata column.
        index_type: Type of vector index ('hnsw' or 'ivfflat').
        hnsw_m: HNSW parameter m.
        hnsw_ef_construction: HNSW parameter ef_construction.
        ivfflat_lists: IVFFlat parameter lists.
        maintenance_work_mem: Initial ``maintenance_work_mem`` value.
            Defaults to :attr:`settings.maintenance_work_mem`.
        vector_storage_type: Vector column type ('vector' or 'halfvec').

    """
    if maintenance_work_mem is None:
        maintenance_work_mem = settings.maintenance_work_mem

    ddl_statements = generate_index_ddl(
        schema,
        table,
        metadata_columns,
        metadata_json_column,
        index_type,
        hnsw_m,
        hnsw_ef_construction,
        ivfflat_lists,
        vector_storage_type=vector_storage_type,
    )

    with psycopg.connect(dsn) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            _set_maintenance_work_mem(cur, maintenance_work_mem)
            for ddl in ddl_statements:
                idx_type = _classify_index(ddl)
                logger.info("Creating %s index on '%s.%s'…", idx_type, schema, table)
                _execute_index_ddl_with_escalation(cur, ddl, idx_type, schema)


# ── Table initialisation (langchain PGEngine) ────────────────────────


def ensure_metadata_column_is_jsonb(
    dsn: str,
    schema: str,
    table: str,
    metadata_json_column: str = "langchain_metadata",
) -> None:
    """Upgrade the metadata column from ``JSON`` to ``JSONB`` when needed.

    ``langchain_postgres`` creates the metadata column as plain ``JSON``.
    This function issues ``ALTER TABLE … ALTER COLUMN … TYPE JSONB`` so that
    GIN indexes, containment operators (``@>``) and key-existence (``?``)
    work correctly.  The statement is a no-op when the column is already
    stored as ``jsonb``.

    Args:
        dsn: PostgreSQL connection string (plain ``postgresql://`` format).
        schema: Schema name.
        table: Table name.
        metadata_json_column: Name of the metadata column to upgrade.

    """
    with psycopg.connect(dsn) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = %s
                  AND table_name = %s
                  AND column_name = %s
                  AND data_type = 'json'
                """,
                (schema, table, metadata_json_column),
            )
            if cur.fetchone():
                alter_sql = psycopg.sql.SQL(
                    "ALTER TABLE {schema}.{table} ALTER COLUMN {col} TYPE JSONB USING {col}::JSONB"
                ).format(
                    schema=psycopg.sql.Identifier(schema),
                    table=psycopg.sql.Identifier(table),
                    col=psycopg.sql.Identifier(metadata_json_column),
                )
                execute_with_mem_escalation(
                    cur, alter_sql, f"JSON->JSONB upgrade on '{schema}.{table}.{metadata_json_column}'"
                )
                logger.info(
                    "Upgraded '%s.%s.%s' column type from JSON to JSONB.",
                    schema,
                    table,
                    metadata_json_column,
                )


def init_vector_table(
    dsn: str,
    schema: str,
    table: str,
    vector_size: int,
    metadata_columns: list[str] | None = None,
    metadata_json_column: str = "langchain_metadata",
) -> None:
    """Initialise the vector store table using langchain's ``PGEngine``.

    This is the **single source of truth** for table creation.  The same
    ``PGEngine.init_vectorstore_table`` call is used by the main app
    (``PGVectorStoreAdapter``, ``TenantService``) and the migration
    service.

    Args:
        dsn: PostgreSQL connection string (plain ``postgresql://`` format).
        schema: Target schema name.
        table: Table name.
        vector_size: Dimension of the embedding vectors.
        metadata_columns: Additional metadata column names.
            Defaults to :data:`METADATA_COLUMNS`.
        metadata_json_column: Name of the JSONB metadata column.

    """
    from langchain_postgres import Column, PGEngine
    from sqlalchemy import AsyncAdaptedQueuePool

    if metadata_columns is None:
        metadata_columns = METADATA_COLUMNS

    # PGEngine requires the ``postgresql+psycopg://`` URL scheme.
    psycopg_url = dsn.replace("postgresql://", "postgresql+psycopg://")

    engine = PGEngine.from_connection_string(
        url=psycopg_url,
        poolclass=AsyncAdaptedQueuePool,
    )

    cols = [Column(name=c, data_type="text") for c in metadata_columns]

    engine.init_vectorstore_table(
        table_name=table,
        vector_size=vector_size,
        store_metadata=True,
        metadata_columns=cols,
        metadata_json_column=metadata_json_column,
        schema_name=schema,
    )
    ensure_metadata_column_is_jsonb(dsn=dsn, schema=schema, table=table, metadata_json_column=metadata_json_column)
    logger.info(
        "Vector store table '%s.%s' initialised (vector_size=%d).",
        schema,
        table,
        vector_size,
    )
