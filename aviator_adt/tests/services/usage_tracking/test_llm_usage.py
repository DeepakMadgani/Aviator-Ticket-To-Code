"""Tests for LLM usage callback collection."""

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from aviator.services.usage_tracking.llm_usage import (
    LLMUsageTrackingCallbackHandler,
    begin_llm_usage_collection,
    finish_llm_usage_collection,
)


def test_callback_collects_usage_metadata_tokens():
    token = begin_llm_usage_collection()
    handler = LLMUsageTrackingCallbackHandler()

    message = AIMessage(
        content="ok",
        usage_metadata={
            "input_tokens": 12,
            "output_tokens": 4,
            "total_tokens": 16,
        },
    )
    result = LLMResult(generations=[[ChatGeneration(message=message)]])

    handler.on_llm_end(result)
    totals = finish_llm_usage_collection(token)

    assert totals.requests == 1
    assert totals.input_tokens == 12
    assert totals.output_tokens == 4


def test_callback_no_usage_metadata():
    handler = LLMUsageTrackingCallbackHandler()
    result = LLMResult(generations=[[ChatGeneration(message=AIMessage(content="ok"))]])

    # Should not raise when no usage-metadata is available.
    handler.on_llm_end(result)


def test_callback_active_collection_without_usage_metadata():
    token = begin_llm_usage_collection()
    handler = LLMUsageTrackingCallbackHandler()
    result = LLMResult(generations=[[ChatGeneration(message=AIMessage(content="ok"))]])

    handler.on_llm_end(result)

    totals = finish_llm_usage_collection(token)

    assert totals.requests == 1
    assert totals.input_tokens == 0
    assert totals.output_tokens == 0
