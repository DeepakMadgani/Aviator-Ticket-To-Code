"""Test suite for vector store factory."""

import pytest
from langchain_core.embeddings import Embeddings

from aviator.services.embeddings import EmbeddingsRegistry
from aviator.settings import settings
from aviator.vector_store.adapters import InMemoryVectorStoreAdapter, PGVectorStoreAdapter
from aviator.vector_store.factory import VectorStoreFactory


class TestVectorStoreFactory:
    """Tests for VectorStoreFactory."""

    def test_create_adapter_memory(self, monkeypatch):
        """Test creating in-memory vector store adapter."""
        monkeypatch.setattr(settings, "vector_store", "memory")

        adapter = VectorStoreFactory.create_adapter()

        assert isinstance(adapter, InMemoryVectorStoreAdapter)
        assert adapter.embeddings is not None

    def test_create_adapter_pgvector(self, monkeypatch):
        """Test creating PGVector store adapter."""
        monkeypatch.setattr(settings, "vector_store", "pgvectorstore")

        adapter = VectorStoreFactory.create_adapter()

        assert isinstance(adapter, PGVectorStoreAdapter)
        assert adapter.embeddings is not None

    def test_create_adapter_unsupported_type(self, monkeypatch):
        """Test creating adapter with unsupported type raises error."""
        monkeypatch.setattr(settings, "vector_store", "unsupported_type")

        with pytest.raises(ValueError, match="Unsupported vector store type"):
            VectorStoreFactory.create_adapter()

    def test_create_adapter_embeddings_instance(self, monkeypatch):
        """Test that created adapter has embeddings instance."""
        monkeypatch.setattr(settings, "vector_store", "memory")

        adapter = VectorStoreFactory.create_adapter()

        assert hasattr(adapter, "embeddings")
        assert isinstance(adapter.embeddings, Embeddings)

    def test_create_adapter_memory_multiple_times(self, monkeypatch):
        """Test creating multiple memory adapters returns different instances."""
        monkeypatch.setattr(settings, "vector_store", "memory")

        adapter1 = VectorStoreFactory.create_adapter()
        adapter2 = VectorStoreFactory.create_adapter()

        assert adapter1 is not adapter2
        assert isinstance(adapter1, InMemoryVectorStoreAdapter)
        assert isinstance(adapter2, InMemoryVectorStoreAdapter)

    def test_create_adapter_pgvector_multiple_times(self, monkeypatch):
        """Test creating multiple pgvector adapters returns different instances."""
        monkeypatch.setattr(settings, "vector_store", "pgvectorstore")

        adapter1 = VectorStoreFactory.create_adapter()
        adapter2 = VectorStoreFactory.create_adapter()

        assert adapter1 is not adapter2
        assert isinstance(adapter1, PGVectorStoreAdapter)
        assert isinstance(adapter2, PGVectorStoreAdapter)

    def test_create_adapter_switch_types(self, monkeypatch):
        """Test switching between different adapter types."""
        # First create memory adapter
        monkeypatch.setattr(settings, "vector_store", "memory")
        adapter1 = VectorStoreFactory.create_adapter()
        assert isinstance(adapter1, InMemoryVectorStoreAdapter)

        # Then create pgvector adapter
        monkeypatch.setattr(settings, "vector_store", "pgvectorstore")
        adapter2 = VectorStoreFactory.create_adapter()
        assert isinstance(adapter2, PGVectorStoreAdapter)

    def test_create_adapter_respects_settings(self, monkeypatch):
        """Test that factory respects settings configuration."""
        monkeypatch.setattr(settings, "vector_store", "memory")

        adapter = VectorStoreFactory.create_adapter()

        assert isinstance(adapter, InMemoryVectorStoreAdapter)

    def test_create_adapter_uses_embeddings_registry(self, monkeypatch):
        """Test that factory uses EmbeddingsRegistry for embeddings."""
        from unittest.mock import MagicMock, patch

        monkeypatch.setattr(settings, "vector_store", "memory")

        with patch.object(EmbeddingsRegistry, "get_embeddings") as mock_get:
            mock_embeddings = MagicMock(spec=Embeddings)
            mock_get.return_value = mock_embeddings

            adapter = VectorStoreFactory.create_adapter()

            mock_get.assert_called_once()
            assert adapter.embeddings == mock_embeddings

    def test_create_adapter_error_message(self, monkeypatch):
        """Test error message for unsupported type."""
        monkeypatch.setattr(settings, "vector_store", "invalid_store")

        with pytest.raises(ValueError) as exc_info:
            VectorStoreFactory.create_adapter()

        assert "invalid_store" in str(exc_info.value)
        assert "Unsupported vector store type" in str(exc_info.value)
