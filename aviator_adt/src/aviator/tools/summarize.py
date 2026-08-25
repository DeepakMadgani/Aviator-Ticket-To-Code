"""Tool for generating workspace and document summaries.

All requests flow through the filtered summary path which classifies by where-clause
intent and post-permission document count:
- Single document — direct return of the stored summary
- Multi-document — per-document summaries highlighted by name using map-reduce
- Overall / workspace — high-level overview via the workspace summary prompt
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Annotated

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_core.tools.base import InjectedToolCallId
from langgraph.prebuilt import InjectedState
from langgraph.types import Command
from opentelemetry import trace

from aviator.models import Chunk, StateModel, WhereClauseReferenceModel

# Permission filtering is now done via shared plugin helper (see apply_rag_permission_filter)
from aviator.services.docdata_retrieval import (
    retrieve_summaries_by_doc_ids,
    retrieve_titles_by_doc_ids,
)
from aviator.services.llm import LLMRegistry
from aviator.services.prompts_loader import PromptsLoader
from aviator.services.tenant import tenant_id_to_schema_name
from aviator.settings import settings
from aviator.tools.rag import get_search_filter
from aviator.utils.token_utils import batch_by_token_limit
from aviator.vector_store import vector_store

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

_prompts_loader: PromptsLoader | None = None


@dataclass
class _SummaryResult:
    """Internal result container from _handle_filtered_summary.

    Attributes:
        text: The summary text to return as the tool message content.
        disclaimer: Pre-formatted disclaimer to append to the response when ingestion is incomplete.
            None when no disclaimer should be shown (error paths or all docs have summaries).

    """

    text: str
    disclaimer: str | None = None


def _get_prompts_loader() -> PromptsLoader:
    """Return a module-level PromptsLoader singleton so prompt caching is reused across calls."""
    global _prompts_loader  # noqa: PLW0603
    if _prompts_loader is None:
        _prompts_loader = PromptsLoader(
            model_provider=settings.llm_provider,
            model=settings.llm_model,
        )
    return _prompts_loader


@tool
@tracer.start_as_current_span("generate_summary")
async def generate_summary(
    state: Annotated[StateModel, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Generate summaries for workspaces or documents based on the current context.

    Use this tool when the user requests a summary of a workspace or one or more documents.
    The tool automatically determines the summary type from the where filter:

    - **Filtered summary**: When workspace IDs, multiple document IDs, or other
      metadata filters are provided, discovers documents via the vector store and
      returns a summary. Uses the multi-document prompt when explicit document IDs
      are present, and the workspace prompt otherwise.
    - **Single document summary**: When exactly one document ID is provided, returns
      the stored summary directly.

    Do not prefer `rag_query` for summary formatting alone. Use `rag_query` only
    when the user is asking questions that require retrieval or details beyond
    the stored summary content.

    Returns:
        Command: A LangGraph Command that updates graph state with the summary text
        and, when applicable, sets summary_disclaimer.

    """
    logger.info("Generating summary with where filter: %s", state.where)

    user_query = state.query
    tenant_id = state.user.get("tenantId") if state.user else None
    schema_name = tenant_id_to_schema_name(tenant_id)

    if state.where:
        logger.info("Generating filtered summary from where clause")
        result = await _handle_filtered_summary(state, user_query, schema_name)
    else:
        result = _SummaryResult(text="Unable to generate summary: no context provided in the request.")

    state_update: dict = {
        "messages": [
            ToolMessage(
                content=result.text,
                tool_call_id=tool_call_id,
                name="generate_summary",
            )
        ]
    }

    if result.disclaimer is not None:
        state_update["summary_disclaimer"] = result.disclaimer

    return Command(update=state_update)


