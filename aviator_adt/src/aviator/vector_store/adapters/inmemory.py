"""In-memory vector store adapter."""

import logging
from typing import Any

from langchain_core.vectorstores import InMemoryVectorStore, VectorStore

from aviator.vector_store.adapters.base import VectorStoreAdapter

logger = logging.getLogger(__name__)


class InMemoryVectorStoreAdapter(VectorStoreAdapter):
    """Adapter for in-memory vector store."""

    async def asetup(self) -> None:
        """Set up the in-memory vector store asynchronously."""
        logger.info("Setting up InMemory vector store...")
        self._store = InMemoryVectorStore(embedding=self.embeddings)
        logger.info("InMemory vector store initialized.")

    def setup(self) -> VectorStore:
        """Set up and get the in-memory vector store synchronously."""
        if self._store is None:
            logger.info("Initializing InMemory vector store synchronously...")
            self._store = InMemoryVectorStore(embedding=self.embeddings)
        return self._store

    async def asimilarity_search_with_relevance_scores(
        self,
        query: str,
        k: int = 10,
        metadata_filter: dict[str, Any] | None = None,
        score_threshold: float | None = None,
    ) -> list[tuple[object, float]]:
        """Search the in-memory vector store by similarity.

        Args:
            query: Query string to embed and search.
            k: Top-k results to return.
            metadata_filter: MongoDB-style metadata filter dict.
            score_threshold: Optional minimum similarity score.

        Returns:
            List of (Document, score) tuples.

        """
        store = self.setup()
        fetch_k = k
        raw_results = await store.asimilarity_search_with_relevance_scores(
            query, k=fetch_k, metadata_filter=metadata_filter
        )

        results = []
        result_count = 0
        for doc, score in raw_results:
            if score_threshold is not None and score < score_threshold:
                continue

            results.append((doc, score))
            result_count += 1

            if result_count >= k:
                break

        return results

    async def aget_distinct_document_ids(self, metadata_filter: dict[str, Any] | None = None) -> list[str]:  # noqa: ARG002
        """Return distinct document IDs from the in-memory store.

        Args:
            metadata_filter: MongoDB-style metadata filter dict (ignored for in-memory).

        Returns:
            List of unique document ID strings.

        """
        store = self.setup()
        doc_ids: set[str] = set()
        for doc in store.store.values():
            metadata = doc.get("metadata", {}) if isinstance(doc, dict) else getattr(doc, "metadata", {})
            document_id = metadata.get("document_id")
            if document_id is not None:
                doc_ids.add(str(document_id))
        return list(doc_ids)

    def add_documents(self, documents: list[Any]) -> list[str]:
        """Add documents to the in-memory vector store and return their IDs."""
        from langchain_core.documents import Document

        store = self.setup()
        doc_objs = []
        ids = []
        for doc in documents:
            # Always convert to Document for downstream compatibility
            if isinstance(doc, Document):
                doc_obj = doc
            elif hasattr(doc, "page_content") and hasattr(doc, "metadata"):
                doc_obj = Document(page_content=doc.page_content, metadata=doc.metadata)
            else:
                doc_obj = Document(**doc)
            doc_objs.append(doc_obj)
            doc_id = doc_obj.metadata.get("document_id") or doc_obj.metadata.get("id") or str(len(store.store))
            ids.append(str(doc_id))
        store.add_documents(doc_objs)
        return ids
