"""Shared summary generation logic used by both the real-time summary task and migration backfill."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableConfig
from langchain_text_splitters import RecursiveCharacterTextSplitter
from opentelemetry import trace

from aviator.exceptions import WorkspaceSummaryError, WorkspaceSummaryRetryableError
from aviator.models import DocumentSummary
from aviator.services.llm import LLMRegistry
from aviator.services.prompts_loader import PromptsLoader
from aviator.settings import settings
from aviator.utils.token_utils import estimate_tokens

tracer = trace.get_tracer(__name__)
logger = logging.getLogger(__name__)

_DEFAULT_BATCH_MAX_CONCURRENCY = 5


@tracer.start_as_current_span("workspace_summary_generation")
def generate_summary(content: str) -> DocumentSummary:
    """Generate a document summary using the configured LLM.

    If content exceeds model context threshold, performs recursive summarization (Map-Reduce).

    Args:
        content: The full document text to summarise.

    Returns:
        A ``DocumentSummary`` with ``title`` and ``summary`` fields.

    Raises:
        WorkspaceSummaryError: On LLM configuration or invocation failures.

    """
    content_tokens = estimate_tokens(content)
    threshold = settings.summary_content_threshold_tokens

    if content_tokens > threshold:
        logger.info(
            "Content size (%d tokens) exceeds threshold (%d tokens). Splitting into batches.",
            content_tokens,
            threshold,
        )

        # Use RecursiveCharacterTextSplitter to split into safe batches
        # We target characters roughly (chars = tokens * 4) but use the token estimator for exactness
        batch_splitter = RecursiveCharacterTextSplitter(
            chunk_size=threshold * 4,
            chunk_overlap=settings.text_splitter_chunk_overlap * 4,
        )
        batches = batch_splitter.split_text(content)

        batch_summaries = []
        for i, batch in enumerate(batches):
            logger.info("Generating summary for batch %d/%d", i + 1, len(batches))
            batch_summary = generate_summary(batch)  # Recursive call
            batch_summaries.append(batch_summary.summary)

        # Final reduction
        combined_text = "\n\n".join(batch_summaries)
        logger.info("Generating final summary from %d batch summaries", len(batch_summaries))
        return generate_summary(combined_text)

    summary_runnable = _build_summary_runnable()

    # Invoke LLM
    with tracer.start_as_current_span("llm_invoke"):
        try:
            response = asyncio.run(summary_runnable.ainvoke({"content": content}))
        except Exception as e:
            error_msg = f"LLM summary invocation failed: {e!s}"
            logger.warning(error_msg)
            raise WorkspaceSummaryRetryableError(205, error_msg) from e

    if response is None:
        error_msg = "LLM returned empty summary"
        logger.error(error_msg)
        raise WorkspaceSummaryError(205, error_msg)

    return response


@dataclass
class _SummaryResult:
    """Internal result container for batch summary generation."""

    index: int
    summary: DocumentSummary | None
    error: str | None = None


def _build_summary_runnable():  # noqa: ANN202
    """Build the LangChain runnable for summary generation (prompt | LLM).

    Factored out so it can be constructed once and reused across a batch.

    Returns:
        A runnable chain that accepts ``{"content": str}`` and returns ``DocumentSummary``.

    Raises:
        WorkspaceSummaryError: On LLM configuration or prompt loading failures.

    """
    try:
        _summary_options: dict = {
            "max_tokens": settings.document_summary_max_tokens,
            "temperature": settings.llm_temperature,
        }
        _timeout = settings.summary_llm_request_timeout
        if _timeout is not None:
            _summary_options["timeout"] = _timeout
        llm = LLMRegistry.get_llm(options=_summary_options)
    except (ValueError, KeyError) as e:
        error_msg = f"LLM configuration error: {e!s}"
        logger.error(error_msg)
        raise WorkspaceSummaryError(202, error_msg) from e
    except Exception as e:
        error_msg = f"LLM initialization failed: {e!s}"
        logger.error(error_msg)
        raise WorkspaceSummaryRetryableError(202, error_msg) from e

    prompts_loader = PromptsLoader(model_provider=settings.llm_provider, model=settings.llm_model)
    try:
        prompt_template = asyncio.run(prompts_loader.load_prompt(prompt_name="summarize"))
        summary_prompt = ChatPromptTemplate.from_messages([("human", prompt_template)])
    except KeyError as e:
        error_msg = f"Prompt template formatting error: {e!s}"
        logger.error(error_msg)
        raise WorkspaceSummaryError(204, error_msg) from e

    return summary_prompt | llm.with_structured_output(DocumentSummary)


@tracer.start_as_current_span("workspace_summary_generation_batch")
def generate_summaries_batch(
    contents: list[str],
    *,
    max_concurrency: int = _DEFAULT_BATCH_MAX_CONCURRENCY,
) -> list[DocumentSummary | None]:
    """Generate summaries for multiple documents concurrently.

    Documents whose token count is within the threshold are batched through
    a single LangChain ``.batch()`` call (thread-pool concurrency).  Documents
    that exceed the threshold fall back to the existing :func:`generate_summary`
    which handles recursive map-reduce internally.

    Args:
        contents: List of document texts to summarise.
        max_concurrency: Maximum number of concurrent LLM requests.

    Returns:
        A list aligned with *contents* — ``DocumentSummary`` on success,
        ``None`` on failure, for each input document.

    """
    if not contents:
        return []

    threshold = settings.summary_content_threshold_tokens

    # Partition into simple (within threshold) and complex (needs map-reduce).
    simple_indices: list[int] = []
    complex_indices: list[int] = []
    for i, content in enumerate(contents):
        if estimate_tokens(content) > threshold:
            complex_indices.append(i)
        else:
            simple_indices.append(i)

    results: list[DocumentSummary | None] = [None] * len(contents)

    # ── Simple docs: concurrent .batch() ──────────────────────────────
    if simple_indices:
        try:
            runnable = _build_summary_runnable()
        except WorkspaceSummaryError:
            logger.exception("Failed to build summary runnable — all simple docs will fail.")
            simple_indices = []  # Skip batch; results stay None
            runnable = None

        if simple_indices and runnable:
            inputs = [{"content": contents[i]} for i in simple_indices]
            config = RunnableConfig(max_concurrency=max_concurrency)

            batch_results = runnable.batch(inputs, config=config, return_exceptions=True)

            for idx, result in zip(simple_indices, batch_results, strict=True):
                if isinstance(result, Exception):
                    logger.error(
                        "Summary generation failed for document at index %d: %s",
                        idx,
                        result,
                    )
                else:
                    results[idx] = result

    # ── Complex docs: sequential generate_summary() (map-reduce) ─────
    for idx in complex_indices:
        try:
            results[idx] = generate_summary(contents[idx])
        except Exception:
            logger.exception("Summary generation (map-reduce) failed for document at index %d", idx)

    return results
