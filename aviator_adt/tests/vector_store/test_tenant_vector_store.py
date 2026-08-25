"""Tests for multi-tenancy in the vector store module."""

from unittest.mock import MagicMock, patch

from aviator.settings import settings
from aviator.vector_store import VectorStoreManager


class TestVectorStoreManagerTenancy:
    """Tests for tenant-aware VectorStoreManager."""

    def test_get_default_schema_returns_default_adapter(self):
        manager = VectorStoreManager()
        mock_adapter = MagicMock()
        mock_store = MagicMock()
        mock_adapter.setup.return_value = mock_store
        manager._adapter = mock_adapter

        result = manager.get(schema_name=None)

        assert result is mock_store
        mock_adapter.setup.assert_called_once()

    def test_get_public_schema_returns_default_adapter(self):
        manager = VectorStoreManager()
        mock_adapter = MagicMock()
        mock_store = MagicMock()
        mock_adapter.setup.return_value = mock_store
        manager._adapter = mock_adapter

        result = manager.get(schema_name=settings.default_schema)

        assert result is mock_store

    @patch("aviator.vector_store.VectorStoreFactory.create_adapter")
    def test_get_tenant_schema_creates_new_adapter(self, mock_create):
        manager = VectorStoreManager()
        mock_adapter = MagicMock()
        mock_store = MagicMock()
        mock_adapter.setup.return_value = mock_store
        mock_create.return_value = mock_adapter

        result = manager.get(schema_name="tenant_acme")

        mock_create.assert_called_once_with(schema_name="tenant_acme")
        assert result is mock_store

    @patch("aviator.vector_store.VectorStoreFactory.create_adapter")
    def test_get_tenant_schema_caches_adapter(self, mock_create):
        manager = VectorStoreManager()
        mock_adapter = MagicMock()
        mock_store = MagicMock()
        mock_adapter.setup.return_value = mock_store
        mock_create.return_value = mock_adapter

        manager.get(schema_name="tenant_acme")
        manager.get(schema_name="tenant_acme")

        # Should only create once, then reuse cached
        mock_create.assert_called_once()

    @patch("aviator.vector_store.VectorStoreFactory.create_adapter")
    def test_different_tenants_get_different_adapters(self, mock_create):
        manager = VectorStoreManager()

        adapter_a = MagicMock()
        adapter_a.setup.return_value = MagicMock(name="store_a")
        adapter_b = MagicMock()
        adapter_b.setup.return_value = MagicMock(name="store_b")

        mock_create.side_effect = [adapter_a, adapter_b]

        store_a = manager.get(schema_name="tenant_a")
        store_b = manager.get(schema_name="tenant_b")

        assert store_a is not store_b
        assert mock_create.call_count == 2

    def test_remove_tenant_store(self):
        manager = VectorStoreManager()
        mock_adapter = MagicMock()
        manager._tenant_adapters["tenant_acme"] = mock_adapter

        manager.remove_tenant_store("tenant_acme")

        assert "tenant_acme" not in manager._tenant_adapters

    def test_remove_tenant_store_nonexistent_is_noop(self):
        manager = VectorStoreManager()
        # Should not raise
        manager.remove_tenant_store("nonexistent")
