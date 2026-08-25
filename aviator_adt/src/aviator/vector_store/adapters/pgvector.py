"""PostgreSQL vector store adapter."""

import ast
import asyncio
import copy
import hashlib
import json
import logging
import math
import re
import sys
import time
import uuid

import psycopg
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import VectorStore
from langchain_postgres import Column, PGEngine, PGVectorStore
from psycopg import sql
from psycopg.errors import DuplicateTable
from sqlalchemy import AsyncAdaptedQueuePool, create_engine
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.schema import CreateSchema

from aviator.exceptions import EmbeddingDimensionMismatchError
from aviator.settings import settings
from aviator.vector_store.adapters.base import VectorStoreAdapter
from aviator.vector_store.adapters.query_adapter import MongoStyleQueryAdapter
from aviator.vector_store.schema import (
    HALFVEC_STORAGE_TYPE,
    HNSW_INDEX_TYPE,
    IVFFLAT_INDEX_TYPE,
    METADATA_COLUMNS,
    ensure_metadata_column_is_jsonb,
    get_embedding_ann_index_name,
    get_embedding_opclass,
    get_embedding_storage_type,
)

logger = logging.getLogger(__name__)


def ensure_windows_selector_event_loop_policy() -> None:
    """Use the selector event loop policy on Windows when available."""

    if sys.platform != "win32":
        return
    policy_cls = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if policy_cls is None:
        return

    current_policy = asyncio.get_event_loop_policy()
    if not isinstance(current_policy, policy_cls):
        asyncio.set_event_loop_policy(policy_cls())


# Database connection string prefixes
_POSTGRES_PREFIX = "postgresql://"
_PSYCOPG_PREFIX = "postgresql+psycopg://"


def _embedding_to_list(embedding: object) -> list[float]:
    """Convert embedding from any format to list[float].

    Handles PostgreSQL vector, numpy arrays, lists, and tuples.
    """
    if isinstance(embedding, list):
        return [float(e) for e in embedding]
    if isinstance(embedding, tuple):
        return [float(e) for e in embedding]
    if hasattr(embedding, "tolist"):  # numpy array
        return [float(e) for e in embedding.tolist()]
    if isinstance(embedding, str):
        # Parse string format like "[0.1, 0.2, ...]"
        return [float(e) for e in ast.literal_eval(embedding)]
    # Assume iterable
    return [float(e) for e in embedding]


