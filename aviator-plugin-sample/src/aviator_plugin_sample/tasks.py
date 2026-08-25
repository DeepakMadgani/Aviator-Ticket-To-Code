"""Celery tasks for the sample plugin.

This module defines Celery tasks that run in the worker process.
These tasks are automatically imported when the plugin loads.
"""

import logging
from typing import Any

from aviator.models import EmbeddingRequest
from celery import Celery

from .knowledge_graph import get_knowledge_graph

logger = logging.getLogger(__name__)

# Get the Celery app instance
# Note: In Aviator, the Celery app is already configured
# We just need to register our tasks with it
app = Celery("aviator-worker")


@app.task(name="sample.process_embedding")
def process_embedding_task(embedding_data: dict[str, Any]) -> None:
    """Process an embedding request in the worker.

    This task is called when embeddings are processed.
    It extracts knowledge from the embedded content and updates the knowledge graph.

    Args:
        embedding_data: Dictionary containing embedding request data

    """
    try:
        logger.info("[CELERY WORKER] Processing embedding task with data: %s", embedding_data)

        # Create EmbeddingRequest from the data
        request = EmbeddingRequest(**embedding_data)

        # Process the document and add to knowledge graph
        metadata = request.metadata or {}
        doc_id = metadata.get("documentID", f"doc_{id(request)}")
        doc_name = metadata.get("name", "Unknown Document")

        # Add document to knowledge graph
        kg = get_knowledge_graph()
        entity_id = f"doc_{doc_id}"
        kg.add_entity(entity_id, doc_name, "Document")

        logger.info("[CELERY WORKER] Added document to knowledge graph: %s", doc_name)
        logger.info("[CELERY WORKER] Embedding task completed successfully")

    except Exception:
        logger.exception("[CELERY WORKER] Error in embedding task")
        raise


@app.task(name="sample.update_knowledge_graph")
def update_knowledge_graph_task(
    entity_id: str,
    entity_name: str,
    entity_type: str = "Entity",
    _text_content: str = "",
    _metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Update the knowledge graph with new entity.

    Args:
        entity_id: Unique identifier for the entity
        entity_name: Display name for the entity
        entity_type: Type of entity (e.g., Document, Technology)
        text_content: Text content (optional, for future use)
        metadata: Additional metadata (optional)

    Returns:
        Dictionary with processing results

    """
    try:
        logger.info("Updating knowledge graph: %s (%s)", entity_name, entity_type)

        kg = get_knowledge_graph()

        # Add the entity to the simplified knowledge graph
        kg.add_entity(entity_id, entity_name, entity_type)

        # Get updated stats
        stats = {
            "entities": len(kg.entities),
            "relationships": len(kg.relationships),
        }

        logger.info("Knowledge graph updated - %s", stats)
        return stats

    except Exception:
        logger.exception("Error updating knowledge graph")
        raise


@app.task(name="sample.health_check")
def health_check_task() -> dict[str, str]:
    """Health check task to verify worker is functioning.

    Returns:
        Dictionary with health status

    """
    try:
        logger.info("Running health check task")

        # Test knowledge graph access
        kg = get_knowledge_graph()
        entity_count = len(kg.entities)

        return {
            "status": "healthy",
            "worker": "aviator-plugin-sample",
            "entities": str(entity_count),
        }

    except Exception as e:
        logger.error("Health check failed: %s", e)
        return {"status": "unhealthy", "error": str(e), "worker": "aviator-plugin-sample"}


# Task registration for auto-discovery
__all__ = ["health_check_task", "process_embedding_task", "update_knowledge_graph_task"]
