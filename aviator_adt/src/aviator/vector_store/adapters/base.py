"""Base adapter for vector stores."""

from abc import ABC, abstractmethod
from typing import Any

from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import VectorStore


class VectorStoreAdapter(ABC):
    """Abstract base adapter for vector stores."""

    def __init__(self, embeddings: Embeddings) -> None:
        """Initialize the adapter with embeddings."""
        self.embeddings = embeddings
        self._store: VectorStore | None = None

    @abstractmethod
    async def asetup(self) -> None:
        """Set up the vector store asynchronously."""

    @abstractmethod
    def setup(self) -> VectorStore:
        """Set up and get the vector store synchronously."""

    @abstractmethod
    async def aget_distinct_document_ids(self, metadata_filter: dict[str, Any] | None = None) -> list[str]:
        """Return distinct document IDs matching the given filter.

        Args:
            metadata_filter: MongoDB-style metadata filter dict, or ``None`` for all documents.

        Returns:
            List of unique document ID strings.

        """

    def get_store(self) -> VectorStore:
        """Get the initialized vector store."""
        if self._store is None:
            msg = "Vector store not initialized. Call asetup() first or use setup()."
            raise RuntimeError(msg)
        return self._store
