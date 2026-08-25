"""Embedding buffer service for accumulating and batching document chunks."""

import logging
import threading
import time

import sqlalchemy.exc
from langchain_core.documents import Document

from aviator.exceptions import EmbeddingError, EmbeddingRetryableError
from aviator.settings import settings
from aviator.vector_store import vector_store

logger = logging.getLogger(__name__)


class EmbeddingBuffer:
    """Thread-safe buffer that accumulates document chunks across Celery tasks.

    Inspired by the consumer script's ``PgVectorBatcher``, this buffer collects
    chunks from multiple embedding requests and only flushes them to the vector
    store when the batch reaches the character limit, document count limit, or
    the ``max_wait_seconds`` timeout fires.

    This avoids calling the embedding API for tiny batches when a single message's
    chunks don't fill a batch — instead it waits for the next message(s) to arrive.
    """

    def __init__(
        self,
        max_batch_chars: int | None = None,
        max_batch_size: int | None = None,
        max_wait_seconds: float | None = None,
    ) -> None:
        """Initialize the embedding buffer with configurable batch limits.

        Args:
            max_batch_chars: Maximum characters before auto-flush (default from settings)
            max_batch_size: Maximum document count before auto-flush (default from settings)
            max_wait_seconds: Maximum wait time before timer flush (default from settings)

        """
        self.max_batch_chars = max_batch_chars or settings.embedding_batch_max_chars
        self.max_batch_size = max_batch_size or settings.embedding_batch_size
        self.max_wait_seconds = (
            max_wait_seconds if max_wait_seconds is not None else settings.embedding_batch_max_wait_seconds
        )
        self.buffer: list[Document] = []
        self.buffer_hashes: list[str] = []  # Track pre-computed text hashes alongside documents
        self.buffer_chars: int = 0
        self.lock = threading.Lock()
        self.last_flush: float = time.monotonic()
        self._timer: threading.Timer | None = None
        self.current_schema_name: str | None = None  # Track schema for the buffered documents

    def add(self, docs: list[Document], schema_name: str, text_hashes: list[str] | None = None) -> list[str]:
        """Add documents to the buffer. Flushes immediately if thresholds are exceeded.

        Args:
            docs: List of documents to add
            schema_name: PostgreSQL schema name for these documents
            text_hashes: Optional pre-computed text hashes for the documents (one per document)

        Returns:
            List of stored document IDs from any flushes triggered.

        Raises:
            EmbeddingError: If text_hashes is provided but count doesn't match documents.

        """
        if text_hashes is None:
            text_hashes = [""] * len(docs)
        elif len(text_hashes) != len(docs):
            raise EmbeddingError(
                110,
                f"Text hashes count mismatch: {len(text_hashes)} hashes for {len(docs)} documents. "
                "Either provide one hash per document or None to skip.",
            )

        result: list[str] = []
        with self.lock:
            # Ensure all buffered docs belong to the same schema
            if self.buffer and self.current_schema_name and self.current_schema_name != schema_name:
                logger.warning(
                    "Schema change detected in buffer: %s -> %s. Flushing buffer for %s first.",
                    self.current_schema_name,
                    schema_name,
                    self.current_schema_name,
                )
                result.extend(self._flush_locked(self.current_schema_name))

            # Update schema for new documents
            self.current_schema_name = schema_name

            for doc, text_hash in zip(docs, text_hashes, strict=True):
                doc_chars = len(doc.page_content)
                self.buffer.append(doc)
                self.buffer_hashes.append(text_hash)
                self.buffer_chars += doc_chars

                # Flush when char or count threshold is reached
                if self.buffer_chars >= self.max_batch_chars or len(self.buffer) >= self.max_batch_size:
                    result.extend(self._flush_locked(schema_name))

            # Schedule a timer flush if we still have buffered docs
            if self.buffer:
                self._schedule_timer()

        return result

    def flush(self) -> list[str]:
        """Force-flush the buffer (called by the timer or on shutdown).

        Returns:
            List of stored document IDs.

        """
        with self.lock:
            if self.current_schema_name:
                return self._flush_locked(self.current_schema_name)
            return []

    def _flush_locked(self, schema_name: str) -> list[str]:
        """Flush the current buffer to the vector store. Must be called while holding self.lock.

        Returns:
            List of stored document IDs.

        """
        self._cancel_timer()

        if not self.buffer:
            return []

        docs_to_store = self.buffer
        hashes_to_store = self.buffer_hashes
        chars_to_store = self.buffer_chars
        self.buffer = []
        self.buffer_hashes = []
        self.buffer_chars = 0
        self.last_flush = time.monotonic()

        logger.info(
            "Flushing embedding buffer: %d chunks, %d chars (%d with pre-computed hashes)",
            len(docs_to_store),
            chars_to_store,
            sum(1 for h in hashes_to_store if h),
        )

        result: list[str] = []
        try:
            # Get adapter for this batch (has our custom methods)
            adapter = vector_store.get_adapter(schema_name=schema_name)

            # Separate docs with and without pre-computed hashes
            docs_with_hashes = []
            docs_with_hashes_list = []

            docs_without_hashes = []

            for doc, text_hash in zip(docs_to_store, hashes_to_store, strict=True):
                if text_hash:
                    # Use add_documents_with_embeddings for docs with pre-computed hashes
                    # We don't have embeddings yet, but this method will handle text_hash properly
                    docs_with_hashes.append(doc)
                    docs_with_hashes_list.append(text_hash)
                else:
                    docs_without_hashes.append(doc)

            # Strategy:
            # 1. For docs WITH pre-computed hashes: use add_documents(), then immediately UPDATE text_hash
            # 2. For docs WITHOUT hashes: use add_documents(), then populate_text_hash_from_content()
            # Insert all documents using the adapter's custom method, which generates
            # embeddings and writes all metadata columns correctly in a single INSERT.

            inserted_ids = adapter.add_documents(docs_to_store)
            result.extend(inserted_ids)

            if result:
                # Extract document IDs from the batch (for logging purposes)
                doc_ids = {
                    doc_id
                    for doc in docs_to_store
                    if (doc_id := doc.metadata.get("document_id") or doc.metadata.get("documentID"))
                }
                logger.info(
                    "Flushed %d chunks for %d document(s) to vector store",
                    len(result),
                    len(doc_ids),
                )
                logger.debug("Document IDs in batch: %s", list(doc_ids))

                if docs_with_hashes:
                    logger.debug(
                        "Setting pre-computed text_hash for %d of %d flushed documents",
                        len(docs_with_hashes),
                        len(result),
                    )
                    try:
                        # Directly set text_hash for documents with pre-computed values using adapter
                        adapter.set_text_hash_for_documents(docs_with_hashes, docs_with_hashes_list)
                    except Exception as _:
                        logger.exception("Failed to set pre-computed text_hash")
                        # Fallback to populate from content
                        try:
                            adapter.populate_text_hash_from_content()
                        except Exception as _:
                            logger.exception("Fallback populate_text_hash also failed")

                elif docs_without_hashes:
                    # For docs without pre-computed hashes, populate from content
                    logger.debug(
                        "Populating text_hash from content for %d of %d flushed documents",
                        len(docs_without_hashes),
                        len(result),
                    )
                    try:
                        adapter.populate_text_hash_from_content()
                    except Exception as _:
                        logger.exception("Failed to populate text_hash after flush")
        except sqlalchemy.exc.DataError as e:
            error_msg = f"SQL data error: {e!s}"
            raise EmbeddingError(104, error_msg) from e
        except sqlalchemy.exc.SQLAlchemyError as e:
            error_msg = f"Database error: {e!s}"
            raise EmbeddingRetryableError(100, error_msg) from e
        except Exception as e:
            error_msg = f"Vector store operation failed: {e!s}"
            raise EmbeddingRetryableError(100, error_msg) from e

        return result

    def _schedule_timer(self) -> None:
        """Schedule a timer to flush the buffer after max_wait_seconds."""
        self._cancel_timer()
        self._timer = threading.Timer(self.max_wait_seconds, self._timer_flush_wrapper)
        self._timer.daemon = True
        self._timer.start()

    def _cancel_timer(self) -> None:
        """Cancel any pending flush timer."""
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _timer_flush_wrapper(self) -> None:
        """Timer callback wrapper — flushes using stored schema_name."""
        with self.lock:
            if self.current_schema_name:
                elapsed = time.monotonic() - self.last_flush
                if self.buffer and elapsed >= self.max_wait_seconds:
                    logger.debug(
                        "Timer triggered: flushing %d buffered chunks after %.1fs idle",
                        len(self.buffer),
                        elapsed,
                    )
                    self._flush_locked(self.current_schema_name)

    def remove_by_document_id(self, document_id: str) -> int:
        """Remove all buffered documents matching the given document_id.

        Args:
            document_id: The document_id to match in document metadata

        Returns:
            Number of documents removed from the buffer

        """
        with self.lock:
            if not self.buffer:
                return 0

            initial_count = len(self.buffer)

            # Filter out documents with matching document_id
            filtered_buffer = []
            filtered_hashes = []
            removed_chars = 0

            for doc, text_hash in zip(self.buffer, self.buffer_hashes, strict=True):
                doc_document_id = doc.metadata.get("document_id") or doc.metadata.get("documentID")
                if doc_document_id == document_id:
                    # Remove this document
                    removed_chars += len(doc.page_content)
                else:
                    # Keep this document
                    filtered_buffer.append(doc)
                    filtered_hashes.append(text_hash)

            removed_count = initial_count - len(filtered_buffer)

            if removed_count > 0:
                self.buffer = filtered_buffer
                self.buffer_hashes = filtered_hashes
                self.buffer_chars -= removed_chars
                logger.info(
                    "Removed %d buffered chunks for document_id=%s (%d chars freed)",
                    removed_count,
                    document_id,
                    removed_chars,
                )

            return removed_count

    @property
    def pending_count(self) -> int:
        """Return number of documents currently buffered."""
        with self.lock:
            return len(self.buffer)

    @property
    def pending_chars(self) -> int:
        """Return total characters currently buffered."""
        with self.lock:
            return self.buffer_chars


# Module-level singleton — shared across all task invocations in the worker
embedding_buffer = EmbeddingBuffer()
