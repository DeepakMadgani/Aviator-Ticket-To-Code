"""Implement tool to interact with the vector store."""

import asyncio
import inspect
import json
import logging
from typing import Annotated, Any

from langchain_core.tools import tool
from langgraph.config import get_stream_writer
from langgraph.prebuilt import InjectedState
from opentelemetry import trace

from aviator.mcp.server.utils.decorator import mcp_expose
from aviator.models import (
    ContextDocumentModel,
    ContextRequestModel,
    RAGQueryModel,
    StateModel,
    WhereClauseReferenceModel,
)
from aviator.plugins import get_rag_metadata_retriever, load_search_filter_extensions
from aviator.services.llm import LLMRegistry
from aviator.services.permissions import apply_rag_permission_filter
from aviator.services.tenant import tenant_id_to_schema_name, tenant_service
from aviator.settings import settings
from aviator.utils.search_filter import build_metadata_post_filter, build_search_filter
from aviator.vector_store import vector_store

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


@tracer.start_as_current_span("get_doc_level_metadata")
async def get_doc_level_metadata(chunks: list[ContextDocumentModel], user: dict | None) -> dict[str, dict]:
    """Fetch document-level metadata from the content system for the given chunks.

    Args:
        chunks: List of context documents to fetch metadata for
        user: User object for authentication

    Returns:
        Dictionary mapping document_id to metadata dict

    """
    if not settings.add_doc_metadata_to_context:
        logger.debug("Document metadata fetching is disabled via settings")
        return {}

    if settings.content_system is None:
        logger.debug("No content system configured, skipping metadata fetch")
        return {}

    # Extract unique document IDs from chunks
    doc_ids = list({document.document_id for document in chunks})

    if not doc_ids:
        logger.debug("No document IDs found for metadata retrieval")
        return {}

    metadata_map: dict[str, dict] = {}

    # Get the metadata retriever for the configured content system
    metadata_retriever = get_rag_metadata_retriever()

    if metadata_retriever is None:
        logger.debug("No metadata retriever found for content system: %s", settings.content_system)
        return {}

    logger.info("Fetching document metadata for %d documents", len(doc_ids))
    with tracer.start_as_current_span("get_doc_level_metadata:retrieve"):
        try:
            # Call the metadata retriever plugin in a separate thread
            metadata_map = await asyncio.to_thread(metadata_retriever, user, doc_ids)

            if not metadata_map or not isinstance(metadata_map, dict):
                logger.exception("Metadata retriever returned invalid response: %s", type(metadata_map))
                return {}

            logger.debug("Successfully fetched metadata for %d documents", len(metadata_map))
        except Exception:
            logger.exception("Error fetching document metadata")
            return {}

    return metadata_map


@tracer.start_as_current_span("attach_metadata_to_chunks")
def attach_metadata_to_chunks(
    documents: list[ContextDocumentModel], metadata_map: dict[str, dict]
) -> list[ContextDocumentModel]:
    """Attach document-level metadata to chunks.

    Args:
        documents: List of context documents
        metadata_map: Dictionary mapping document_id to metadata dict

    Returns:
        List of documents with metadata attached

    """
    if not metadata_map:
        return documents

    for document in documents:
        if document.document_id in metadata_map:
            document.metadata = metadata_map[document.document_id]
            logger.debug("Attached metadata to document %s", document.document_id)

    return documents