async def _handle_filtered_summary(
    state: StateModel, user_query: str, schema_name: str | None = None
) -> _SummaryResult:
    """Generate a high-level overview from documents matching the where filter.

    Uses the same filter-based approach as the RAG tool to discover documents
    via the vector store, so it works regardless of whether workspace_id is present.

    Returns:
        _SummaryResult with text and an optional pre-formatted disclaimer.
        disclaimer is None for error/empty paths (no state update needed).

    """
    search_filter = get_search_filter(state_where=state.where)
    adapter = vector_store.get_adapter(schema_name=schema_name)
    doc_ids = await adapter.aget_distinct_document_ids(metadata_filter=search_filter)
    if settings.dev_tools:
        logger.debug("documents found matching the current filters are %s", doc_ids)

    if not doc_ids:
        return _SummaryResult(text="No documents found matching the current filters.")

    accessible_doc_ids = await _get_accessible_documents(state.user, doc_ids)
    if not accessible_doc_ids:
        return _SummaryResult(text="No accessible documents found for the user.")

    total = len(accessible_doc_ids)
    summaries = await _retrieve_summaries_of_docs(accessible_doc_ids, schema_name=schema_name)
    found = len(summaries)

    if not summaries:
        logger.info(
            "No summaries found for %d accessible document(s); ingestion may be in progress",
            total,
        )
        return _SummaryResult(
            text="No summaries available for the matching documents.",
            disclaimer=(
                f"> **Note:** Above result is based on 0 out of {total} docs. Remaining summaries are still being generated, please try again after few mins."
            ),
        )

    is_partial = found < total
    if is_partial:
        logger.info(
            "Partial summary: found %d summaries for %d accessible document(s); ingestion may be in progress",
            found,
            total,
        )

    summary_type = _resolve_summary_type(state.where, accessible_doc_ids)
    logger.info("Summary type classified as '%s' using where-clause intent and accessible documents", summary_type)
    match summary_type:
        case "single_document":
            logger.info("Generating single-document summary for %s", accessible_doc_ids[0])
            text = summaries[0]["summary"]
        case "multi_document":
            logger.info("Generating multi-document summary for %d documents", len(accessible_doc_ids))
            prompt_template = await _load_prompt("multi_doc_summary")
            text = await _map_reduce_summaries(prompt_template, summaries, user_query)
        case _:
            logger.info("Generating overall summary for %d documents", len(accessible_doc_ids))
            prompt_template = await _load_prompt("workspace_summary")
            text = await _map_reduce_summaries(prompt_template, summaries, user_query)

    return _SummaryResult(
        text=text,
        disclaimer=(
            f"> **Note:** Above result is based on {found} out of {total} docs. Remaining summaries are still being generated, please try again after few mins."
        )
        if is_partial
        else None,
    )


def _summary_type(where: list[WhereClauseReferenceModel]) -> str:
    """Classify summary type from where-clause filters.

    Returns:
        "single_document" when exactly one document ID is present,
        "multi_document" when multiple document IDs are present,
        "overall" otherwise (workspace-level or no document filters).

    """

    def _extract_ids(value: str | list[str] | dict | None) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [v for v in value if isinstance(v, str)]
        if isinstance(value, dict):
            extracted: list[str] = []
            for v in value.values():
                extracted.extend(_extract_ids(v))
            return extracted
        return []

    document_ids: set[str] = set()
    for where_clause in where:
        if not isinstance(where_clause, WhereClauseReferenceModel):
            continue
        document_ids.update(_extract_ids(where_clause.document_id))

    if len(document_ids) == 1:
        return "single_document"
    if len(document_ids) > 1:
        return "multi_document"
    return "overall"


def _resolve_summary_type(
    where: list[WhereClauseReferenceModel],
    accessible_doc_ids: list[str],
) -> str:
    """Resolve summary type using where-clause intent and post-permission accessibility.

    Resolution order:
    1. Use where-clause analysis as the initial intent.
    2. Override to single-document when only one accessible document remains.
    3. Keep multi-document only when the where intent is multi-document and multiple
       accessible documents remain.
    4. Otherwise fall back to overall summary.

    """
    where_intent = _summary_type(where)

    if len(accessible_doc_ids) == 1:
        return "single_document"
    if where_intent == "multi_document" and len(accessible_doc_ids) > 1:
        return "multi_document"
    return "overall"


def _format_summary(summary: dict[str, str]) -> str:
    """Format a summary dict into a context string."""
    return f"Document: {summary['summary']}"


def _batch_summaries_by_tokens(summaries: list[dict[str, str]]) -> list[str]:
    """Split summaries into context batches respecting the configured token limit.

    Args:
        summaries: List of summary dicts to batch.

    Returns:
        List of context batch strings.

    """
    formatted = (_format_summary(s) for s in summaries)
    batches = batch_by_token_limit(formatted, settings.summary_batch_token_limit)
    return ["\n\n".join(batch) for batch in batches]


