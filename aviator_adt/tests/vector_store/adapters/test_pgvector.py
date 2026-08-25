"""Test suite for PGVector adapter."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import VectorStore
from sqlalchemy import AsyncAdaptedQueuePool
from sqlalchemy.schema import CreateSchema

from aviator.exceptions import EmbeddingDimensionMismatchError
from aviator.settings import settings
from aviator.vector_store.adapters.pgvector import PGVectorStoreAdapter
from aviator.vector_store.schema import get_embedding_ann_index_name


class TestPGVectorStoreAdapter:
    """Tests for PGVectorStoreAdapter."""

    @pytest.fixture
    def mock_embeddings(self):
        """Create mock embeddings."""
        return MagicMock(spec=Embeddings)

    @pytest.fixture
    def adapter(self, mock_embeddings, monkeypatch):
        """Create PGVectorStoreAdapter instance."""
        monkeypatch.setattr(
            settings, "postgres_connection", MagicMock(encoded_string=lambda: "postgresql://user:pass@localhost/db")
        )
        monkeypatch.setattr(settings, "vector_store_table_name", "test_embeddings")
        monkeypatch.setattr(settings, "vector_size", 768)
        monkeypatch.setattr(settings, "vector_storage_type", "vector")
        monkeypatch.setattr(settings, "vector_index_type", "hnsw")
        monkeypatch.setattr(settings, "ivfflat_lists", 100)
        monkeypatch.setattr(settings, "ivfflat_probes", None)
        return PGVectorStoreAdapter(mock_embeddings)

    def test_init(self, adapter, mock_embeddings):
        """Test adapter initialization."""
        assert adapter.embeddings == mock_embeddings
        assert adapter.table_name == "test_embeddings"
        assert adapter.vector_size == 768
        assert adapter.metadata_columns == ["workspace_id", "document_id", "text_hash"]

    def test_get_connection_string(self, monkeypatch):
        """Test connection string conversion."""
        monkeypatch.setattr(
            settings, "postgres_connection", MagicMock(encoded_string=lambda: "postgresql://user:pass@localhost/db")
        )

        conn_str = PGVectorStoreAdapter._get_connection_string()

        assert conn_str == "postgresql+psycopg://user:pass@localhost/db"

    def test_connection_string_replacement(self, adapter):
        """Test that connection string has correct prefix."""
        assert adapter.connection_string.startswith("postgresql+psycopg://")
        assert "postgresql://" not in adapter.connection_string or "postgresql+psycopg://" in adapter.connection_string

    def test_create_engine(self, adapter):
        """Test PGEngine creation."""
        with patch("aviator.vector_store.adapters.pgvector.PGEngine") as mock_engine:
            mock_engine.from_connection_string.return_value = MagicMock()

            engine = adapter._create_engine()

            mock_engine.from_connection_string.assert_called_once_with(
                url=adapter.connection_string,
                poolclass=AsyncAdaptedQueuePool,
                **settings.postgres_connection_settings,
            )
            assert engine is not None

    def test_get_existing_vector_dimension_table_not_exists(self, adapter):
        """Test getting vector dimension when table doesn't exist."""
        with patch("aviator.vector_store.adapters.pgvector.psycopg.connect") as mock_connect:
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = [False]
            mock_conn = MagicMock()
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)
            mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
            mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
            mock_connect.return_value = mock_conn

            result = adapter._get_existing_vector_dimension()

            assert result is None

    def test_get_existing_vector_dimension_with_fixed_dimension(self, adapter):
        """Test getting vector dimension with fixed dimension."""
        with patch("aviator.vector_store.adapters.pgvector.psycopg.connect") as mock_connect:
            mock_cursor = MagicMock()
            mock_cursor.fetchone.side_effect = [[True], [768]]
            mock_conn = MagicMock()
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)
            mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
            mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
            mock_connect.return_value = mock_conn

            result = adapter._get_existing_vector_dimension()

            assert result == 768

    def test_get_existing_vector_dimension_flexible(self, adapter):
        """Test getting vector dimension with flexible dimension."""
        with patch("aviator.vector_store.adapters.pgvector.psycopg.connect") as mock_connect:
            mock_cursor = MagicMock()
            mock_cursor.fetchone.side_effect = [[True], [-1]]
            mock_conn = MagicMock()
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)
            mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
            mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
            mock_connect.return_value = mock_conn

            result = adapter._get_existing_vector_dimension()

            assert result is None

    def test_get_existing_vector_dimension_error(self, adapter):
        """Test error handling in get_existing_vector_dimension."""
        with patch("aviator.vector_store.adapters.pgvector.psycopg.connect", side_effect=Exception("Connection error")):
            result = adapter._get_existing_vector_dimension()

            assert result is None

    def test_ensure_schema_exists(self, adapter):
        """Test schema bootstrap for non-existing schemas."""
        with patch("aviator.vector_store.adapters.pgvector.create_engine") as mock_create_engine:
            mock_engine = MagicMock()
            mock_begin_conn = MagicMock()
            mock_engine.begin.return_value.__enter__ = MagicMock(return_value=mock_begin_conn)
            mock_engine.begin.return_value.__exit__ = MagicMock(return_value=False)
            mock_create_engine.return_value = mock_engine

            adapter._ensure_schema_exists()

            mock_create_engine.assert_called_once_with(
                adapter.connection_string,
                **settings.postgres_connection_settings,
            )
            mock_begin_conn.execute.assert_called_once()
            statement = mock_begin_conn.execute.call_args.args[0]
            assert isinstance(statement, CreateSchema)
            mock_engine.dispose.assert_called_once()

    def test_validate_embedding_dimension_mismatch_raises(self, adapter):
        """Test mismatch between configured and stored vector dimensions raises."""
        with (
            patch.object(adapter, "_get_existing_vector_dimension", return_value=1536),
            pytest.raises(EmbeddingDimensionMismatchError, match="Vector dimension mismatch"),
        ):
            adapter._validate_embedding_dimension()

    def test_migrate_embedding_type_noop_when_matching(self, adapter):
        """Test type migration is skipped when the configured and stored types match."""
        with (
            patch.object(adapter, "_get_existing_embedding_type", return_value="vector"),
            patch.object(adapter, "_get_embedding_index_names") as mock_get_indexes,
            patch("aviator.vector_store.adapters.pgvector.psycopg.connect") as mock_connect,
        ):
            adapter._migrate_embedding_type_if_needed()

            mock_get_indexes.assert_not_called()
            mock_connect.assert_not_called()

    def test_migrate_embedding_type_drops_indexes_and_converts_to_halfvec(self, adapter, monkeypatch):
        """Test type migration drops embedding indexes and converts the column to halfvec."""
        monkeypatch.setattr(adapter, "vector_storage_type", "halfvec")

        with (
            patch.object(adapter, "_get_existing_embedding_type", return_value="vector"),
            patch.object(adapter, "_get_embedding_index_names", return_value=["legacy_embedding_idx"]),
            patch.object(adapter, "_get_existing_hnsw_index_params", return_value=(16, 200)),
            patch("aviator.vector_store.adapters.pgvector.psycopg.connect") as mock_connect,
        ):
            mock_cursor = MagicMock()
            mock_conn = MagicMock()
            mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
            mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
            mock_connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_connect.return_value.__exit__ = MagicMock(return_value=False)

            adapter._migrate_embedding_type_if_needed()

            first_stmt = str(mock_cursor.execute.call_args_list[0].args[0])
            second_stmt = str(mock_cursor.execute.call_args_list[1].args[0])
            assert "DROP INDEX IF EXISTS" in first_stmt
            assert "legacy_embedding_idx" in first_stmt
            assert "ALTER TABLE" in second_stmt
            assert "halfvec(768)" in second_stmt
            assert adapter._preserved_hnsw_m == 16
            assert adapter._preserved_hnsw_ef_construction == 200
            mock_conn.commit.assert_called_once()

    def test_migrate_embedding_type_drops_indexes_and_converts_to_vector(self, adapter, monkeypatch):
        """Test type migration converts an existing halfvec column back to vector."""
        monkeypatch.setattr(adapter, "vector_storage_type", "vector")

        with (
            patch.object(adapter, "_get_existing_embedding_type", return_value="halfvec"),
            patch.object(adapter, "_get_embedding_index_names", return_value=["legacy_embedding_idx"]),
            patch.object(adapter, "_get_existing_hnsw_index_params", return_value=(16, 200)),
            patch("aviator.vector_store.adapters.pgvector.psycopg.connect") as mock_connect,
        ):
            mock_cursor = MagicMock()
            mock_conn = MagicMock()
            mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
            mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
            mock_connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_connect.return_value.__exit__ = MagicMock(return_value=False)

            adapter._migrate_embedding_type_if_needed()

            second_stmt = str(mock_cursor.execute.call_args_list[1].args[0])
            assert "vector(768)" in second_stmt
            mock_conn.commit.assert_called_once()

    def test_migrate_embedding_type_preserves_ivfflat_lists(self, adapter, monkeypatch):
        """Test type migration preserves IVFFlat list count when IVFFlat is the configured index type."""
        monkeypatch.setattr(adapter, "vector_storage_type", "halfvec")
        monkeypatch.setattr(adapter, "vector_index_type", "ivfflat")

        with (
            patch.object(adapter, "_get_existing_embedding_type", return_value="vector"),
            patch.object(adapter, "_get_embedding_index_names", return_value=["legacy_embedding_idx"]),
            patch.object(adapter, "_get_existing_ivfflat_index_lists", return_value=256),
            patch("aviator.vector_store.adapters.pgvector.psycopg.connect") as mock_connect,
        ):
            mock_cursor = MagicMock()
            mock_conn = MagicMock()
            mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
            mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
            mock_connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_connect.return_value.__exit__ = MagicMock(return_value=False)

            adapter._migrate_embedding_type_if_needed()

            assert adapter._preserved_ivfflat_lists == 256
            mock_conn.commit.assert_called_once()

    def test_create_indexes_uses_preserved_hnsw_params(self, adapter):
        """Test recreated index preserves previously detected HNSW settings."""
        adapter._preserved_hnsw_m = 16
        adapter._preserved_hnsw_ef_construction = 200

        with patch("aviator.vector_store.adapters.pgvector.psycopg.connect") as mock_connect:
            mock_cursor = MagicMock()
            mock_conn = MagicMock()
            mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
            mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
            mock_connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_connect.return_value.__exit__ = MagicMock(return_value=False)

            adapter._create_indexes()

            index_sql = mock_cursor.execute.call_args_list[1].args[0]
            assert "WITH (m = 16, ef_construction = 200)" in index_sql

    def test_create_indexes_uses_halfvec_opclass(self, adapter, monkeypatch):
        """Test halfvec mode creates HNSW index with halfvec_cosine_ops."""
        monkeypatch.setattr(adapter, "vector_storage_type", "halfvec")

        with patch("aviator.vector_store.adapters.pgvector.psycopg.connect") as mock_connect:
            mock_cursor = MagicMock()
            mock_conn = MagicMock()
            mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
            mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
            mock_connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_connect.return_value.__exit__ = MagicMock(return_value=False)

            adapter._create_indexes()

            index_sql = mock_cursor.execute.call_args_list[1].args[0]
            assert "USING hnsw" in index_sql
            assert "halfvec_cosine_ops" in index_sql

    def test_create_indexes_uses_ivfflat_with_preserved_lists(self, adapter, monkeypatch):
        """Test IVFFlat mode creates IVFFlat index with preserved list count."""
        monkeypatch.setattr(adapter, "vector_index_type", "ivfflat")
        adapter._preserved_ivfflat_lists = 256

        with patch("aviator.vector_store.adapters.pgvector.psycopg.connect") as mock_connect:
            mock_cursor = MagicMock()
            mock_conn = MagicMock()
            mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
            mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
            mock_connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_connect.return_value.__exit__ = MagicMock(return_value=False)

            adapter._create_indexes()

            index_sql = mock_cursor.execute.call_args_list[1].args[0]
            assert "USING ivfflat" in index_sql
            assert "WITH (lists = 256)" in index_sql

    @pytest.mark.parametrize(
        ("index_type", "expected_name"),
        [("hnsw", "idx_test_embeddings_embedding_hnsw"), ("ivfflat", "idx_test_embeddings_embedding_ivfflat")],
    )
    def test_get_embedding_index_name_uses_managed_method_suffix(self, adapter, monkeypatch, index_type, expected_name):
        """Test the managed ANN index name follows the method-specific convention."""
        monkeypatch.setattr(adapter, "vector_index_type", index_type)

        assert adapter._get_embedding_index_name() == expected_name
        assert get_embedding_ann_index_name(adapter.table_name, index_type) == expected_name

    def test_get_configured_ivfflat_lists_derives_from_total_chunks(self, adapter, monkeypatch):
        """Test IVFFlat lists default to floor(sqrt(total_chunks)) when unset."""
        monkeypatch.setattr(adapter, "ivfflat_lists", None)

        with patch.object(adapter, "_get_total_chunks", return_value=10_000):
            assert adapter._get_configured_ivfflat_lists() == 100

    def test_get_effective_ivfflat_probes_derives_from_lists(self, adapter, monkeypatch):
        """Test IVFFlat probes default to floor(sqrt(lists)) when unset."""
        monkeypatch.setattr(adapter, "ivfflat_lists", None)
        monkeypatch.setattr(adapter, "ivfflat_probes", None)

        with patch.object(adapter, "_get_total_chunks", return_value=10_000):
            assert adapter._get_effective_ivfflat_probes() == 10

    def test_get_connection_settings_injects_ivfflat_probes(self, adapter, monkeypatch):
        """Test IVFFlat probes are applied as a PostgreSQL session option."""
        monkeypatch.setattr(adapter, "vector_index_type", "ivfflat")
        monkeypatch.setattr(adapter, "ivfflat_lists", None)
        monkeypatch.setattr(adapter, "ivfflat_probes", None)

        with patch.object(adapter, "_get_total_chunks", return_value=10_000):
            connection_settings = adapter._get_connection_settings()

        assert "-c ivfflat.probes=10" in connection_settings["connect_args"]["options"]

    def test_reconcile_embedding_index_drops_mismatched_indexes(self, adapter, monkeypatch):
        """Test drifted embedding indexes are dropped so the managed index can be recreated."""
        monkeypatch.setattr(adapter, "vector_index_type", "ivfflat")

        with (
            patch.object(
                adapter,
                "_get_embedding_indexes",
                return_value=[
                    {
                        "name": "idx_public_test_embeddings_embedding",
                        "method": "hnsw",
                        "reloptions": ["m=16", "ef_construction=200"],
                        "definition": "CREATE INDEX idx_public_test_embeddings_embedding ON public.test_embeddings USING hnsw (embedding vector_cosine_ops)",
                    }
                ],
            ),
            patch.object(adapter, "_drop_embedding_indexes") as mock_drop,
        ):
            adapter._reconcile_embedding_index_if_needed()

            mock_drop.assert_called_once_with(
                ["idx_public_test_embeddings_embedding"],
                reason="index configuration drift",
            )

    def test_reconcile_embedding_index_skips_current_managed_name(self, adapter):
        """Test a correctly named managed ANN index is left in place."""
        configured_reloptions = [
            f"m={adapter.hnsw_m}",
            f"ef_construction={adapter.hnsw_ef_construction}",
        ]

        with (
            patch.object(
                adapter,
                "_get_embedding_indexes",
                return_value=[
                    {
                        "name": "idx_test_embeddings_embedding_hnsw",
                        "method": "hnsw",
                        "reloptions": configured_reloptions,
                        "definition": "CREATE INDEX idx_test_embeddings_embedding_hnsw ON public.test_embeddings USING hnsw (embedding vector_cosine_ops)",
                    }
                ],
            ),
            patch.object(adapter, "_drop_embedding_indexes") as mock_drop,
        ):
            adapter._reconcile_embedding_index_if_needed()

            mock_drop.assert_not_called()

    def test_init_table_halfvec_path(self, adapter, monkeypatch):
        """Test halfvec mode uses manual halfvec table creation path."""
        monkeypatch.setattr(adapter, "vector_storage_type", "halfvec")
        mock_engine = MagicMock()

        with (
            patch.object(adapter, "_ensure_schema_exists"),
            patch.object(adapter, "_validate_embedding_dimension"),
            patch.object(adapter, "_migrate_embedding_type_if_needed"),
            patch.object(adapter, "_ensure_metadata_columns_exist") as mock_ensure_metadata_columns,
            patch.object(adapter, "_reconcile_embedding_index_if_needed") as mock_reconcile,
            patch.object(adapter, "_create_halfvec_table") as mock_create_halfvec_table,
            patch.object(adapter, "_create_indexes") as mock_create_indexes,
        ):
            adapter._init_table(mock_engine)

            mock_create_halfvec_table.assert_called_once()
            mock_ensure_metadata_columns.assert_called_once()
            mock_reconcile.assert_called_once()
            mock_create_indexes.assert_called_once()
            mock_engine.init_vectorstore_table.assert_not_called()

    def test_init_table_success(self, adapter):
        """Test successful table initialization."""
        mock_engine = MagicMock()

        with (
            patch.object(adapter, "_ensure_schema_exists"),
            patch.object(adapter, "_validate_embedding_dimension"),
            patch.object(adapter, "_migrate_embedding_type_if_needed"),
            patch.object(adapter, "_reconcile_embedding_index_if_needed") as mock_reconcile,
            patch.object(adapter, "_create_indexes"),
            patch("aviator.vector_store.adapters.pgvector.ensure_metadata_column_is_jsonb"),
        ):
            adapter._init_table(mock_engine)

            mock_engine.init_vectorstore_table.assert_called_once()
            mock_reconcile.assert_called_once()
            call_kwargs = mock_engine.init_vectorstore_table.call_args.kwargs
            assert call_kwargs["table_name"] == "test_embeddings"
            assert call_kwargs["vector_size"] == 768

    def test_init_table_ensures_schema_before_create(self, adapter):
        """Test that _init_table ensures schema creation first."""
        mock_engine = MagicMock()

        with (
            patch.object(adapter, "_ensure_schema_exists") as mock_ensure_schema,
            patch.object(adapter, "_create_indexes"),
            patch("aviator.vector_store.adapters.pgvector.ensure_metadata_column_is_jsonb"),
        ):
            adapter._init_table(mock_engine)

            mock_ensure_schema.assert_called_once()
            mock_engine.init_vectorstore_table.assert_called_once()

    def test_init_table_matching_dimension(self, adapter):
        """Test table initialization with matching dimension."""
        mock_engine = MagicMock()

        with (
            patch.object(adapter, "_ensure_schema_exists"),
            patch.object(adapter, "_validate_embedding_dimension"),
            patch.object(adapter, "_migrate_embedding_type_if_needed"),
            patch.object(adapter, "_create_indexes"),
            patch("aviator.vector_store.adapters.pgvector.ensure_metadata_column_is_jsonb"),
        ):
            adapter._init_table(mock_engine)

            mock_engine.init_vectorstore_table.assert_called_once()

    def test_init_table_warning_on_exception(self, adapter):
        """Test that table initialization re-raises non-ProgrammingError exceptions."""
        mock_engine = MagicMock()
        mock_engine.init_vectorstore_table.side_effect = Exception("Some other error")

        with (
            patch.object(adapter, "_ensure_schema_exists"),
            patch.object(adapter, "_validate_embedding_dimension"),
            patch.object(adapter, "_migrate_embedding_type_if_needed"),
            pytest.raises(Exception, match="Some other error"),
        ):
            adapter._init_table(mock_engine)

    @pytest.mark.anyio
    async def test_asetup(self, adapter):
        """Test async setup of vector store."""
        mock_engine = MagicMock()
        mock_store = MagicMock(spec=VectorStore)

        with (
            patch.object(adapter, "_create_engine", return_value=mock_engine),
            patch.object(adapter, "_init_table"),
            patch(
                "aviator.vector_store.adapters.pgvector.PGVectorStore.create",
                new_callable=AsyncMock,
                return_value=mock_store,
            ),
        ):
            await adapter.asetup()

            assert adapter._store == mock_store

    def test_setup_sync(self, adapter):
        """Test synchronous setup of vector store."""
        mock_engine = MagicMock()
        mock_store = MagicMock(spec=VectorStore)

        with (
            patch.object(adapter, "_create_engine", return_value=mock_engine),
            patch.object(adapter, "_init_table"),
            patch("aviator.vector_store.adapters.pgvector.PGVectorStore.create_sync", return_value=mock_store),
        ):
            result = adapter.setup()

            assert result == mock_store
            assert adapter._store == mock_store

    def test_setup_sync_cached(self, adapter):
        """Test that sync setup returns cached store."""
        mock_store = MagicMock(spec=VectorStore)
        adapter._store = mock_store

        result = adapter.setup()

        assert result == mock_store

    def test_metadata_columns(self, adapter):
        """Test metadata columns are set correctly."""
        assert "workspace_id" in adapter.metadata_columns
        assert "document_id" in adapter.metadata_columns
        assert "text_hash" in adapter.metadata_columns
        assert len(adapter.metadata_columns) == 3

    def test_adapter_inherits_base(self, adapter):
        """Test that adapter has base class methods."""
        assert hasattr(adapter, "get_store")
        assert hasattr(adapter, "asetup")
        assert hasattr(adapter, "setup")

    def test_get_store_before_setup_raises(self, adapter):
        """Test that get_store raises error before setup."""
        with pytest.raises(RuntimeError, match="Vector store not initialized"):
            adapter.get_store()

    def test_get_store_after_setup(self, adapter):
        """Test that get_store returns store after setup."""
        mock_store = MagicMock(spec=VectorStore)
        adapter._store = mock_store

        result = adapter.get_store()

        assert result == mock_store

    def test_init_table_metadata_columns(self, adapter):
        """Test that init_table uses correct metadata columns."""
        mock_engine = MagicMock()

        with (
            patch.object(adapter, "_ensure_schema_exists"),
            patch.object(adapter, "_validate_embedding_dimension"),
            patch.object(adapter, "_migrate_embedding_type_if_needed"),
            patch.object(adapter, "_create_indexes"),
            patch("aviator.vector_store.adapters.pgvector.ensure_metadata_column_is_jsonb"),
        ):
            adapter._init_table(mock_engine)

            call_kwargs = mock_engine.init_vectorstore_table.call_args.kwargs
            assert "metadata_columns" in call_kwargs
            metadata_cols = call_kwargs["metadata_columns"]
            assert len(metadata_cols) == 3

    def test_connection_string_property(self, adapter):
        """Test that connection_string is set on init."""
        assert adapter.connection_string is not None
        assert isinstance(adapter.connection_string, str)

    def test_table_name_from_settings(self, mock_embeddings, monkeypatch):
        """Test that table name comes from settings."""
        monkeypatch.setattr(settings, "vector_store_table_name", "custom_table")
        monkeypatch.setattr(
            settings, "postgres_connection", MagicMock(encoded_string=lambda: "postgresql://user:pass@localhost/db")
        )

        adapter = PGVectorStoreAdapter(mock_embeddings)

        assert adapter.table_name == "custom_table"

    def test_vector_size_from_settings(self, mock_embeddings, monkeypatch):
        """Test that vector size comes from settings."""
        monkeypatch.setattr(settings, "vector_size", 1536)
        monkeypatch.setattr(
            settings, "postgres_connection", MagicMock(encoded_string=lambda: "postgresql://user:pass@localhost/db")
        )

        adapter = PGVectorStoreAdapter(mock_embeddings)

        assert adapter.vector_size == 1536