@tracer.start_as_current_span("get_search_filter")
def get_search_filter(
    state_where: list[WhereClauseReferenceModel] | None = None,
    query_where: list[WhereClauseReferenceModel] | None = None,
) -> dict[str, list[str]]:
    """Build the MongoDB-style filter for the RAG query.

    Combines state-level and query-level filters. When both are provided,
    they are merged with AND logic to ensure query filters stay within
    the scope defined by state filters.

    Both native DB columns (``workspace_id``, ``document_id``) and custom
    metadata keys are included.  The PGVectorStore is patched to handle
    custom metadata keys via JSONB expressions at the SQL level.

    Args:
        state_where: State-level filter (defines allowed scope).
        query_where: Query-level filter (requested by the LLM tool call).

    Returns:
        MongoDB-style filter dict, or None if no filters.

    """

    extensions = load_search_filter_extensions()
    if extensions is not None and len(extensions) > 0:
        logger.info("Applying %d search filter extensions to RAG query.", len(extensions))

        for extension in extensions:
            try:
                if inspect.iscoroutinefunction(extension):
                    state_where = asyncio.run(extension(state_where))
                else:
                    state_where = extension(state_where)
            except Exception as e:
                logger.warning(
                    "Search filter extension '%s' failed with error: %s. Skipping this extension.",
                    extension,
                    str(e),
                )

    return build_search_filter(
        state_where_clauses=state_where or None,
        query_where_clauses=query_where or None,
    )


def _get_rag_tool_call_count_from_state(state: StateModel) -> int:
    """Count rag_query tool calls from the latest assistant tool-call message.

    Args:
        state: Current graph state.

    Returns:
        Number of rag_query calls in latest tool-call batch, or 1 if none found.

    """
    for message in reversed(state.messages):
        if tool_calls := getattr(message, "tool_calls", None):
            count = sum(1 for tc in tool_calls if tc.get("name") == "rag_query")
            return count if count > 0 else 1
    return 1


async def is_list_query(query_string: str) -> bool:
    """Determine whether a query is asking for a list of items using LLM classification.

    Args:
        query_string: The input query string to analyze.

    Returns:
        True if the query appears to be a list-type request.

    """
    if not query_string or not query_string.strip():
        return False

    try:
        llm = LLMRegistry.get_llm(options={"streaming": False, "max_tokens": 5})
        response = await llm.ainvoke(
            [
                {
                    "role": "system",
                    "content": "Answer only 'yes' or 'no'. Does the following query ask for a list of multiple items?",
                },
                {"role": "user", "content": query_string},
            ]
        )
        content = response.content.strip().lower() if hasattr(response, "content") else ""
        return content.startswith("yes")
    except Exception:
        logger.warning("LLM-based list query detection failed; defaulting to False.")
        return False


@tracer.start_as_current_span("api_rag_query")
async def api_rag_query(
    context: ContextRequestModel, tenant_id: str | None = None, user: dict[str, Any] | None = None
) -> list[ContextDocumentModel] | str:
    """Query the available knowledge base and provide information about the search string.

    Args:
        context (ContextRequestModel): Input for RAG query from API Call.
        tenant_id: Optional tenant identifier for schema isolation.
        user: Optional user information for context.

    Returns:
        list[ContextDocumentModel] | str: Matching context grouped by document from the vector store

    """

    search_filter = get_search_filter(state_where=context.metadata)
    schema_name = tenant_id_to_schema_name(tenant_id)

    # If schema-per-tenant is enabled and the tenant schema doesn't exist, return empty results
    if settings.multi_tenant_enabled and tenant_id is not None and not tenant_service.tenant_exists(tenant_id):
        logger.warning("Tenant '%s' does not exist. Returning empty results.", tenant_id)
        return []

    if settings.dev_tools:
        # this logging is only for development purpose. it exposes customer data in logs. #
        logger.debug(
            "Executing RAG search with query -> %s using filter -> %s (max results -> %d, schema -> %s)",
            context.query,
            search_filter,
            context.num_results,
            schema_name,
        )

    documents = await _search_documents_from_vector_store(
        schema_name=schema_name,
        search_string=context.query,
        limit=context.num_results,
        search_filter=search_filter,
        user=user,
        threshold=context.threshold,
    )

    return documents


