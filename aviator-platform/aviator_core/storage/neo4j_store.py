"""Neo4j storage layer for graph-based queries and traversals.

This is an optional storage backend that complements SQLite.
While SQLite is the primary store, Neo4j enables:
- Complex graph traversals (3+ hop queries)
- Architectural pattern detection
- Microservice dependency mapping
- Call chain visualization

Install with: pip install "aviator-platform[neo4j]"
Configure with: NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD
"""

from __future__ import annotations

import os
from typing import Iterable, Optional
from pathlib import Path

from aviator_core.models import Edge, EdgeKind, FileRecord, Symbol, SymbolKind


class Neo4jStore:
    """Optional graph database backend using Neo4j."""
    
    def __init__(self, workspace_path: str, uri: Optional[str] = None, user: Optional[str] = None, password: Optional[str] = None):
        """Initialize Neo4j connection.
        
        Args:
            uri: Neo4j connection URI (default: env NEO4J_URI or bolt://localhost:7687)
            user: Neo4j username (default: env NEO4J_USER or neo4j)
            password: Neo4j password (default: env NEO4J_PASSWORD)
        """
        try:
            from neo4j import GraphDatabase
        except ImportError:
            raise ImportError(
                "Neo4j support requires the neo4j driver. "
                'Install with: pip install "aviator-platform[neo4j]"'
            )
        
        self.uri = uri or os.getenv("NEO4J_URI", "bolt://localhost:7687")
        self.user = user or os.getenv("NEO4J_USER", "neo4j")
        self.password = password or os.getenv("NEO4J_PASSWORD")
        
        if not self.password:
            raise ValueError(
                "Neo4j password required. Set NEO4J_PASSWORD environment variable "
                "or pass password parameter."
            )
        
        self.driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
        self.workspace_path = workspace_path
        self._create_constraints()
    
    def _create_constraints(self) -> None:
        """Create uniqueness constraints and indexes."""
        with self.driver.session() as session:
            # Drop old single-property constraints that conflict with multi-workspace
            old_constraints = [
                "DROP CONSTRAINT symbol_id_unique IF EXISTS",
                "DROP CONSTRAINT file_path_unique IF EXISTS",
            ]
            for old in old_constraints:
                try:
                    session.run(old)
                except Exception:
                    pass
            
            # Composite constraints for multi-workspace isolation
            constraints = [
                "CREATE CONSTRAINT symbol_ws_id_unique IF NOT EXISTS FOR (s:Symbol) REQUIRE (s.workspace, s.id) IS UNIQUE",
                "CREATE CONSTRAINT file_ws_path_unique IF NOT EXISTS FOR (f:File) REQUIRE (f.workspace, f.path) IS UNIQUE",
            ]
            
            # Indexes for performance
            indexes = [
                "CREATE INDEX symbol_name IF NOT EXISTS FOR (s:Symbol) ON (s.name)",
                "CREATE INDEX symbol_qname IF NOT EXISTS FOR (s:Symbol) ON (s.qualified_name)",
                "CREATE INDEX symbol_kind IF NOT EXISTS FOR (s:Symbol) ON (s.kind)",
                "CREATE INDEX symbol_stereotype IF NOT EXISTS FOR (s:Symbol) ON (s.spring_stereotype)",
                "CREATE INDEX file_package IF NOT EXISTS FOR (f:File) ON (f.package)",
                "CREATE INDEX symbol_workspace IF NOT EXISTS FOR (s:Symbol) ON (s.workspace)",
                "CREATE INDEX file_workspace IF NOT EXISTS FOR (f:File) ON (f.workspace)",
            ]
            
            for constraint in constraints:
                try:
                    session.run(constraint)
                except Exception:
                    pass  # Constraint may already exist
            
            for index in indexes:
                try:
                    session.run(index)
                except Exception:
                    pass  # Index may already exist
    
    def upsert_file(self, file: FileRecord) -> None:
        """Store or update a file node.
        
        Args:
            file: FileRecord to store
        """
        with self.driver.session() as session:
            session.run(
                """
                MERGE (f:File {workspace: $workspace, path: $path})
                SET f.language = $language,
                    f.package = $package,
                    f.sha256 = $sha256,
                    f.size_bytes = $size_bytes,
                    f.parse_ok = $parse_ok,
                    f.parse_error = $parse_error
                """,
                path=file.path,
                workspace=str(self.workspace_path),
                language=file.language,
                package=file.package,
                sha256=file.sha256,
                size_bytes=file.size_bytes,
                parse_ok=file.parse_ok,
                parse_error=file.parse_error,
            )
    
    _BATCH_SIZE = 500

    def insert_symbols(self, symbols: Iterable[Symbol]) -> None:
        """Store symbols as nodes with relationships to parent files.
        
        Args:
            symbols: Symbols to store
        """
        batch: list[dict] = []
        parent_batch: list[dict] = []

        def _flush(sess) -> None:
            if batch:
                sess.run(
                    """
                    UNWIND $rows AS r
                    MERGE (s:Symbol {workspace: $workspace, id: r.id})
                    SET s.kind = r.kind,
                        s.name = r.name,
                        s.qualified_name = r.qualified_name,
                        s.package = r.package,
                        s.path = r.path,
                        s.start_line = r.start_line,
                        s.end_line = r.end_line,
                        s.signature = r.signature,
                        s.return_type = r.return_type,
                        s.modifiers = r.modifiers,
                        s.annotations = r.annotations,
                        s.spring_stereotype = r.spring_stereotype,
                        s.spring_endpoints = r.spring_endpoints,
                        s.is_feign_client = r.is_feign_client,
                        s.feign_service_name = r.feign_service_name
                    WITH s, r
                    MERGE (f:File {workspace: $workspace, path: r.path})
                    MERGE (f)-[:CONTAINS]->(s)
                    """,
                    rows=batch,
                    workspace=str(self.workspace_path),
                )
                batch.clear()
            if parent_batch:
                sess.run(
                    """
                    UNWIND $rows AS r
                    MATCH (parent:Symbol {workspace: $workspace, id: r.parent_id})
                    MATCH (child:Symbol {workspace: $workspace, id: r.child_id})
                    MERGE (parent)-[:CONTAINS]->(child)
                    """,
                    rows=parent_batch,
                    workspace=str(self.workspace_path),
                )
                parent_batch.clear()

        with self.driver.session() as session:
            for symbol in symbols:
                batch.append({
                    "id": symbol.id,
                    "kind": symbol.kind.value,
                    "name": symbol.name,
                    "qualified_name": symbol.qualified_name,
                    "package": symbol.package,
                    "path": symbol.location.path,
                    "start_line": symbol.location.start_line,
                    "end_line": symbol.location.end_line,
                    "signature": symbol.signature,
                    "return_type": symbol.return_type,
                    "modifiers": symbol.modifiers,
                    "annotations": symbol.annotations,
                    "spring_stereotype": symbol.spring_stereotype,
                    "spring_endpoints": symbol.spring_endpoints,
                    "is_feign_client": symbol.is_feign_client,
                    "feign_service_name": symbol.feign_service_name,
                })
                if symbol.parent_id:
                    parent_batch.append({"parent_id": symbol.parent_id, "child_id": symbol.id})
                if len(batch) >= self._BATCH_SIZE:
                    _flush(session)
            _flush(session)
    
    def insert_edges(self, edges: Iterable[Edge]) -> None:
        """Store edges as relationships between symbols.
        
        Args:
            edges: Edges to store
        """
        # Separate resolved (dst_id present) from unresolved edges
        resolved: list[dict] = []
        unresolved: list[dict] = []

        for edge in edges:
            if edge.dst_id:
                resolved.append({
                    "src_id": edge.src_id,
                    "dst_id": edge.dst_id,
                    "dst_name": edge.dst_name,
                    "rel_type": edge.kind.value.upper(),
                })
            else:
                unresolved.append({
                    "src_id": edge.src_id,
                    "dst_name": edge.dst_name,
                    "kind": edge.kind.value,
                })

        with self.driver.session() as session:
            # Batch resolved edges grouped by rel_type
            from itertools import groupby
            resolved_sorted = sorted(resolved, key=lambda e: e["rel_type"])
            for rel_type, group in groupby(resolved_sorted, key=lambda e: e["rel_type"]):
                rows = list(group)
                for i in range(0, len(rows), self._BATCH_SIZE):
                    chunk = rows[i:i + self._BATCH_SIZE]
                    session.run(
                        f"""
                        UNWIND $rows AS r
                        MATCH (src:Symbol {{workspace: $workspace, id: r.src_id}})
                        MATCH (dst:Symbol {{workspace: $workspace, id: r.dst_id}})
                        MERGE (src)-[:{rel_type} {{dst_name: r.dst_name}}]->(dst)
                        """,
                        rows=chunk,
                        workspace=str(self.workspace_path),
                    )

            # Batch unresolved edges
            # Design decision: External nodes are intentionally NOT workspace-scoped.
            # They represent third-party/stdlib references (e.g., java.util.List)
            # that are semantically identical across workspaces. Only the source
            # Symbol MATCH is scoped, so relationships are workspace-bound but the
            # target External node is shared globally.
            for i in range(0, len(unresolved), self._BATCH_SIZE):
                chunk = unresolved[i:i + self._BATCH_SIZE]
                session.run(
                    """
                    UNWIND $rows AS r
                    MERGE (ext:External {name: r.dst_name})
                    WITH ext, r
                    MATCH (src:Symbol {workspace: $workspace, id: r.src_id})
                    MERGE (src)-[:REFERENCES {kind: r.kind}]->(ext)
                    """,
                    rows=chunk,
                        workspace=str(self.workspace_path),
                )
    
    def find_call_chain(self, start_symbol_id: str, max_depth: int = 3) -> list[dict]:
        """Find call chains starting from a symbol.
        
        Args:
            start_symbol_id: ID of starting symbol
            max_depth: Maximum traversal depth
            
        Returns:
            List of paths in the call graph
        """
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH path = (start:Symbol {workspace: $workspace, id: $start_id})-[:CALLS*1..%d]->(end:Symbol {workspace: $workspace})
                RETURN [node in nodes(path) | {
                    id: node.id,
                    name: node.name,
                    path: node.path,
                    kind: node.kind
                }] as chain
                LIMIT 50
                """ % max_depth,
                start_id=start_symbol_id,
                workspace=str(self.workspace_path),
            )
            return [record["chain"] for record in result]
    
    def find_spring_layer_traversal(
        self,
        entry_point: str,
        max_depth: int = 3
    ) -> list[dict]:
        """Find Spring architectural layers starting from an entry point.
        
        Traverses: Controller → Service → Repository
        
        Args:
            entry_point: Starting symbol ID (typically a Controller)
            max_depth: Maximum depth to traverse
            
        Returns:
            List of layer traversal paths
        """
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH path = (start:Symbol {workspace: $workspace, id: $entry})-[:CALLS*1..%d]->(end:Symbol {workspace: $workspace})
                WHERE start.spring_stereotype IN ['Controller', 'RestController']
                AND end.spring_stereotype IN ['Service', 'Repository']
                RETURN [node in nodes(path) | {
                    id: node.id,
                    name: node.name,
                    stereotype: node.spring_stereotype,
                    path: node.path
                }] as layers
                LIMIT 30
                """ % max_depth,
                entry=entry_point,
                workspace=str(self.workspace_path),
            )
            return [record["layers"] for record in result]
    
    def find_microservice_dependencies(self, service_name: str) -> list[dict]:
        """Find all Feign client dependencies for a microservice.
        
        Args:
            service_name: Name of the microservice
            
        Returns:
            List of Feign client interfaces and their target services
        """
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (f:File {workspace: $workspace, package: $service})-[:CONTAINS]->(s:Symbol {workspace: $workspace})
                WHERE s.is_feign_client = true
                RETURN s.name as client_name,
                       s.feign_service_name as target_service,
                       s.path as path
                LIMIT 50
                """,
                service=service_name,
                workspace=str(self.workspace_path),
            )
            return [dict(record) for record in result]
    
    def clear_repository(self, repo_path: str) -> None:
        """Delete all nodes and relationships for a repository.
        
        Args:
            repo_path: Repository path prefix to delete
        """
        with self.driver.session() as session:
            # Delete all symbols and files matching the repo path — scoped to this workspace only
            session.run(
                """
                MATCH (n {workspace: $workspace})
                WHERE n.path STARTS WITH $prefix
                DETACH DELETE n
                """,
                prefix=repo_path,
                workspace=str(self.workspace_path),
            )
    
    
    def query(self, query_string: str, parameters: Optional[dict] = None) -> list[dict]:
        if parameters is None:
            parameters = {}
        if hasattr(self, 'workspace_path') and self.workspace_path:
            parameters['workspace'] = str(self.workspace_path)
            if '$workspace' not in query_string:
                raise ValueError('SECURITY REJECTION: All Neo4j queries must be scoped with {workspace: $workspace}')
        with self.driver.session() as session:
            result = session.run(query_string, parameters)
            return [record.data() for record in result]

    def close(self) -> None:
        """Close the Neo4j driver connection."""
        self.driver.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


# Example usage
if __name__ == "__main__":
    # Initialize store
    store = Neo4jStore()
    
    # Example: Find call chains
    print("Call chains from main method:")
    chains = store.find_call_chain("some-symbol-id", max_depth=2)
    for chain in chains:
        print(" → ".join([node["name"] for node in chain]))
    
    # Example: Find Spring layers
    print("\nSpring layer traversal:")
    layers = store.find_spring_layer_traversal("controller-symbol-id")
    for layer in layers:
        print(" → ".join([f"{node['stereotype']}:{node['name']}" for node in layer]))
    
    store.close()
