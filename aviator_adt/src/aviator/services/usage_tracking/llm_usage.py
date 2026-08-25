"""Request-scoped LLM usage tracking helpers.

This module provides a callback handler and a context-local collector to
accumulate LLM request and token totals for a single API request.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import TYPE_CHECKING

from langchain_core.callbacks.base import BaseCallbackHandler

if TYPE_CHECKING:
    from langchain_core.outputs import LLMResult

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class LLMUsageTotals:
    """Aggregated LLM usage counters for one request."""

    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


_current_usage: ContextVar[LLMUsageTotals | None] = ContextVar("current_llm_usage", default=None)


def begin_llm_usage_collection() -> Token:
    """Start collecting usage counters in the current context."""
    return _current_usage.set(LLMUsageTotals())


def finish_llm_usage_collection(token: Token) -> LLMUsageTotals:
    """Stop collection and return accumulated counters.

    The context is reset using the provided token.
    """
    usage = _current_usage.get() or LLMUsageTotals()
    _current_usage.reset(token)
    return usage


def get_current_llm_usage() -> LLMUsageTotals | None:
    """Return the active request collector, if set."""
    return _current_usage.get()


def _extract_usage_from_message(message: object) -> tuple[int, int]:
    usage_metadata = getattr(message, "usage_metadata", None) or {}
    if usage_metadata:
        input_tokens = int(usage_metadata.get("input_tokens") or 0)
        output_tokens = int(usage_metadata.get("output_tokens") or 0)
        return input_tokens, output_tokens

    logger.debug("No usage metadata found on message")
    return 0, 0


def _extract_token_counts(result: LLMResult) -> tuple[int, int]:
    for generation_batch in result.generations:
        for generation in generation_batch:
            message = getattr(generation, "message", None)
            if message is None:
                continue
            counts = _extract_usage_from_message(message)
            if counts != (0, 0):
                return counts

    logger.debug("No generation messages found")
    return 0, 0


class LLMUsageTrackingCallbackHandler(BaseCallbackHandler):
    """LangChain callback handler that accumulates per-request LLM usage."""

    def on_llm_end(self, response: LLMResult, **_kwargs: object) -> None:
        """Capture usage counters from an LLM completion callback event."""
        usage = get_current_llm_usage()
        if usage is None:
            return

        usage.requests += 1
        input_tokens, output_tokens = _extract_token_counts(response)
        usage.input_tokens += input_tokens
        usage.output_tokens += output_tokens

        logger.debug("Added LLM usage for 1 request, %d input tokens, %d output tokens", input_tokens, output_tokens)
