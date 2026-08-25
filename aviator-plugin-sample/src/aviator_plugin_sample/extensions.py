"""Plugin entry points for Aviator ADT.

This module defines the plugin hooks that Aviator calls during initialization:
- startup(): Called when the plugin loads
- modify_tools(): Register custom tools and agents
- graph(): Modify the conversation graph (optional)
- prompts(): Modify system prompts (optional)
"""

import asyncio
import logging
import sys
from typing import Any

from aviator.models import EmbeddingRequest, StateModel
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from langchain_core.prompts import ChatPromptTemplate, SystemMessagePromptTemplate
from langchain_core.runnables import RunnableConfig
from langgraph.graph import StateGraph

# Import tools and utility agent
from .basic_tool import greeting_tool
from .holidays_canada import holidays_canada
from .knowledge_graph import get_knowledge_graph
from .knowledge_graph_tools import kg_add_entity, kg_add_relationship, kg_display_all, kg_search, kg_summary
from .nodes import my_validation_node
from .utility import call_utility_agent
from .weather_fetcher import weather_fetcher

# Fix for Windows: psycopg requires SelectorEventLoop
# Must be set BEFORE any async operations
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

logger = logging.getLogger(__name__)

router = APIRouter()


def build_sample_knowledge_graph() -> None:
    """Build a simple demo knowledge graph."""
    kg = get_knowledge_graph()

    logger.info("Building sample knowledge graph...")

    # Add a few demo entities
    kg.add_entity("company_opentext", "OpenText", "Company")
    kg.add_entity("product_aviator", "Aviator ADT", "Product")
    kg.add_entity("tech_langchain", "LangChain", "Technology")
    kg.add_entity("person_alice", "Alice", "Person")

    # Add demo relationships
    kg.add_relationship("company_opentext", "product_aviator", "develops")
    kg.add_relationship("product_aviator", "tech_langchain", "uses")
    kg.add_relationship("person_alice", "product_aviator", "works_on")

    logger.info("Knowledge graph built: %s", kg.summary())


@router.get("/test", tags=["test"])
async def test_endpoint() -> JSONResponse:
    """Test endpoint to verify the plugin is working."""
    return {"status": "Content Aviator plugin is operational."}


def startup_extension(**_kwargs: dict[str, Any]) -> None:
    """List of steps to execute during Content Aviator startup.

    Args:
        **kwargs (dict[str, Any]):
            Additional keyword arguments for customization.

    """
    logger.info("Sample plugin starting up...")

    # Initialize knowledge graph with sample data
    try:
        logger.info("Building sample knowledge graph...")
        build_sample_knowledge_graph()
        logger.info("Knowledge graph initialized successfully!")
    except Exception:
        logger.exception("Failed to initialize knowledge graph")

    logger.info("Plugin initialized successfully!")


def tool_extension(
    tools: list,
    state: StateModel | None = None,
    _config: RunnableConfig | None = None,
    **_kwargs: dict[str, Any],
) -> None:
    """Register additional 'assistant' tools. These are basically the agents.

    Args:
        tools (list):
            The list of tools to extend.
        state (StateModel | None):
            The current state model, if available.
        config (RunnableConfig | None):
            The runnable configuration, if available.
        **kwargs (dict[str, Any]):
            Additional keyword arguments for customization.

    """
    logger.info("Registering sample plugin tools and agents...")

    if state is not None:
        logger.debug("Current StateModel during tool_extension: %s", state)

    # Add all tools and agents to the list
    tools.append(greeting_tool)
    tools.append(weather_fetcher)
    tools.append(holidays_canada)
    tools.append(call_utility_agent)

    # Add knowledge graph tools
    tools.append(kg_add_entity)
    tools.append(kg_add_relationship)
    tools.append(kg_search)
    tools.append(kg_summary)
    tools.append(kg_display_all)


def graph_extension(graph: StateGraph, **_kwargs: dict[str, Any]) -> None:
    """Modify the graph.

    Args:
        graph (StateGraph):
            The state graph to modify.
        **kwargs (dict[str, Any]):
            Additional keyword arguments for customization.

    """
    logger.info("Graph modification hook called")

    # Import and register custom nodes
    graph.add_node("my_validation_node", my_validation_node)
    graph.add_edge("assistant", "my_validation_node")

    logger.info("Graph modification complete")


def prompt_extension(
    prompt_name: str,
    template: ChatPromptTemplate,
    _state: StateModel | None = None,
    _config: RunnableConfig | None = None,
    **_kwargs: dict[str, Any],
) -> None:
    """Update the prompt templates.

    Args:
        prompt_name (str):
            The name of the prompt to update.
        template (ChatPromptTemplate):
            The chat prompt template to modify.
        state (StateModel | None):
            The current state model, if available.
        config (RunnableConfig | None):
            The runnable configuration, if available.
        **kwargs (dict[str, Any]):
            Additional keyword arguments for customization.

    """

    match prompt_name:
        case "primary_assistant_prompt":
            logger.info("Adjusting the system prompt with plugin specific information ...")
            system_message: SystemMessagePromptTemplate = next(
                (msg for msg in template.messages if isinstance(msg, SystemMessagePromptTemplate)), None
            )

            if system_message is None:
                logger.warning("No SystemMessagePromptTemplate found in primary_assistant_prompt")
                return

            system_message.prompt.template += (
                "\n## Sample Plugin Custom Instructions\n\n"
                "You have access to the following custom tools from the sample plugin:\n"
                "- greeting_tool: Use for personalized greetings\n"
                "- weather_fetcher: Use to retrieve real-time weather using coordinates (latitude, longitude, location_name)\n"
                "- holidays_canada: Use to get Canadian holiday information for any province (returns all holidays as a detailed list)\n"
                "- utility: Delegate to a specialized utility agent with calculator for complex calculations\n\n"
                "IMPORTANT Guidelines:\n"
                "1. When users ask for greetings, always use greeting_tool\n"
                "2. For weather queries, use weather_fetcher with coordinates for real data\n"
                "3. For Canadian holiday queries, use holidays_canada with the province code to fetch holidays as a complete list\n"
                "5. For complex multi-step calculations, delegate to utility agent\n"
                "6. Always be friendly and explain your tool usage to the user\n\n"
                "CRITICAL: Call each tool ONLY ONCE per query. Tool results are complete and accurate.\n"
                "Do NOT call the same tool multiple times to verify or double-check results.\n"
                "Trust the first tool output and immediately return it to the user.\n\n"
            )

        case _:
            pass


def embedding_extension(
    request: EmbeddingRequest,
    _is_metadata: bool,
    **_kwargs: dict[str, Any],
) -> None:
    """Extend the embedding request to update the knowledge graph.

    This extension is triggered for each embedding request.
    We use it to update the knowledge graph when a workspace embedding is created/updated.

    Args:
        request (EmbeddingRequest):
            The embedding request.
        is_metadata (bool):
            Whether the embedding is for metadata.
        **kwargs (dict[str, Any]):
            Additional keyword arguments for customization.

    """
    logger.info("[EMBEDDING EXTENSION] Embedding extension called in worker process")

    try:
        metadata = request.metadata or {}
        doc_id = metadata.get("document_id", metadata.get("documentID", "unknown"))
        doc_name = metadata.get("name", "Unknown Document")

        logger.info("[EMBEDDING EXTENSION] Processing document '%s' (ID: %s)", doc_name, doc_id)
        logger.info("[EMBEDDING EXTENSION] Note: Knowledge graph is in-memory, use chat tools to add entities")

    except Exception:
        logger.exception("[EMBEDDING EXTENSION] Error processing embedding")
