"""Simplified Knowledge Graph for demo purposes."""

import logging
from typing import Any

logger = logging.getLogger(__name__)


class KnowledgeGraph:
    """A minimal in-memory knowledge graph for demonstration.

    Stores entities (nodes) and relationships (edges).
    Note: Each process (API server, worker) has its own separate instance.
    """

    def __init__(self) -> None:
        """Initialize empty graph."""
        self.entities: dict[str, dict[str, Any]] = {}
        self.relationships: list[dict[str, Any]] = []

    def add_entity(self, entity_id: str, name: str, entity_type: str = "Entity") -> str:
        """Add an entity to the graph.

        Args:
            entity_id: Unique ID
            name: Display name
            entity_type: Type/category

        Returns:
            Success message

        """
        self.entities[entity_id] = {"id": entity_id, "name": name, "type": entity_type}
        logger.info("Added entity: %s (%s)", name, entity_type)
        return f"Added: {name}"

    def add_relationship(self, source_id: str, target_id: str, rel_type: str) -> str:
        """Add a relationship between two entities.

        Args:
            source_id: Source entity ID
            target_id: Target entity ID
            rel_type: Relationship type

        Returns:
            Success message

        """
        if source_id not in self.entities:
            return f"Error: Entity {source_id} not found"
        if target_id not in self.entities:
            return f"Error: Entity {target_id} not found"

        self.relationships.append({"source": source_id, "target": target_id, "type": rel_type})

        source_name = self.entities[source_id]["name"]
        target_name = self.entities[target_id]["name"]
        logger.info("Added: %s -[%s]-> %s", source_name, rel_type, target_name)
        return f"Connected: {source_name} -[{rel_type}]-> {target_name}"

    def search_entities(self, query: str) -> list[dict[str, Any]]:
        """Search entities by name.

        Args:
            query: Search term

        Returns:
            List of matching entities

        """
        query_lower = query.lower()
        return [entity for entity in self.entities.values() if query_lower in entity["name"].lower()]

    def get_entity(self, entity_id: str) -> dict[str, Any] | None:
        """Get entity by ID."""
        return self.entities.get(entity_id)

    def get_relationships(self, entity_id: str) -> list[dict[str, Any]]:
        """Get all relationships for an entity."""
        return [rel for rel in self.relationships if rel["source"] == entity_id or rel["target"] == entity_id]

    def summary(self) -> str:
        """Get graph summary."""
        return f"Knowledge Graph: {len(self.entities)} entities, {len(self.relationships)} relationships"

    def get_all_data(self) -> dict[str, Any]:
        """Get all entities and relationships.

        Returns:
            Dictionary with 'entities' and 'relationships' lists

        """
        return {"entities": list(self.entities.values()), "relationships": self.relationships}

    def clear(self) -> None:
        """Clear all data."""
        self.entities.clear()
        self.relationships.clear()
        logger.info("Cleared knowledge graph")


# Global instance for the plugin
_knowledge_graph: KnowledgeGraph | None = None


def get_knowledge_graph() -> KnowledgeGraph:
    """Get the global knowledge graph instance.

    Returns:
        The singleton KnowledgeGraph instance

    """
    global _knowledge_graph  # noqa: PLW0603
    if _knowledge_graph is None:
        _knowledge_graph = KnowledgeGraph()
    return _knowledge_graph