class PGVectorStoreAdapter(VectorStoreAdapter):
    """Adapter for PostgreSQL vector store."""

    _id_column = "langchain_id"

    def __init__(
        self,
        embeddings: Embeddings,
        schema_name: str | None = None,
    ) -> None:
        """Initialize the PGVector adapter with configuration.

        Args:
            embeddings: Embeddings service to use.
            schema_name: PostgreSQL schema to scope the vector store to.
                         Defaults to the configured default schema.

        """
        super().__init__(embeddings)
        self.connection_string = self._get_connection_string()
        self.table_name = settings.vector_store_table_name
        self.vector_size = settings.vector_size
        self.metadata_columns = list(METADATA_COLUMNS)
        self.schema_name = schema_name or settings.default_schema
        self.vector_storage_type = settings.vector_storage_type
        self.vector_index_type = settings.vector_index_type
        self.hnsw_m = settings.hnsw_m
        self.hnsw_ef_construction = settings.hnsw_ef_construction
        self.ivfflat_lists = settings.ivfflat_lists
        self.ivfflat_probes = settings.ivfflat_probes
        self._preserved_hnsw_m: int | None = None
        self._preserved_hnsw_ef_construction: int | None = None
        self._preserved_ivfflat_lists: int | None = None
        self.query_adapter = MongoStyleQueryAdapter(
            schema_name=self.schema_name,
            table_name=self.table_name,
            vector_cast_type=get_embedding_storage_type(self.vector_storage_type),
        )

    async def asimilarity_search_with_relevance_scores(
        self,
        query: str,
        k: int = 10,
        metadata_filter: dict | None = None,
        score_threshold: float | None = None,
    ) -> list[tuple[object, float]]:
        """Hybrid SQL metadata + vector similarity search (async).

        Args:
            query: Query string to embed and search.
            k: Top-k results to return.
            metadata_filter: Metadata filter dict (flat keys, e.g. {"document_id": "foo"}).
            score_threshold: Optional minimum similarity score (cosine distance).

        Returns:
            List of (Document, score) tuples.

        """

        # Get embedding for the query
        query_embedding = await self.embeddings.aembed_query(query)
        sql_query, params = self.query_adapter.build_query(
            filter_dict=metadata_filter, query_embedding=query_embedding, limit=k
        )

        if settings.dev_tools:
            # this logging is only for development purpose. it exposes customer data (metadata names/info) in logs. #
            logger.info(
                "Executing SQL query: %s\n with threshold: %s\nquery string: %s", sql_query, score_threshold, query
            )

        results = []
        psycopg_conn_str = self.connection_string.replace(_PSYCOPG_PREFIX, _POSTGRES_PREFIX)
        # Reuse the same connect_args (including ANN session options like ivfflat.probes /
        # hnsw.ef_search) that the pooled PGEngine uses, so we don't need a separate SET.
        conn_options = self._get_connection_settings().get("connect_args", {}).get("options")
        conn = await psycopg.AsyncConnection.connect(
            psycopg_conn_str,
            **({"options": conn_options} if conn_options else {}),
        )
        try:
            async with conn.cursor() as cur:
                await cur.execute(sql_query, params)
                rows = await cur.fetchall()
                for row in rows:
                    langchain_id, metadata_json, content, distance = row
                    cosine_similarity = 1.0 - distance

                    # Optionally filter by score_threshold (lower distance = more similar)
                    if score_threshold is not None and cosine_similarity < score_threshold:
                        if settings.dev_tools:
                            # this logging is only for development purpose. it exposes customer data (metadata names/info) in logs. #
                            logger.info(
                                "Result with cosine similarity %.4f below threshold %.4f",
                                cosine_similarity,
                                score_threshold,
                            )
                        continue

                    # Build Document object (or dict)
                    doc = Document(
                        id=str(langchain_id),
                        metadata=metadata_json or {},
                        page_content=content,
                        score=cosine_similarity,
                    )

                    results.append((doc, cosine_similarity))
        except Exception as e:
            logger.error("Error executing similarity search: %s", e)
            raise

        finally:
            await conn.close()

        return results

    async def aget_distinct_document_ids(self, metadata_filter: dict | None = None) -> list[str]:
        """Return distinct document IDs matching the given filter.

        Args:
            metadata_filter: MongoDB-style metadata filter dict, or ``None`` for all documents.

        Returns:
            List of unique document ID strings.

        """
        sql_query, params = self.query_adapter.build_distinct_document_ids_query(filter_dict=metadata_filter)

        psycopg_conn_str = self.connection_string.replace(_PSYCOPG_PREFIX, _POSTGRES_PREFIX)
        conn = await psycopg.AsyncConnection.connect(psycopg_conn_str)
        try:
            async with conn.cursor() as cur:
                await cur.execute(sql_query, params)
                rows = await cur.fetchall()
                return [str(row[0]) for row in rows if row[0] is not None]
        except Exception as e:
            logger.error("Error fetching distinct document IDs: %s", e)
            raise
        finally:
            await conn.close()

    @staticmethod
    def _upsert_postgres_option(options: str, key: str, value: int) -> str:
        """Insert or replace a PostgreSQL `-c key=value` option in an options string."""
        normalized = options.strip()
        pattern = rf"(?:^|\s)-c\s+{re.escape(key)}=\S+"
        replacement = f"-c {key}={value}"

        if re.search(pattern, normalized):
            updated = re.sub(
                pattern, lambda match: f" {replacement}" if match.group(0).startswith(" ") else replacement, normalized
            )
            return re.sub(r"\s+", " ", updated).strip()

        if not normalized:
            return replacement

        return f"{normalized} {replacement}"

    def _get_connection_settings(self) -> dict:
        """Return connection settings with ANN session parameters applied."""
        connection_settings = copy.deepcopy(settings.postgres_connection_settings)
        connect_args = connection_settings.setdefault("connect_args", {})
        options = connect_args.get("options", "")

        if self.vector_index_type == IVFFLAT_INDEX_TYPE:
            options = self._upsert_postgres_option(
                options,
                f"{IVFFLAT_INDEX_TYPE}.probes",
                self._get_effective_ivfflat_probes(),
            )

        if options:
            connect_args["options"] = options

        return connection_settings

    @staticmethod
    def _get_connection_string() -> str:
        """Get the PostgreSQL connection string."""
        conn_str = settings.postgres_connection.encoded_string()
        return conn_str.replace(_POSTGRES_PREFIX, _PSYCOPG_PREFIX)

    def _create_engine(self) -> PGEngine:
        """Create a PGEngine instance."""
        ensure_windows_selector_event_loop_policy()
        logger.info("Connecting to PostgreSQL...")
        logger.debug("Creating PGEngine with connection string: %s", self.connection_string)
        connection_settings = self._get_connection_settings()
        engine = PGEngine.from_connection_string(
            url=self.connection_string, poolclass=AsyncAdaptedQueuePool, **connection_settings
        )

        logger.info("Connected to PostgreSQL.")
        return engine

    def _get_total_chunks(self) -> int:
        """Return the current number of embedding rows in the managed table."""
        try:
            psycopg_conn_str = self.connection_string.replace(_PSYCOPG_PREFIX, _POSTGRES_PREFIX)
            with psycopg.connect(psycopg_conn_str) as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT EXISTS("
                    "  SELECT 1 FROM information_schema.tables "
                    "  WHERE table_schema = %s "
                    "  AND table_name = %s"
                    ")",
                    (self.schema_name, self.table_name),
                )
                if not cur.fetchone()[0]:
                    return 0

                cur.execute(
                    sql.SQL("SELECT COUNT(*) FROM {}.{}").format(
                        sql.Identifier(self.schema_name),
                        sql.Identifier(self.table_name),
                    )
                )
                row = cur.fetchone()
                return int(row[0]) if row else 0
        except Exception as e:
            logger.error("Error counting embedding rows for table '%s': %s", self.table_name, e)
            return 0

    def _get_existing_vector_dimension(self) -> int | None:
        """Check if table exists and return its vector dimension.

        Returns:
            None if table doesn't exist or uses flexible dimensions,
            int if table uses fixed dimensions.

        """
        try:
            psycopg_conn_str = self.connection_string.replace(_PSYCOPG_PREFIX, _POSTGRES_PREFIX)
            with psycopg.connect(psycopg_conn_str) as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT EXISTS("
                    "  SELECT 1 FROM information_schema.tables "
                    "  WHERE table_schema = %s "
                    "  AND table_name = %s"
                    ")",
                    (self.schema_name, self.table_name),
                )
                if not cur.fetchone()[0]:
                    return None

                cur.execute(
                    """
                    SELECT a.atttypmod
                    FROM pg_attribute a
                    JOIN pg_class c ON a.attrelid = c.oid
                    JOIN pg_namespace n ON c.relnamespace = n.oid
                    WHERE n.nspname = %s
                    AND c.relname = %s
                    AND a.attname = 'embedding'
                    """,
                    (self.schema_name, self.table_name),
                )
                row = cur.fetchone()
                if row is None:
                    return None

                typmod = row[0]
                if typmod is None or typmod == -1:
                    return None
                return typmod

        except Exception as e:
            logger.error("Error checking vector dimension for table '%s': %s", self.table_name, e)
            return None

    def _get_existing_embedding_type(self) -> str | None:
        """Check if table exists and return the embedding column type name.

        Returns:
            None if table or column doesn't exist,
            str type name (e.g. 'vector' or 'halfvec') otherwise.

        """
        try:
            psycopg_conn_str = self.connection_string.replace(_PSYCOPG_PREFIX, _POSTGRES_PREFIX)
            with psycopg.connect(psycopg_conn_str) as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT t.typname
                    FROM pg_attribute a
                    JOIN pg_class c ON a.attrelid = c.oid
                    JOIN pg_namespace n ON c.relnamespace = n.oid
                    JOIN pg_type t ON a.atttypid = t.oid
                    WHERE n.nspname = %s
                    AND c.relname = %s
                    AND a.attname = 'embedding'
                    """,
                    (self.schema_name, self.table_name),
                )
                row = cur.fetchone()
                return row[0] if row else None
        except Exception as e:
            logger.error("Error checking embedding type for table '%s': %s", self.table_name, e)
            return None

    def _get_expected_embedding_type(self) -> str:
        """Return the configured embedding column type name."""
        return get_embedding_storage_type(self.vector_storage_type)

    def _get_embedding_type_sql(self) -> str:
        """Return the configured embedding column type with dimensions."""
        return f"{self._get_expected_embedding_type()}({self.vector_size})"

    def _validate_embedding_dimension(self) -> None:
        """Validate that the existing table dimensions match configuration."""
        existing_dimension = self._get_existing_vector_dimension()
        if existing_dimension is None or self.vector_size == existing_dimension:
            return

        error_msg = (
            f"FATAL: Vector dimension mismatch for table '{self.table_name}'.\n"
            f"  Existing table: {existing_dimension} dimensions\n"
            f"  Configuration:  {self.vector_size} dimensions\n\n"
            f"To resolve this issue:\n"
            f"  1. Drop the table: DROP TABLE {self.table_name} CASCADE;\n"
            f"  2. OR update VECTOR_SIZE={existing_dimension} in your configuration\n\n"
            f"WARNING: Dropping the table will permanently delete all indexed documents."
        )
        logger.error(error_msg)
        raise EmbeddingDimensionMismatchError(error_msg)

    def _get_embedding_index_names(self) -> list[str]:
        """Return indexes that reference the embedding column."""
        return [index["name"] for index in self._get_embedding_indexes()]

    def _get_embedding_indexes(self) -> list[dict[str, str | list[str] | None]]:
        """Return metadata for indexes that reference the embedding column."""
        try:
            psycopg_conn_str = self.connection_string.replace(_PSYCOPG_PREFIX, _POSTGRES_PREFIX)
            with psycopg.connect(psycopg_conn_str) as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT idx.relname, am.amname, idx.reloptions, pg_get_indexdef(idx.oid)
                    FROM pg_class tbl
                    JOIN pg_namespace ns ON tbl.relnamespace = ns.oid
                    JOIN pg_index i ON tbl.oid = i.indrelid
                    JOIN pg_class idx ON idx.oid = i.indexrelid
                    JOIN pg_am am ON idx.relam = am.oid
                    JOIN pg_attribute a ON a.attrelid = tbl.oid
                    WHERE ns.nspname = %s
                    AND tbl.relname = %s
                    AND a.attname = 'embedding'
                    AND a.attnum = ANY(i.indkey)
                    """,
                    (self.schema_name, self.table_name),
                )
                return [
                    {
                        "name": row[0],
                        "method": row[1],
                        "reloptions": row[2],
                        "definition": row[3],
                    }
                    for row in cur.fetchall()
                ]
        except Exception as e:
            logger.error("Error checking embedding indexes for table '%s': %s", self.table_name, e)
            return []

    @staticmethod
    def _parse_index_reloptions(reloptions: list[str] | None) -> dict[str, int]:
        """Parse PostgreSQL reloptions into integer key-value pairs."""
        params: dict[str, int] = {}
        for option in reloptions or []:
            key, _, value = option.partition("=")
            if value.isdigit():
                params[key] = int(value)
        return params

    def _get_existing_hnsw_index_params(self) -> tuple[int | None, int | None]:
        """Return HNSW index parameters from the existing embedding index, if any."""
        try:
            for index in self._get_embedding_indexes():
                if index["method"] != HNSW_INDEX_TYPE:
                    continue

                params = self._parse_index_reloptions(index["reloptions"])
                return params.get("m"), params.get("ef_construction")

        except Exception as e:
            logger.error("Error checking HNSW index parameters for table '%s': %s", self.table_name, e)
        else:
            return None, None

        return None, None

    def _get_existing_ivfflat_index_lists(self) -> int | None:
        """Return IVFFlat list count from the existing embedding index, if any."""
        try:
            for index in self._get_embedding_indexes():
                if index["method"] != IVFFLAT_INDEX_TYPE:
                    continue

                return self._parse_index_reloptions(index["reloptions"]).get("lists")

        except Exception as e:
            logger.error("Error checking IVFFlat index parameters for table '%s': %s", self.table_name, e)
        else:
            return None

        return None

    def _get_effective_hnsw_params(self) -> tuple[int, int]:
        """Return the HNSW parameters to use for index creation."""
        return (
            self._preserved_hnsw_m if self._preserved_hnsw_m is not None else self.hnsw_m,
            self._preserved_hnsw_ef_construction
            if self._preserved_hnsw_ef_construction is not None
            else self.hnsw_ef_construction,
        )

    def _get_configured_ivfflat_lists(self) -> int:
        """Return the configured or derived IVFFlat list count."""
        if self.ivfflat_lists is not None:
            return self.ivfflat_lists

        return max(1, math.isqrt(max(1, self._get_total_chunks())))

    def _get_effective_ivfflat_lists(self) -> int:
        """Return the IVFFlat list count to use for index creation."""
        return (
            self._preserved_ivfflat_lists
            if self._preserved_ivfflat_lists is not None
            else self._get_configured_ivfflat_lists()
        )

    def _get_effective_ivfflat_probes(self) -> int:
        """Return the IVFFlat probes setting to use for similarity-search sessions."""
        if self.ivfflat_probes is not None:
            return self.ivfflat_probes

        return max(1, math.isqrt(self._get_effective_ivfflat_lists()))

    def _get_embedding_index_name(self) -> str:
        """Return the managed name for the embedding ANN index."""
        return get_embedding_ann_index_name(self.table_name, self.vector_index_type)

    def _get_embedding_index_definition(self) -> str:
        """Return the index expression including the correct operator class."""
        opclass = get_embedding_opclass(self.vector_storage_type)
        return f"embedding {opclass}"

    def _get_configured_index_params(self) -> dict[str, int]:
        """Return configured ANN index parameters without migration preservation."""
        if self.vector_index_type is None:
            return {}
        if self.vector_index_type == IVFFLAT_INDEX_TYPE:
            return {"lists": self._get_configured_ivfflat_lists()}

        return {"m": self.hnsw_m, "ef_construction": self.hnsw_ef_construction}

    def _preserve_existing_index_params_for_configured_type(self) -> None:
        """Preserve the current ANN index parameters for same-type recreation during type migration."""
        self._preserved_hnsw_m = None
        self._preserved_hnsw_ef_construction = None
        self._preserved_ivfflat_lists = None

        if self.vector_index_type is None:
            return

        if self.vector_index_type == IVFFLAT_INDEX_TYPE:
            self._preserved_ivfflat_lists = self._get_existing_ivfflat_index_lists()
            return

        self._preserved_hnsw_m, self._preserved_hnsw_ef_construction = self._get_existing_hnsw_index_params()

    def _drop_embedding_indexes(self, index_names: list[str], reason: str) -> None:
        """Drop all indexes that reference the embedding column."""
        if not index_names:
            return

        psycopg_conn_str = self.connection_string.replace(_PSYCOPG_PREFIX, _POSTGRES_PREFIX)
        with psycopg.connect(psycopg_conn_str) as conn:
            conn.autocommit = False
            with conn.cursor() as cur:
                for index_name in index_names:
                    cur.execute(
                        sql.SQL("DROP INDEX IF EXISTS {}.{}").format(
                            sql.Identifier(self.schema_name),
                            sql.Identifier(index_name),
                        )
                    )
                    logger.info("Dropped embedding index '%s.%s' (%s).", self.schema_name, index_name, reason)
                conn.commit()

    def _embedding_index_matches_configuration(self, indexes: list[dict[str, str | list[str] | None]]) -> bool:
        """Return whether existing embedding indexes already match the managed configuration."""
        if self.vector_index_type is None:
            return len(indexes) == 0

        if len(indexes) != 1:
            return False

        index = indexes[0]
        if index["name"] != self._get_embedding_index_name() or index["method"] != self.vector_index_type:
            return False

        if self._get_embedding_index_definition() not in (index["definition"] or ""):
            return False

        actual_params = self._parse_index_reloptions(index["reloptions"])
        return all(actual_params.get(key) == value for key, value in self._get_configured_index_params().items())

    def _reconcile_embedding_index_if_needed(self) -> None:
        """Rebuild embedding indexes when they drift from the managed configuration."""
        if self.vector_index_type is None:
            return

        indexes = self._get_embedding_indexes()
        if not indexes or self._embedding_index_matches_configuration(indexes):
            return

        self._drop_embedding_indexes(
            [str(index["name"]) for index in indexes],
            reason="index configuration drift",
        )

    def _migrate_embedding_type_if_needed(self) -> None:
        """Migrate the embedding column type to match the configured storage mode."""
        existing_type = self._get_existing_embedding_type()
        expected_type = self._get_expected_embedding_type()

        if existing_type is None or existing_type == expected_type:
            return

        index_names = self._get_embedding_index_names()
        self._preserve_existing_index_params_for_configured_type()
        target_type_sql = self._get_embedding_type_sql()
        psycopg_conn_str = self.connection_string.replace(_PSYCOPG_PREFIX, _POSTGRES_PREFIX)

        with psycopg.connect(psycopg_conn_str) as conn:
            conn.autocommit = False
            with conn.cursor() as cur:
                for index_name in index_names:
                    cur.execute(
                        sql.SQL("DROP INDEX IF EXISTS {}.{}").format(
                            sql.Identifier(self.schema_name),
                            sql.Identifier(index_name),
                        )
                    )
                    logger.info("Dropped embedding index '%s.%s' before type migration.", self.schema_name, index_name)

                cur.execute(
                    sql.SQL("ALTER TABLE {}.{} ALTER COLUMN embedding TYPE {} USING embedding::{}").format(
                        sql.Identifier(self.schema_name),
                        sql.Identifier(self.table_name),
                        sql.SQL(target_type_sql),
                        sql.SQL(target_type_sql),
                    )
                )
                conn.commit()

        logger.info(
            "Migrated '%s.%s.embedding' from %s to %s.",
            self.schema_name,
            self.table_name,
            existing_type,
            expected_type,
        )

    def _create_indexes(self) -> None:
        """Create indexes on the vector store table.

        Delegates to :func:`aviator.vector_store.schema.create_indexes`
        which is the single source of truth for index DDL.
        """
        from aviator.vector_store.schema import create_indexes

        hnsw_m, hnsw_ef_construction = self._get_effective_hnsw_params()
        psycopg_conn_str = self.connection_string.replace(_PSYCOPG_PREFIX, _POSTGRES_PREFIX)
        create_indexes(
            dsn=psycopg_conn_str,
            schema=self.schema_name,
            table=self.table_name,
            metadata_columns=self.metadata_columns,
            metadata_json_column=settings.vector_store_metadata_column,
            index_type=self.vector_index_type,
            hnsw_m=hnsw_m,
            hnsw_ef_construction=hnsw_ef_construction,
            ivfflat_lists=self._get_effective_ivfflat_lists(),
            vector_storage_type=self.vector_storage_type,
        )

    def _ensure_schema_exists(self) -> None:
        """Create the target schema when it does not exist."""
        engine = create_engine(self.connection_string, **settings.postgres_connection_settings)
        try:
            with engine.begin() as conn:
                conn.execute(CreateSchema(self.schema_name, if_not_exists=True))
        finally:
            engine.dispose()

        logger.info("Schema ensured for PGVector store: '%s'.", self.schema_name)

    def _ensure_metadata_columns_exist(self) -> None:
        """Ensure all metadata columns exist in the table.

        This handles migrations when new metadata columns are added after the table was created.
        """
        psycopg_conn_str = self.connection_string.replace(_PSYCOPG_PREFIX, _POSTGRES_PREFIX)

        with psycopg.connect(psycopg_conn_str) as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                # Get existing columns
                cur.execute(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema = %s AND table_name = %s
                    """,
                    (self.schema_name, self.table_name),
                )
                existing_columns = {row[0] for row in cur.fetchall()}

                # Add missing metadata columns
                for col in self.metadata_columns:
                    if col not in existing_columns:
                        logger.info(
                            "Adding missing metadata column '%s.%s.%s'",
                            self.schema_name,
                            self.table_name,
                            col,
                        )
                        cur.execute(
                            sql.SQL("ALTER TABLE {}.{} ADD COLUMN {} TEXT DEFAULT NULL").format(
                                sql.Identifier(self.schema_name),
                                sql.Identifier(self.table_name),
                                sql.Identifier(col),
                            )
                        )
                        logger.info("Column '%s' added successfully.", col)

    def _create_halfvec_table(self) -> None:
        """Create halfvec table manually (since langchain-postgres doesn't natively support it).

        Creates a table with halfvec(vector_size) column instead of vector(vector_size).
        """
        metadata_col = settings.vector_store_metadata_column

        psycopg_conn_str = self.connection_string.replace(_PSYCOPG_PREFIX, _POSTGRES_PREFIX)
        with psycopg.connect(psycopg_conn_str) as conn:
            conn.autocommit = False
            with conn.cursor() as cur:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")

                cur.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(self.schema_name)))

                cur.execute(
                    sql.SQL(
                        "CREATE TABLE IF NOT EXISTS {schema}.{table} ("
                        "  {id_col} UUID PRIMARY KEY,"
                        "  embedding halfvec({dim}) NOT NULL,"
                        "  content TEXT NOT NULL,"
                        "  {meta_col} JSONB,"
                        "  workspace_id TEXT,"
                        "  document_id TEXT,"
                        "  text_hash TEXT DEFAULT NULL,"
                        "  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
                        ")"
                    ).format(
                        schema=sql.Identifier(self.schema_name),
                        table=sql.Identifier(self.table_name),
                        id_col=sql.Identifier(self._id_column),
                        dim=sql.Literal(self.vector_size),
                        meta_col=sql.Identifier(metadata_col),
                    )
                )
                conn.commit()
                logger.info("Halfvec table '%s' created in schema '%s'.", self.table_name, self.schema_name)

    def _init_table(self, engine: PGEngine) -> None:
        """Initialize the vector store table.

        Supports two modes:
        - 'vector': Standard float32 column and index (default, full precision)
        - 'halfvec': Native float16 column and index (50% smaller, slight recall loss)

        Detects dimension mismatches and fails startup if detected.
        If the existing embedding column type doesn't match configuration,
        the adapter migrates it in place and recreates the embedding index.

        Raises:
            EmbeddingDimensionMismatchError: If existing table dimensions don't match configuration.

        """
        self._ensure_schema_exists()

        self._validate_embedding_dimension()
        self._migrate_embedding_type_if_needed()

        if self.vector_storage_type == HALFVEC_STORAGE_TYPE:
            logger.info("Initializing halfvec table '%s' in schema '%s'...", self.table_name, self.schema_name)
            try:
                self._create_halfvec_table()
                self._ensure_metadata_columns_exist()
                self._reconcile_embedding_index_if_needed()
                self._create_indexes()
                logger.info("Halfvec table '%s' initialized.", self.table_name)
            except EmbeddingDimensionMismatchError:
                raise
            except Exception as e:
                logger.error("Error setting up halfvec table: %s", e)
                raise
        else:
            try:
                metadata_cols = [Column(name=col, data_type="text") for col in self.metadata_columns]

                engine.init_vectorstore_table(
                    table_name=self.table_name,
                    vector_size=self.vector_size,
                    store_metadata=True,
                    metadata_columns=metadata_cols,
                    metadata_json_column=settings.vector_store_metadata_column,
                    schema_name=self.schema_name,
                )
                logger.info("PGVector table '%s' created in schema '%s'.", self.table_name, self.schema_name)

            except ProgrammingError as e:
                if isinstance(e.orig, DuplicateTable):
                    logger.info(
                        "PGVector table '%s' already exists",
                        self.table_name,
                    )

                    # Ensure all metadata columns exist (for schema migrations)
                    self._ensure_metadata_columns_exist()

                else:
                    raise
            else:
                logger.info("PGVector table '%s' initialized.", self.table_name)
            psycopg_conn_str = self.connection_string.replace(_PSYCOPG_PREFIX, _POSTGRES_PREFIX)
            ensure_metadata_column_is_jsonb(
                dsn=psycopg_conn_str,
                schema=self.schema_name,
                table=self.table_name,
                metadata_json_column=settings.vector_store_metadata_column,
            )
            self._reconcile_embedding_index_if_needed()
            self._create_indexes()

    async def asetup(self) -> None:
        """Set up the PGVector store asynchronously."""
        logger.info("Setting up PGVector store...")
        engine = self._create_engine()
        self._init_table(engine)
        engine = self._create_engine()

        logger.info("Initializing PGVectorStore (schema=%s)...", self.schema_name)
        self._store = await PGVectorStore.create(
            engine=engine,
            table_name=self.table_name,
            embedding_service=self.embeddings,
            metadata_columns=self.metadata_columns,
            id_column=self._id_column,
            metadata_json_column=settings.vector_store_metadata_column,
            schema_name=self.schema_name,
        )
        logger.info("PGVector store initialized (schema=%s).", self.schema_name)

    def populate_text_hash_from_content(self) -> None:
        """Populate text_hash column by computing SHA256 hash of content.

        This is called after add_documents() to ensure text_hash is populated in the column
        for efficient deduplication lookups. Uses Python-based hashing (no external dependencies).

        Uses FOR UPDATE SKIP LOCKED to prevent concurrent workers from processing the same rows.

        """
        try:
            psycopg_conn_str = self.connection_string.replace(_PSYCOPG_PREFIX, _POSTGRES_PREFIX)
            with psycopg.connect(psycopg_conn_str) as conn:
                conn.autocommit = True
                with conn.cursor() as cur:
                    # Fetch all documents with NULL text_hash, locking rows to prevent concurrent processing
                    fetch_query = sql.SQL(
                        "SELECT langchain_id, content FROM {}.{} WHERE text_hash IS NULL LIMIT 1000 FOR UPDATE SKIP LOCKED"
                    ).format(
                        sql.Identifier(self.schema_name),
                        sql.Identifier(self.table_name),
                    )
                    cur.execute(fetch_query)
                    rows = cur.fetchall()

                    if not rows:
                        return

                    # Compute hashes in Python and update database
                    for langchain_id, content in rows:
                        if content is None:
                            continue

                        # Normalize whitespace to match generate_chunk_hash() in celery.py
                        normalized_content = " ".join(content.split())
                        text_hash = hashlib.sha256(normalized_content.encode()).hexdigest()
                        update_query = sql.SQL("UPDATE {}.{} SET text_hash = %s WHERE langchain_id = %s").format(
                            sql.Identifier(self.schema_name),
                            sql.Identifier(self.table_name),
                        )
                        cur.execute(update_query, (text_hash, langchain_id))

                    logger.info("Populated text_hash for %d documents from content", len(rows))

        except Exception as _:
            logger.exception("Error populating text_hash from content")
            # Don't raise - this is a best-effort operation

    def set_text_hash_for_documents(self, documents: list, text_hashes: list[str]) -> None:
        """Update text_hash column for specific documents with pre-computed values.

        This is more efficient than populate_text_hash_from_content() when hashes are already known.
        Uses a single batch UPDATE with CASE/WHEN to avoid N separate queries.

        Args:
            documents: List of Document objects that were just inserted
            text_hashes: List of pre-computed text hash values corresponding to documents

        """
        if not documents or not text_hashes or len(documents) != len(text_hashes):
            logger.warning(
                "Invalid arguments to set_text_hash_for_documents: %d docs, %d hashes",
                len(documents) if documents else 0,
                len(text_hashes) if text_hashes else 0,
            )
            return

        try:
            psycopg_conn_str = self.connection_string.replace(_PSYCOPG_PREFIX, _POSTGRES_PREFIX)
            with psycopg.connect(psycopg_conn_str) as conn:
                conn.autocommit = True
                with conn.cursor() as cur:
                    # Build batch UPDATE using CASE/WHEN to avoid N separate queries
                    valid_pairs = [
                        (doc.page_content, text_hash)
                        for doc, text_hash in zip(documents, text_hashes, strict=True)
                        if text_hash and doc.page_content
                    ]

                    if not valid_pairs:
                        logger.debug("No valid document/hash pairs to update")
                        return

                    # Build CASE/WHEN clauses and parameter list
                    case_whens = []
                    params = []
                    for content, text_hash in valid_pairs:
                        case_whens.append("WHEN content = %s THEN %s")
                        params.extend([content, text_hash])

                    # Build WHERE clause with distinct contents
                    unique_contents = [content for content, _ in valid_pairs]
                    params.extend(unique_contents)

                    # Construct complete SQL with proper identifier quoting
                    case_sql = " ".join(case_whens)
                    in_placeholders = ",".join("%s" for _ in unique_contents)
                    update_sql = sql.SQL(
                        "UPDATE {}.{} "
                        "SET text_hash = CASE " + case_sql + " ELSE text_hash END "
                        "WHERE text_hash IS NULL AND content IN (" + in_placeholders + ")"
                    ).format(
                        sql.Identifier(self.schema_name),
                        sql.Identifier(self.table_name),
                    )

                    cur.execute(update_sql, params)

                    if cur.rowcount > 0:
                        logger.info("Set pre-computed text_hash for %d documents in single batch", cur.rowcount)

        except Exception as _:
            logger.exception("Error setting text_hash for documents")
            # Don't raise - fallback to populate_text_hash_from_content() will handle it

    def setup(self) -> VectorStore:
        """Set up and get the PGVector store synchronously."""
        if self._store is None:
            max_retries = 3
            retries = 0
            while True:
                try:
                    engine = self._create_engine()
                    self._init_table(engine)
                    engine = self._create_engine()

                    logger.info("Initializing PGVector store synchronously (schema=%s)...", self.schema_name)
                    self._store = PGVectorStore.create_sync(
                        engine=engine,
                        table_name=self.table_name,
                        embedding_service=self.embeddings,
                        metadata_columns=self.metadata_columns,
                        id_column=self._id_column,
                        metadata_json_column=settings.vector_store_metadata_column,
                        schema_name=self.schema_name,
                    )
                    logger.info("PGVector store initialized (schema=%s).", self.schema_name)
                except EmbeddingDimensionMismatchError:
                    raise
                except Exception as e:
                    logger.error("Error setting up PGVector store -> %s", e)
                    if retries >= max_retries:
                        logger.error("Max retries reached. Failing gracefully.")
                        raise
                    logger.info("Retrying in 10 seconds...")
                    time.sleep(10)
                    retries += 1
                else:
                    break

        return self._store

    def add_documents_with_embeddings(
        self,
        documents: list[Document],
        embeddings_list: list[list[float]],
        text_hashes: list[str | None],
    ) -> list[tuple[str, str | None]]:
        """Execute batch INSERT into the vector store.

        This method is used during chunk deduplication when the same content hash already exists.
        It inserts documents with pre-computed embeddings to avoid re-computing them, and optionally
        stores the pre-computed text hash for efficient future deduplication.
        Shared implementation for :meth:`add_documents`.

        Args:
            documents: List of langchain Document objects.
            embeddings_list: List of embedding vectors matching documents order (from cache).
            text_hashes: Optional list of pre-computed text hashes (SHA256 hex strings).
                        If not provided, will compute from document content.

        For each document, if the corresponding entry in *text_hashes* is not None,
        it is used as the text hash. If it is None, a hash is computed from the document
        content using whitespace normalization (" ".join(doc.page_content.split())) and
        SHA256, matching the behavior of generate_chunk_hash().

        Returns:
            List of (langchain_id, text_hash) pairs in insertion order.

        """
        metadata_col = settings.vector_store_metadata_column
        insert_query = sql.SQL(
            "INSERT INTO {schema}.{table}"
            " (langchain_id, {metadata_col}, content, embedding, text_hash, workspace_id, document_id)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s)"
        ).format(
            schema=sql.Identifier(self.schema_name),
            table=sql.Identifier(self.table_name),
            metadata_col=sql.Identifier(metadata_col),
        )

        psycopg_conn_str = self.connection_string.replace(_PSYCOPG_PREFIX, _POSTGRES_PREFIX)

        # Batch prepare all parameters efficiently
        prepared_data = []
        pairs = []

        for idx, (doc, embedding, text_hash) in enumerate(zip(documents, embeddings_list, text_hashes, strict=True)):
            langchain_id = str(uuid.uuid4())
            metadata = doc.metadata or {}
            workspace_id = metadata.get("workspace_id") or metadata.get("workspaceID") or ""
            document_id = metadata.get("document_id") or metadata.get("documentID") or ""

            # Use provided text_hash or compute from document content
            # Apply whitespace normalization to match generate_chunk_hash()
            if text_hashes[idx] is not None:
                text_hash = text_hashes[idx]
            else:
                normalized_content = " ".join(doc.page_content.split())
                text_hash = hashlib.sha256(normalized_content.encode()).hexdigest()

            embedding_str = "[" + ",".join(str(float(e)) for e in _embedding_to_list(embedding)) + "]"

            prepared_data.append(
                (
                    langchain_id,
                    json.dumps(metadata),
                    doc.page_content,
                    embedding_str,
                    text_hash,
                    workspace_id,
                    document_id,
                )
            )
            pairs.append((langchain_id, text_hash))

        # Execute all inserts in a single batch
        if prepared_data:
            with psycopg.connect(psycopg_conn_str) as conn:
                conn.autocommit = True
                with conn.cursor() as cur:
                    cur.executemany(insert_query, prepared_data)

        return pairs

    def add_documents(self, documents: list[Document]) -> list[str]:
        """Insert documents into the vector store with correct metadata in a single operation.

        Args:
            documents: List of LangChain Document objects to insert.

        Returns:
            List of ``langchain_id`` UUIDs (one per document, in insertion order).

        """
        if not documents:
            return []

        # Batch generate embeddings for all documents at once
        content_list = [doc.page_content for doc in documents]
        embeddings_list = self.embeddings.embed_documents(content_list)

        # Create text hashes for each document (no deduplication needed here, just insert)
        text_hashes = [None] * len(documents)

        try:
            pairs = self.add_documents_with_embeddings(documents, embeddings_list, text_hashes)
        except Exception as e:
            logger.error("Error adding documents with embeddings: %s", e)
            raise

        langchain_ids = [lid for lid, _ in pairs]
        logger.info("Inserted %d documents", len(langchain_ids))
        return langchain_ids