class TestAgetDistinctDocumentIds:
    """Tests for aget_distinct_document_ids."""

    @pytest.fixture
    def adapter(self, monkeypatch):
        monkeypatch.setattr(
            settings, "postgres_connection", MagicMock(encoded_string=lambda: "postgresql://user:pass@localhost/db")
        )
        monkeypatch.setattr(settings, "vector_store_table_name", "test_embeddings")
        monkeypatch.setattr(settings, "vector_size", 768)
        monkeypatch.setattr(settings, "vector_storage_type", "vector")
        monkeypatch.setattr(settings, "vector_index_type", "hnsw")
        monkeypatch.setattr(settings, "ivfflat_lists", 100)
        monkeypatch.setattr(settings, "ivfflat_probes", None)
        return PGVectorStoreAdapter(MagicMock(spec=Embeddings))

    def _make_async_conn(self, mock_cursor):
        """Build an AsyncMock connection whose cursor() async-context yields mock_cursor."""
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_cursor)
        ctx.__aexit__ = AsyncMock(return_value=False)

        mock_conn = AsyncMock()
        mock_conn.cursor = MagicMock(return_value=ctx)
        return mock_conn

    @pytest.mark.anyio
    async def test_returns_document_ids(self, adapter):
        """Returns distinct document IDs from query results."""
        mock_cursor = AsyncMock()
        mock_cursor.fetchall.return_value = [("doc-1",), ("doc-2",), ("doc-3",)]
        mock_conn = self._make_async_conn(mock_cursor)

        with patch("aviator.vector_store.adapters.pgvector.psycopg.AsyncConnection.connect", return_value=mock_conn):
            result = await adapter.aget_distinct_document_ids(metadata_filter={"workspace_id": {"$eq": "ws-1"}})

        assert result == ["doc-1", "doc-2", "doc-3"]
        mock_cursor.execute.assert_called_once()
        mock_conn.close.assert_called_once()

    @pytest.mark.anyio
    async def test_returns_empty_for_no_results(self, adapter):
        """Returns empty list when no documents match."""
        mock_cursor = AsyncMock()
        mock_cursor.fetchall.return_value = []
        mock_conn = self._make_async_conn(mock_cursor)

        with patch("aviator.vector_store.adapters.pgvector.psycopg.AsyncConnection.connect", return_value=mock_conn):
            result = await adapter.aget_distinct_document_ids(metadata_filter=None)

        assert result == []

    @pytest.mark.anyio
    async def test_skips_null_document_ids(self, adapter):
        """Filters out NULL document_id values from results."""
        mock_cursor = AsyncMock()
        mock_cursor.fetchall.return_value = [("doc-1",), (None,), ("doc-2",)]
        mock_conn = self._make_async_conn(mock_cursor)

        with patch("aviator.vector_store.adapters.pgvector.psycopg.AsyncConnection.connect", return_value=mock_conn):
            result = await adapter.aget_distinct_document_ids(metadata_filter=None)

        assert result == ["doc-1", "doc-2"]

    @pytest.mark.anyio
    async def test_closes_connection_on_error(self, adapter):
        """Ensures connection is closed even when query fails."""
        mock_cursor = AsyncMock()
        mock_cursor.execute.side_effect = RuntimeError("DB error")
        mock_conn = self._make_async_conn(mock_cursor)

        with (
            patch("aviator.vector_store.adapters.pgvector.psycopg.AsyncConnection.connect", return_value=mock_conn),
            pytest.raises(RuntimeError, match="DB error"),
        ):
            await adapter.aget_distinct_document_ids(metadata_filter=None)

        mock_conn.close.assert_called_once()


