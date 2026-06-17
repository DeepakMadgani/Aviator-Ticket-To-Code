"""Vector embeddings for semantic code search.

This is an optional 5% fallback when keyword + symbol + graph search 
don't find relevant results. Provides:
- Semantic similarity search
- Natural language queries
- Cross-language code search

Install with: pip install "aviator-platform[qdrant]"
Configure with: QDRANT_URL, QDRANT_API_KEY (optional for cloud)
"""

from __future__ import annotations

import hashlib
import os
from typing import Optional
from pathlib import Path

from aviator_core.models import Symbol, SourceLocation


class VectorStore:
    """Optional vector database for semantic search using Qdrant."""
    
    def __init__(
        self,
        collection_name: str = "aviator_symbols",
        qdrant_url: Optional[str] = None,
        qdrant_api_key: Optional[str] = None,
        embedding_model: str = "all-MiniLM-L6-v2"
    ):
        """Initialize Qdrant vector store.
        
        Args:
            collection_name: Name of the Qdrant collection
            qdrant_url: Qdrant connection URL (default: env QDRANT_URL or localhost:6333)
            qdrant_api_key: Qdrant API key for cloud (default: env QDRANT_API_KEY)
            embedding_model: Sentence transformer model for embeddings
        """
        try:
            from qdrant_client import QdrantClient
            from qdrant_client.models import Distance, VectorParams, PointStruct
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise ImportError(
                "Vector search requires qdrant-client and sentence-transformers. "
                'Install with: pip install "aviator-platform[qdrant]"'
            )
        
        self.collection_name = collection_name
        self.qdrant_url = qdrant_url or os.getenv("QDRANT_URL", "localhost")
        self.qdrant_api_key = qdrant_api_key or os.getenv("QDRANT_API_KEY")
        
        # Initialize Qdrant client
        if self.qdrant_api_key:
            self.client = QdrantClient(url=self.qdrant_url, api_key=self.qdrant_api_key)
        else:
            self.client = QdrantClient(host=self.qdrant_url, port=6333)
        
        # Initialize embedding model
        print(f"Loading embedding model: {embedding_model}...")
        self.encoder = SentenceTransformer(embedding_model)
        self.vector_size = self.encoder.get_sentence_embedding_dimension()
        
        # Create collection if it doesn't exist
        self._create_collection()
    
    def _create_collection(self) -> None:
        """Create Qdrant collection with proper schema."""
        from qdrant_client.models import Distance, VectorParams
        
        collections = self.client.get_collections().collections
        collection_names = [c.name for c in collections]
        
        if self.collection_name not in collection_names:
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(
                    size=self.vector_size,
                    distance=Distance.COSINE
                )
            )
            print(f"Created collection: {self.collection_name}")
    
    def index_symbol(self, symbol: Symbol) -> None:
        """Index a single symbol with its embedding.
        
        Args:
            symbol: Symbol to index
        """
        # Create searchable text representation
        text = self._symbol_to_text(symbol)
        
        # Generate embedding
        embedding = self.encoder.encode(text).tolist()
        
        # Create point
        from qdrant_client.models import PointStruct
        
        point = PointStruct(
            id=self._symbol_hash(symbol.id),
            vector=embedding,
            payload={
                "id": symbol.id,
                "kind": symbol.kind.value,
                "name": symbol.name,
                "qualified_name": symbol.qualified_name,
                "package": symbol.package,
                "path": symbol.location.path,
                "start_line": symbol.location.start_line,
                "signature": symbol.signature,
                "annotations": symbol.annotations,
                "spring_stereotype": symbol.spring_stereotype,
                "text": text,
            }
        )
        
        # Upsert to Qdrant
        self.client.upsert(
            collection_name=self.collection_name,
            points=[point]
        )
    
    def index_symbols_batch(self, symbols: list[Symbol], batch_size: int = 100) -> int:
        """Index multiple symbols in batches.
        
        Args:
            symbols: List of symbols to index
            batch_size: Number of symbols per batch
            
        Returns:
            Number of symbols indexed
        """
        from qdrant_client.models import PointStruct
        
        total = 0
        batch = []
        
        for symbol in symbols:
            text = self._symbol_to_text(symbol)
            embedding = self.encoder.encode(text).tolist()
            
            point = PointStruct(
                id=self._symbol_hash(symbol.id),
                vector=embedding,
                payload={
                    "id": symbol.id,
                    "kind": symbol.kind.value,
                    "name": symbol.name,
                    "qualified_name": symbol.qualified_name,
                    "package": symbol.package,
                    "path": symbol.location.path,
                    "start_line": symbol.location.start_line,
                    "signature": symbol.signature,
                    "annotations": symbol.annotations,
                    "spring_stereotype": symbol.spring_stereotype,
                    "text": text,
                }
            )
            batch.append(point)
            
            if len(batch) >= batch_size:
                self.client.upsert(
                    collection_name=self.collection_name,
                    points=batch
                )
                total += len(batch)
                batch = []
                print(f"Indexed {total} symbols...")
        
        # Upload remaining batch
        if batch:
            self.client.upsert(
                collection_name=self.collection_name,
                points=batch
            )
            total += len(batch)
        
        return total
    
    def search(
        self,
        query: str,
        limit: int = 10,
        score_threshold: float = 0.5,
        filter_dict: Optional[dict] = None
    ) -> list[dict]:
        """Semantic search for symbols matching query.
        
        Args:
            query: Natural language query
            limit: Maximum results to return
            score_threshold: Minimum similarity score (0.0 to 1.0)
            filter_dict: Optional Qdrant filter conditions
            
        Returns:
            List of matching symbols with scores
        """
        # Generate query embedding
        query_vector = self.encoder.encode(query).tolist()
        
        # Search Qdrant
        results = self.client.search(
            collection_name=self.collection_name,
            query_vector=query_vector,
            limit=limit,
            score_threshold=score_threshold,
            query_filter=filter_dict
        )
        
        # Format results
        matches = []
        for hit in results:
            matches.append({
                "id": hit.payload["id"],
                "name": hit.payload["name"],
                "qualified_name": hit.payload["qualified_name"],
                "path": hit.payload["path"],
                "start_line": hit.payload["start_line"],
                "kind": hit.payload["kind"],
                "spring_stereotype": hit.payload.get("spring_stereotype"),
                "score": hit.score,
                "text": hit.payload["text"]
            })
        
        return matches
    
    def search_by_spring_layer(
        self,
        query: str,
        stereotype: str,
        limit: int = 10
    ) -> list[dict]:
        """Search within a specific Spring layer.
        
        Args:
            query: Natural language query
            stereotype: Spring stereotype (Controller, Service, Repository, etc.)
            limit: Maximum results
            
        Returns:
            Matching symbols from the specified layer
        """
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        
        filter_dict = Filter(
            must=[
                FieldCondition(
                    key="spring_stereotype",
                    match=MatchValue(value=stereotype)
                )
            ]
        )
        
        return self.search(query, limit=limit, filter_dict=filter_dict)
    
    def delete_by_path(self, file_path: str) -> None:
        """Delete all vectors for symbols in a file.
        
        Args:
            file_path: Repo-relative file path
        """
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        
        self.client.delete(
            collection_name=self.collection_name,
            points_selector=Filter(
                must=[
                    FieldCondition(
                        key="path",
                        match=MatchValue(value=file_path)
                    )
                ]
            )
        )
    
    def clear_collection(self) -> None:
        """Delete all vectors in the collection."""
        self.client.delete_collection(self.collection_name)
        self._create_collection()
    
    def _symbol_to_text(self, symbol: Symbol) -> str:
        """Convert symbol to searchable text representation.
        
        Args:
            symbol: Symbol to convert
            
        Returns:
            Text representation for embedding
        """
        parts = [
            symbol.qualified_name,
            symbol.name,
            symbol.kind.value,
        ]
        
        if symbol.signature:
            parts.append(symbol.signature)
        
        if symbol.annotations:
            parts.extend(symbol.annotations)
        
        if symbol.spring_stereotype:
            parts.append(f"Spring {symbol.spring_stereotype}")
        
        if symbol.doc:
            parts.append(symbol.doc)
        
        return " ".join(parts)
    
    def _symbol_hash(self, symbol_id: str) -> int:
        """Convert symbol ID to integer hash for Qdrant.
        
        Args:
            symbol_id: Symbol ID string
            
        Returns:
            Integer hash
        """
        return int(hashlib.sha1(symbol_id.encode()).hexdigest()[:16], 16)