async def _invoke_llm_batch(
    system_prompt: str,
    contexts: list[str],
    user_query: str,
) -> list[str]:
    """Invoke the LLM for multiple contexts in parallel.

    Args:
        system_prompt: System prompt for the LLM.
        contexts: List of context strings to process.
        user_query: The original user query.

    Returns:
        List of LLM responses.

    """
    tasks = [_invoke_llm(system_prompt, context, user_query) for context in contexts]
    return await asyncio.gather(*tasks)


async def _map_reduce_summaries(
    prompt_template: str,
    summaries: list[dict[str, str]],
    user_query: str,
) -> str:
    """Apply map-reduce pattern to summarize documents.

    Maps initial summaries into batches, reduces recursively until one summary remains.
    Handles token limits intelligently to minimize redundant processing.

    Args:
        prompt_template: System prompt for the LLM.
        summaries: List of summary dicts to process (from docdata_retrieval module).
        user_query: The original user query.

    Returns:
        Final consolidated summary string.

    """
    if not summaries:
        return ""

    if len(summaries) == 1:
        return summaries[0]["summary"]

    # Map phase: batch and summarize
    batches = _batch_summaries_by_tokens(summaries)
    logger.debug("Map phase: %d summaries -> %d batches", len(summaries), len(batches))

    if len(batches) == 1:
        return await _invoke_llm(prompt_template, batches[0], user_query)

    # Reduce phase: iteratively reduce until single summary remains
    current = await _invoke_llm_batch(prompt_template, batches, user_query)
    reduction_round = 0

    while len(current) > 1:
        reduction_round += 1
        current_dicts = [{"summary": s} for s in current]
        batches = _batch_summaries_by_tokens(current_dicts)
        logger.debug(
            "Reduce round %d: %d summaries -> %d batches",
            reduction_round,
            len(current),
            len(batches),
        )
        current = await _invoke_llm_batch(prompt_template, batches, user_query)

    return current[0]


async def _load_prompt(prompt_name: str) -> str:
    """Load a prompt template by name using the PromptsLoader.

    Args:
        prompt_name: Name of the prompt file (without .md extension).

    Returns:
        Prompt template string.

    """
    loader = _get_prompts_loader()
    return await loader.load_prompt(prompt_name)


async def _invoke_llm(system_prompt: str, context: str, user_query: str) -> str:
    """Invoke the LLM with a system prompt, context, and user query."""
    llm = LLMRegistry.get_llm(options={"max_tokens": settings.summary_tool_max_token})
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"**User query:** {user_query}\n\n**Document summaries:**\n{context}"),
    ]
    response = await llm.ainvoke(messages)
    return str(response.content)


async def _get_accessible_doc_ids(
    user: dict | None,
    document_ids: list[str] | None = None,
) -> list[str]:
    """Retrieve document IDs the user can access, filtered by permissions."""
    if document_ids:
        return await _get_accessible_documents(user, document_ids)

    return []


async def _get_accessible_documents(user: dict | None, document_ids: list[str]) -> list[str]:
    """Filter requested document IDs by user permissions."""
    if settings.content_system is None:
        return document_ids

    chunks = [Chunk(chunk_id=doc_id, text="", document_id=doc_id) for doc_id in document_ids]

    from aviator.services.permissions import apply_rag_permission_filter

    filtered_chunks = await apply_rag_permission_filter(chunks, user, span_name="summarize_permission_check")
    return [chunk.chunk_id for chunk in filtered_chunks]


async def _retrieve_summaries_of_docs(
    document_ids: list[str] | None,
    schema_name: str | None = None,
) -> list[dict[str, str]]:
    """Retrieve summaries or titles based on token budget constraints.

    Args:
        document_ids: List of document IDs to retrieve.
        schema_name: PostgreSQL schema for tenant isolation.

    Returns:
        List of summary dicts with 'document_id', 'summary'.

    """
    if not document_ids:
        return []

    estimated_budget = settings.document_summary_max_tokens * len(document_ids)
    if estimated_budget <= settings.summary_batch_token_limit:
        return await retrieve_summaries_by_doc_ids(doc_ids=document_ids, schema_name=schema_name)

    logger.debug(
        "Estimated token budget (%d) exceeds limit (%d), retrieving titles only",
        estimated_budget,
        settings.summary_batch_token_limit,
    )
    return await retrieve_titles_by_doc_ids(doc_ids=document_ids, schema_name=schema_name)