@mcp_expose
@tool
@tracer.start_as_current_span("rag_query")
async def rag_query(
    query: RAGQueryModel,
    state: Annotated[StateModel, InjectedState],
) -> list[ContextDocumentModel] | str:
    """Query the available knowledge base and provide information about the search string.

    Use this tool for factual lookup from retrieved documents.
    Language behavior:
    - Do not infer response language from names, places, or nationality in the query.
    - Follow the user's explicit language preference if provided.
    - If no language is requested, default to English for the final answer.
    - Keep citations/chunk identifiers unchanged.

    Args:
        query: RAGQueryModel containing the search parameters.
        state: Current state of the Graph (injected).

    Returns:
        list[ContextDocumentModel] | str: Matching context items from the vector Store

    """
    # Validate and convert state if it's a dict (in case it's not properly injected as StateModel)
    if isinstance(state, dict):
        state = StateModel.model_validate(state)

    # This tool uses flat argument structure (individual parameters) rather than nested/structured
    # arguments for compatibility with models like llama3.3 that fail to generate nested arguments properly.

    # Handle string-serialized arguments for compatibility with llama3.3

    query_where: list[WhereClauseReferenceModel] | None = None
    is_summary = False

    if query.is_summary is not None:
        is_summary = query.is_summary.lower() == "true" if isinstance(query.is_summary, str) else bool(query.is_summary)

    # Fallback to state where clause if no search_filter was provided in query
    if query.search_filter is None:
        query.search_filter = state.where

    if query.search_filter is not None:
        if isinstance(query.search_filter, list):
            query_where = query.search_filter
        elif isinstance(query.search_filter, str):
            try:
                query_where = json.loads(query.search_filter)
            except json.JSONDecodeError:
                query_where = []

    # Dynamically increase limit for list-type queries, splitting budget across
    # sibling rag_query calls when multiple are emitted in the same assistant step.
    limit = settings.rag_default_limit
    if await is_list_query(state.query):
        count = _get_rag_tool_call_count_from_state(state)
        limit = max(settings.rag_default_limit, settings.rag_list_query_limit // count)

    search_filter = get_search_filter(state_where=state.where, query_where=query_where)

    if settings.dev_tools:
        # this logging is only for development purpose. it exposes customer data in logs. #
        logger.info("Executing RAG search for (limit: %s) -> %s (%s)", limit, query.search_string, search_filter)

    writer = get_stream_writer()
    writer(
        {
            "event": "message",
            "message": f"Executing RAG search for (filter: {search_filter}, limit: {limit}, threshold: {settings.vector_score_threshold}) -> {query.search_string}...\n",
        }
    )

    tenant_id = state.user.get("tenantId") if state.user else None
    schema_name = tenant_id_to_schema_name(tenant_id)

    # If schema-per-tenant is enabled and the tenant schema doesn't exist, return empty results
    if settings.multi_tenant_enabled and tenant_id is not None and not tenant_service.tenant_exists(tenant_id):
        logger.warning("Tenant '%s' does not exist. Returning empty results.", tenant_id)
        return json.dumps([])

    documents = await _search_documents_from_vector_store(
        schema_name=schema_name,
        search_string=query.search_string,
        limit=limit,
        search_filter=search_filter,
        user=state.user,
        threshold=settings.vector_score_threshold if not is_summary else None,
    )

    # Fetch and attach document-level metadata after grouping (if enabled) or directly to chunks
    # This ensures metadata is only attached once per document (if grouping is enabled)
    # or once per chunk (if grouping is disabled)
    metadata_map = await get_doc_level_metadata(documents, state.user)
    documents = attach_metadata_to_chunks(documents, metadata_map)

    if settings.dev_tools:
        # this logging is only for development purpose. it exposes customer data in logs. #
        logger.info("RAG search returned documents with metadata attached: %s", documents)

    writer(
        {
            "event": "message",
            "message": f"RAG search returned {sum(len(document.chunks) for document in documents)} chunks across {len(documents)} documents...\n",
        }
    )

    return json.dumps([document.model_dump(by_alias=True) for document in documents])


@tracer.start_as_current_span("_search_documents_from_vector_store")
async def _search_documents_from_vector_store(
    schema_name: str,
    search_string: str,
    limit: int,
    search_filter: dict | None,
    user: dict | None,
    threshold: float | None,
) -> list[ContextDocumentModel]:
    """Search the vector store for relevant documents based on the query and filters.

    Args:
        schema_name: The name of the schema to query.
        search_string: The search string to query the vector store.
        limit: Maximum number of results to return.
        search_filter: MongoDB-style filter dict to apply to the search.
        user: Optional user dict for permission filtering.
        threshold: Score threshold for filtering results.

    Returns:
        List of context documents matching the query and filters

    """

    if settings.dev_tools:
        # this logging is only for development purpose. it exposes customer data in logs. #
        logger.debug(
            "Executing RAG search with query -> %s using filter -> %s (max results -> %d, schema -> %s)",
            search_string,
            search_filter,
            limit,
            schema_name,
        )

    store = vector_store.get_adapter(schema_name=schema_name)

    with tracer.start_as_current_span("rag_query:similarity_search"):
        items = await store.asimilarity_search_with_relevance_scores(
            query=search_string,
            k=limit,
            metadata_filter=search_filter,
            score_threshold=threshold,
        )

    if not items:
        logger.info("RAG search returned 0 results.")
        return []
    else:
        logger.info("RAG search returned %d chunks from vector store.", len(items))

    # Convert langchain Documents to grouped context documents
    documents = prepare_context_from_chunks(items)

    logger.info("Context prepared: %d chunks grouped into %d documents", len(items), len(documents))

    if settings.dev_tools:
        # this logging is only for development purpose. it exposes customer data in logs. #
        logger.info("RAG search returned documents before permission filtering: %s", documents)

    if settings.content_system is not None:
        # Flatten documents to chunks, apply permission filter, then regroup
        flat_docs = []
        for document in documents:
            flat_docs.extend(document.chunks)

        filtered_chunks = await apply_rag_permission_filter(flat_docs, user, span_name="rag_query")

        if not filtered_chunks:
            # Either no items passed filtering, or the filter failed (fail-closed)
            return []

        # Regroup filtered chunks by document
        allowed_ids = {chunk.chunk_id for chunk in filtered_chunks}
        filtered_documents = []
        for document in documents:
            allowed_chunks = [chunk for chunk in document.chunks if chunk.chunk_id in allowed_ids]
            if allowed_chunks:
                document.chunks = allowed_chunks
                filtered_documents.append(document)
        documents = filtered_documents

    return documents


def _group_chunks_by_document(chunks: list[ContextDocumentModel]) -> list[ContextDocumentModel]:
    """Merge context documents by document/workspace while preserving chunk granularity.

    Args:
        chunks: Retrieved context documents in relevance order.

    Returns:
        List of context documents containing nested chunks.

    """
    doc_groups: dict[tuple[str, str], ContextDocumentModel] = {}

    for document in chunks:
        key = (document.document_id, document.workspace_id)
        if key not in doc_groups:
            doc_groups[key] = ContextDocumentModel(
                document_id=document.document_id,
                workspace_id=document.workspace_id,
                metadata=document.metadata,
                chunks=[],
            )
        elif doc_groups[key].metadata is None and document.metadata is not None:
            doc_groups[key].metadata = document.metadata

        doc_groups[key].chunks.extend(document.chunks)

    return list(doc_groups.values())


def prepare_context_from_chunks(chunks: list[tuple]) -> list[ContextDocumentModel]:
    """Prepare document-grouped context while preserving original chunk IDs.

    This keeps each retrieved chunk as-is so the assistant can cite exact chunk IDs
    returned by retrieval. Avoiding chunk concatenation removes citation ambiguity.

    Args:
        chunks: List of `(langchain_document, distance)` tuples retrieved from vector store.

    Returns:
        List of grouped documents with nested chunks.

    """
    context_documents = [ContextDocumentModel.from_langchain_document(doc, distance=score) for doc, score in chunks]
    return _group_chunks_by_document(context_documents)


# Re-export for backward compatibility; no additional logic needed.
get_metadata_post_filter = build_metadata_post_filter
