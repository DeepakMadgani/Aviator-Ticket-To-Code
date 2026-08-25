"""Utility functions for generating deterministic hashes of document chunks.

This module provides functions to create cache keys for chunk embeddings,
enabling deduplication of embeddings across documents and requests.
"""

import hashlib
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def generate_chunk_hash(content: str) -> str:
    """Generate deterministic SHA256 hash of chunk content.

    This hash is used as the primary key for caching chunk embeddings.
    By hashing the content itself (not document metadata), we achieve
    deduplication across documents—a chunk with identical text in
    different documents will reuse the same cached embedding.

    Args:
        content: The text content of the chunk to hash.

    Returns:
        SHA256 hex digest of the content.

    Example:
        >>> hash1 = generate_chunk_hash("Some text content")
        >>> hash2 = generate_chunk_hash("Some text content")
        >>> hash1 == hash2
        True

    """
    if not content:
        logger.warning("Empty content provided to generate_chunk_hash")
        return hashlib.sha256(b"").hexdigest()

    # Normalize whitespace for consistent hashing across content variations
    normalized = " ".join(content.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def generate_cache_key(
    chunk_content: str,
    prefix: str = "embedding_chunk",
) -> str:
    """Generate a cache key for chunk embeddings.

    Combines a prefix with the chunk hash to create a namespaced cache key.
    This allows embeddings cache to coexist with other caches using the
    same backend (e.g., Redis).

    Args:
        chunk_content: The text content of the chunk.
        prefix: Prefix for the cache key (default: "embedding_chunk").

    Returns:
        Cache key suitable for use with CacheAdapter.

    Example:
        >>> key = generate_cache_key("Some content", "embedding")
        >>> key.startswith("embedding:")
        True

    """
    chunk_hash = generate_chunk_hash(chunk_content)
    return f"{prefix}:{chunk_hash}"


def generate_chunk_metadata_hash(metadata: dict[str, Any]) -> str:
    """Generate deterministic hash of chunk metadata.

    Used to verify that chunk metadata hasn't changed when retrieving
    cached embeddings. This helps detect if the same text appears in
    different document versions or with different metadata attributes.

    Args:
        metadata: Dictionary of chunk metadata (document_id, workspace_id, etc.)

    Returns:
        SHA256 hex digest of sorted metadata JSON.

    Example:
        >>> m1 = {"doc_id": "123", "workspace": "ws1"}
        >>> m2 = {"workspace": "ws1", "doc_id": "123"}
        >>> generate_chunk_metadata_hash(m1) == generate_chunk_metadata_hash(m2)
        True  # Order doesn't matter

    """
    try:
        # Sort keys for deterministic JSON serialization
        sorted_metadata = json.dumps(
            metadata,
            sort_keys=True,
            default=str,  # Handle non-serializable types
            ensure_ascii=True,
        )
        return hashlib.sha256(sorted_metadata.encode("utf-8")).hexdigest()
    except Exception as e:
        logger.error("Failed to hash metadata %s: %s", metadata, e)
        # Return empty hash on error—caching will be conservative
        return hashlib.sha256(b"").hexdigest()


__all__ = [
    "generate_cache_key",
    "generate_chunk_hash",
    "generate_chunk_metadata_hash",
]