class VectorLocalizer:
    """Semantic localization using vector embeddings (5% fallback weight)."""
    
    def __init__(self, repo_path: str, vector_store: Optional[VectorStore] = None):
        """Initialize vector localizer.
        
        Args:
            repo_path: Repository path
            vector_store: Optional pre-initialized VectorStore
        """
        self.repo_path = Path(repo_path)
        self.vector_store = vector_store
        
        # Lazy initialization - only create if needed
        if vector_store is None:
            self._vector_store_initialized = False
        else:
            self._vector_store_initialized = True
    
    def localize(self, ticket_description: str, limit: int = 10) -> list[dict]:
        """Semantic search for relevant files.
        
        Args:
            ticket_description: Natural language ticket description
            limit: Maximum results
            
        Returns:
            List of file candidates with semantic scores
        """
        if not self._vector_store_initialized:
            try:
                self.vector_store = VectorStore()
                self._vector_store_initialized = True
            except ImportError:
                # Vector search not available
                return []
        
        try:
            results = self.vector_store.search(ticket_description, limit=limit)
            
            # Group by file and aggregate scores
            from collections import defaultdict
            file_scores = defaultdict(lambda: {'score': 0.0, 'symbols': []})
            
            for result in results:
                path = result['path']
                file_scores[path]['score'] = max(file_scores[path]['score'], result['score'])
                file_scores[path]['symbols'].append(result['name'])
            
            # Convert to candidates
            candidates = []
            for path, data in file_scores.items():
                candidates.append({
                    'path': path,
                    'score': round(data['score'], 2),
                    'reason': f"Semantic match: {', '.join(data['symbols'][:3])}"
                })
            
            # Sort by score
            candidates.sort(key=lambda x: x['score'], reverse=True)
            return candidates
        
        except Exception as e:
            print(f"Warning: Vector search failed: {e}")
            return []


# Example usage
if __name__ == "__main__":
    # Initialize vector store
    store = VectorStore()
    
    # Search examples
    print("Searching for: validation logic for transmittals")
    results = store.search("validation logic for transmittals", limit=5)
    
    for i, result in enumerate(results, 1):
        print(f"{i}. {result['qualified_name']} (score: {result['score']:.2f})")
        print(f"   Path: {result['path']}:{result['start_line']}")
        print(f"   Type: {result['kind']}")
        if result['spring_stereotype']:
            print(f"   Spring: {result['spring_stereotype']}")
        print()
