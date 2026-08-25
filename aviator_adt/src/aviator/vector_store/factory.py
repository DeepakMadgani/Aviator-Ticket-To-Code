"""Factory for creating vector store adapters."""

from aviator.services.embeddings import EmbeddingsRegistry
from aviator.settings import settings
from aviator.vector_store.adapters import (
    InMemoryVectorStoreAdapter,
    PGVectorStoreAdapter,
    VectorStoreAdapter,
)


class VectorStoreFactory:
    """Factory for creating vector store adapters."""

    @staticmethod
    def create_adapter(schema_name: str | None = None) -> VectorStoreAdapter:
        """Create a vector store adapter based on configuration.

        Args:
            schema_name: PostgreSQL schema to scope the store to.
                         Defaults to the configured default schema. Only used for pgvectorstore.

        """
        resolved_schema = schema_name or settings.default_schema
        store_type = settings.vector_store
        embeddings = EmbeddingsRegistry.get_embeddings()

        match store_type:
            case "memory":
                return InMemoryVectorStoreAdapter(embeddings)

            case "pgvectorstore":
                return PGVectorStoreAdapter(embeddings, schema_name=resolved_schema)

            case _:
                msg = f"Unsupported vector store type: {store_type}"
                raise ValueError(msg)
