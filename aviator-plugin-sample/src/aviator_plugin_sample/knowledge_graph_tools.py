"""Simplified Knowledge Graph Tools for demo purposes."""

import json
import logging

from langchain_core.tools import tool

from .knowledge_graph import get_knowledge_graph

logger = logging.getLogger(__name__)


@tool
def kg_add_entity(entity_id: str, name: str, entity_type: str = "Entity") -> str:
    """Add an entity to the knowledge graph.

    Use this to store people, companies, products, or concepts.

    Args:
        entity_id: Unique ID (e.g., "person_john")
        name: Display name (e.g., "John Smith")
        entity_type: Type (e.g., "Person", "Company")

    Returns:
        Success message

    """
    kg = get_knowledge_graph()
    return kg.add_entity(entity_id, name, entity_type)


@tool
def kg_add_relationship(source_id: str, target_id: str, rel_type: str) -> str:
    """Connect two entities with a relationship.

    Both entities must exist first. Use this to show connections like
    "works_for", "knows", "located_in", etc.

    Args:
        source_id: Source entity ID
        target_id: Target entity ID
        rel_type: Relationship type (e.g., "works_for", "knows")

    Returns:
        Success message

    """
    kg = get_knowledge_graph()
    return kg.add_relationship(source_id, target_id, rel_type)


@tool
def kg_search(query: str) -> str:
    """Search for entities by name.

    Args:
        query: Search term

    Returns:
        JSON list of matching entities

    """
    kg = get_knowledge_graph()
    results = kg.search_entities(query)

    if not results:
        return f"No entities found matching '{query}'"

    return json.dumps(results, indent=2)


@tool
def kg_summary() -> str:
    """Get a summary of the knowledge graph.

    Returns:
        Summary with entity and relationship counts

    """
    kg = get_knowledge_graph()
    return kg.summary()


@tool
def kg_display_all() -> str:
    """Display all entities and relationships in the knowledge graph.

    Use this when the user wants to see everything stored in the knowledge graph,
    or wants a complete overview of all entities and their connections.

    Returns:
        JSON string with all entities and relationships

    """
    kg = get_knowledge_graph()
    data = kg.get_all_data()

    if not data["entities"] and not data["relationships"]:
        return "The knowledge graph is empty."

    # Format the output nicely
    output = {"summary": kg.summary(), "entities": data["entities"], "relationships": data["relationships"]}

    return json.dumps(output, indent=2)
