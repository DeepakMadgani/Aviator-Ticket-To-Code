"""Vector store module with adapter pattern."""

import logging

from langchain_core.vectorstores import VectorStore

from aviator.settings import settings
from aviator.vector_store.adapters import VectorStoreAdapter
from aviator.vector_store.factory import VectorStoreFactory

logger = logging.getLogger(__name__)


class VectorStoreManager:
    """Manager for vector store operations.

    Supports multi-tenancy by caching one adapter per PostgreSQL schema.
    The default adapter is used when no ``schema_name`` is given.
    """

    def __init__(self) -> None:
        """Initialize the manager."""
        self._adapter: VectorStoreAdapter | None = None
        self._tenant_adapters: dict[str, VectorStoreAdapter] = {}

    def setup_vector_store(self) -> None:
        """Initialize the default vector store based on the selected backend (sync version)."""
        if self._adapter is not None:
            logger.debug("Vector store already initialized")
            return

        logger.info("Setting up vector store...")
        self._adapter = VectorStoreFactory.create_adapter()
        # Use sync initialization for backward compatibility
        self._adapter.setup()

    async def aget(self, schema_name: str | None = None) -> VectorStore:
        """Get the vector store asynchronously.

        Args:
            schema_name: PostgreSQL schema. ``None`` returns the default store.

        """
        return self.get(schema_name=schema_name)

    def get(self, schema_name: str | None = None) -> VectorStore:
        """Get the initialized vector store for the given schema.

        When ``schema_name`` is ``None`` or equals the configured default schema
        the default adapter is returned.
        For tenant schemas the adapter is created on first access and cached.

        Args:
            schema_name: PostgreSQL schema. ``None`` returns the default store.

        """
        # Default / public schema
        if schema_name is None or schema_name == settings.default_schema:
            if self._adapter is None:
                logger.warning("Vector store adapter not initialized. Creating one now...")
                self._adapter = VectorStoreFactory.create_adapter()
            return self._adapter.setup()

        # Tenant-specific schema — use cache
        if schema_name not in self._tenant_adapters:
            logger.info("Creating vector store adapter for schema '%s'...", schema_name)
            adapter = VectorStoreFactory.create_adapter(schema_name=schema_name)
            self._tenant_adapters[schema_name] = adapter

        return self._tenant_adapters[schema_name].setup()

    def get_adapter(self, schema_name: str | None = None) -> VectorStoreAdapter:
        """Get the vector store adapter (for advanced operations like deduplication).

        Args:
            schema_name: PostgreSQL schema. ``None`` returns the default adapter.

        Returns:
            The VectorStoreAdapter instance for the schema.

        """
        # Default / public schema
        if schema_name is None or schema_name == settings.default_schema:
            if self._adapter is None:
                logger.warning("Vector store adapter not initialized. Creating one now...")
                self._adapter = VectorStoreFactory.create_adapter()
            return self._adapter

        # Tenant-specific schema — use cache
        if schema_name not in self._tenant_adapters:
            logger.info("Creating vector store adapter for schema '%s'...", schema_name)
            adapter = VectorStoreFactory.create_adapter(schema_name=schema_name)
            self._tenant_adapters[schema_name] = adapter

        return self._tenant_adapters[schema_name]

    def remove_tenant_store(self, schema_name: str) -> None:
        """Remove a cached tenant adapter (e.g. after deleting a tenant)."""
        self._tenant_adapters.pop(schema_name, None)


# Singleton instance
vector_store = VectorStoreManager()


__all__ = [
    "VectorStoreAdapter",
    "VectorStoreFactory",
    "vector_store",
]