class TestNoIndexMode:
    """Tests for vector_index_type=None (no ANN index, sequential scan)."""

    @pytest.fixture
    def adapter(self, monkeypatch):
        monkeypatch.setattr(
            settings, "postgres_connection", MagicMock(encoded_string=lambda: "postgresql://user:pass@localhost/db")
        )
        monkeypatch.setattr(settings, "vector_store_table_name", "test_embeddings")
        monkeypatch.setattr(settings, "vector_size", 768)
        monkeypatch.setattr(settings, "vector_storage_type", "vector")
        monkeypatch.setattr(settings, "vector_index_type", None)
        monkeypatch.setattr(settings, "ivfflat_lists", 100)
        monkeypatch.setattr(settings, "ivfflat_probes", None)
        return PGVectorStoreAdapter(MagicMock(spec=Embeddings))

    def test_reconcile_is_noop(self, adapter):
        """_reconcile_embedding_index_if_needed returns immediately when index type is None."""
        with patch.object(adapter, "_get_embedding_indexes") as mock_get:
            adapter._reconcile_embedding_index_if_needed()
            mock_get.assert_not_called()

    def test_embedding_index_matches_configuration_no_indexes(self, adapter):
        """No existing indexes is the correct state when index type is None."""
        assert adapter._embedding_index_matches_configuration([]) is True

    def test_embedding_index_matches_configuration_unexpected_index(self, adapter):
        """An existing index does NOT match when index type is None."""
        assert (
            adapter._embedding_index_matches_configuration(
                [{"name": "idx_test_embeddings_embedding_hnsw", "method": "hnsw", "reloptions": [], "definition": ""}]
            )
            is False
        )

    def test_get_configured_index_params_returns_empty(self, adapter):
        """No index params when index type is None."""
        assert adapter._get_configured_index_params() == {}

    def test_preserve_existing_index_params_returns_early(self, adapter):
        """Preserving params is a no-op when index type is None."""
        with patch.object(adapter, "_get_existing_hnsw_index_params") as mock_hnsw:
            adapter._preserve_existing_index_params_for_configured_type()
            mock_hnsw.assert_not_called()
        assert adapter._preserved_hnsw_m is None
        assert adapter._preserved_hnsw_ef_construction is None
        assert adapter._preserved_ivfflat_lists is None

    def test_create_indexes_skips_ann_index(self, adapter):
        """_create_indexes only creates metadata indexes, not an ANN index."""
        with patch("aviator.vector_store.adapters.pgvector.psycopg.connect") as mock_connect:
            mock_cursor = MagicMock()
            mock_conn = MagicMock()
            mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
            mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
            mock_connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_connect.return_value.__exit__ = MagicMock(return_value=False)

            adapter._create_indexes()

            executed_sqls = [str(call.args[0]) for call in mock_cursor.execute.call_args_list]
            assert not any("USING hnsw" in s or "USING ivfflat" in s for s in executed_sqls)
            assert any("GIN" in s for s in executed_sqls)

    def test_get_embedding_index_name_returns_base_prefix(self, adapter):
        """Index name uses base prefix (no method suffix) when index type is None."""
        assert adapter._get_embedding_index_name() == "idx_test_embeddings_embedding"


class TestSimilaritySearchConnectionOptions:
    """Tests for psycopg connection options during similarity search."""

    @pytest.fixture
    def adapter(self, monkeypatch):
        monkeypatch.setattr(
            settings, "postgres_connection", MagicMock(encoded_string=lambda: "postgresql://user:pass@localhost/db")
        )
        monkeypatch.setattr(settings, "vector_store_table_name", "test_embeddings")
        monkeypatch.setattr(settings, "vector_size", 768)
        monkeypatch.setattr(settings, "vector_storage_type", "vector")
        monkeypatch.setattr(settings, "vector_index_type", "hnsw")
        monkeypatch.setattr(settings, "ivfflat_lists", 100)
        monkeypatch.setattr(settings, "ivfflat_probes", 63)
        return PGVectorStoreAdapter(MagicMock(spec=Embeddings))

    def _make_async_conn(self, mock_cursor):
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_cursor)
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_conn = AsyncMock()
        mock_conn.cursor = MagicMock(return_value=ctx)
        return mock_conn

    @pytest.mark.anyio
    async def test_similarity_search_reuses_default_connection_options_for_hnsw(self, adapter, monkeypatch):
        """asimilarity_search_with_relevance_scores reuses pooled connection options for HNSW."""
        monkeypatch.setattr(adapter, "vector_index_type", "hnsw")
        mock_embeddings = MagicMock()
        mock_embeddings.aembed_query = AsyncMock(return_value=[0.1] * 768)
        adapter.embeddings = mock_embeddings

        mock_cursor = AsyncMock()
        mock_cursor.fetchall.return_value = []
        mock_conn = self._make_async_conn(mock_cursor)

        with patch(
            "aviator.vector_store.adapters.pgvector.psycopg.AsyncConnection.connect",
            return_value=mock_conn,
        ) as mock_connect:
            await adapter.asimilarity_search_with_relevance_scores("test query")

        _, kwargs = mock_connect.call_args
        assert kwargs.get("options") == settings.postgres_connection_settings["connect_args"]["options"]
        assert mock_cursor.execute.call_count == 1

    @pytest.mark.anyio
    async def test_similarity_search_applies_ivfflat_session_param(self, adapter, monkeypatch):
        """asimilarity_search_with_relevance_scores passes ivfflat.probes via connect options."""
        monkeypatch.setattr(adapter, "vector_index_type", "ivfflat")
        monkeypatch.setattr(adapter, "ivfflat_probes", 63)
        mock_embeddings = MagicMock()
        mock_embeddings.aembed_query = AsyncMock(return_value=[0.1] * 768)
        adapter.embeddings = mock_embeddings

        mock_cursor = AsyncMock()
        mock_cursor.fetchall.return_value = []
        mock_conn = self._make_async_conn(mock_cursor)

        with patch(
            "aviator.vector_store.adapters.pgvector.psycopg.AsyncConnection.connect",
            return_value=mock_conn,
        ) as mock_connect:
            await adapter.asimilarity_search_with_relevance_scores("test query")

        _, kwargs = mock_connect.call_args
        assert "ivfflat.probes=63" in kwargs.get("options", "")
        # Only the search query; no preceding SET statement
        assert mock_cursor.execute.call_count == 1

    @pytest.mark.anyio
    async def test_similarity_search_no_ann_option_when_none(self, adapter, monkeypatch):
        """asimilarity_search_with_relevance_scores keeps only default connection options when ANN is disabled."""
        monkeypatch.setattr(adapter, "vector_index_type", None)
        mock_embeddings = MagicMock()
        mock_embeddings.aembed_query = AsyncMock(return_value=[0.1] * 768)
        adapter.embeddings = mock_embeddings

        mock_cursor = AsyncMock()
        mock_cursor.fetchall.return_value = []
        mock_conn = self._make_async_conn(mock_cursor)

        with patch(
            "aviator.vector_store.adapters.pgvector.psycopg.AsyncConnection.connect",
            return_value=mock_conn,
        ) as mock_connect:
            await adapter.asimilarity_search_with_relevance_scores("test query")

        _, kwargs = mock_connect.call_args
        assert kwargs.get("options") == settings.postgres_connection_settings["connect_args"]["options"]
        assert mock_cursor.execute.call_count == 1
