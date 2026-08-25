r"""Tests for the recursive Map-Reduce summarization threshold logic.

Coverage matrix
---------------
Part 1  Settings.summary_content_threshold_tokens
        - default 70% threshold per provider
        - boundary values (0.0, 1.0)
        - custom percentage
        - unknown/fallback provider

Part 2  generate_summary() - Positive cases
        - content under threshold  -> 1 LLM call
        - content exactly at threshold  -> still 1 LLM call (condition is >)
        - content over threshold (2 batches)  -> 3 calls (map x2 + reduce x1)
        - 3-batch split  -> 4 calls
        - single batch from split still triggers reduce  -> 2 calls
        - batch summaries joined with '\n\n' before reduction call

Part 3  generate_summary() - Negative / error cases
        - LLMRegistry ValueError  -> WorkspaceSummaryError(202)
        - LLMRegistry KeyError    -> WorkspaceSummaryError(202)
        - PromptNotFoundError     -> propagates unchanged
        - ainvoke failure         -> WorkspaceSummaryError(205)

Part 4  generate_summary() - Edge cases
        - empty string (0 tokens)  -> direct LLM call
        - zero threshold           -> any content triggers batching
        - full (1.0) threshold     -> large content goes direct
        - splitter chunk_size      -> must equal threshold x 4
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aviator.exceptions import PromptNotFoundError, WorkspaceSummaryError, WorkspaceSummaryRetryableError
from aviator.models import DocumentSummary
from aviator.services.summary import generate_summary
from aviator.settings import LLMProvider, settings

_MOD = "aviator.services.summary"


# -- Helpers -------------------------------------------------------------------


def _ds(summary: str, title: str = "") -> DocumentSummary:
    """Return a DocumentSummary shorthand."""
    return DocumentSummary(summary=summary, title=title)


# -- Module-level fixtures -----------------------------------------------------


@pytest.fixture
def mock_summary_chain():
    """Patch ChatPromptTemplate so ``(prompt | llm_runnable)`` returns a controllable mock.

    Tests set ``.ainvoke`` on the yielded object.
    """
    mock_prompt = MagicMock()
    mock_runnable = MagicMock()
    mock_prompt.__or__.return_value = mock_runnable
    with patch(f"{_MOD}.ChatPromptTemplate.from_messages", return_value=mock_prompt):
        yield mock_runnable


@pytest.fixture
def mock_loader():
    """Patch PromptsLoader to always serve a minimal valid prompt template."""
    mock_instance = MagicMock()
    mock_instance.load_prompt = AsyncMock(return_value="Summarize: {content}")
    with patch(f"{_MOD}.PromptsLoader", return_value=mock_instance):
        yield mock_instance


@pytest.fixture
def mock_llm():
    """Return a generic mock LLM whose structured-output runner is also a mock."""
    llm = MagicMock()
    llm.with_structured_output.return_value = MagicMock()
    return llm


# ==============================================================================
# Part 1 - Settings: summary_content_threshold_tokens
# ==============================================================================


class TestSummaryContentThresholdTokens:
    """Verify the computed threshold = context_limit x threshold_ratio."""

    # -- Positive: default 0.7 threshold per provider -------------------------

    @pytest.mark.parametrize(
        ("provider", "llm_model", "context_limit"),
        [
            (LLMProvider.GOOGLE_GENAI, None, 1_000_000),
            (LLMProvider.OPENAI, "gpt-4o", 128_000),
            (LLMProvider.OPENAI, "gpt-5", 400_000),
            (LLMProvider.AWS_BEDROCK, "nova-lite-v1:0", 300_000),
            (LLMProvider.ANTHROPIC, None, 200_000),
            (LLMProvider.MISTRAL, None, 128_000),
        ],
    )
    def test_default_70pct_threshold_per_provider(self, provider, llm_model, context_limit):
        """Default 0.7 threshold applies correctly to each provider's context window."""
        expected = int(context_limit * 0.7)
        patches = [patch.object(settings, "llm_provider", provider)]
        if llm_model:
            patches.append(patch.object(settings, "llm_model", llm_model))

        with patches[0]:
            if llm_model:
                with patches[1]:
                    assert settings.summary_content_threshold_tokens == expected
            else:
                assert settings.summary_content_threshold_tokens == expected

    # -- Positive: custom threshold percentages --------------------------------

    def test_50pct_threshold_halves_context_window(self):
        """50% threshold returns exactly half the provider's context window."""
        with (
            patch.object(settings, "llm_provider", LLMProvider.ANTHROPIC),
            patch.object(settings, "summary_model_input_token_limit_threshold", 0.5),
        ):
            assert settings.summary_content_threshold_tokens == 100_000

    # -- Edge: boundary threshold values ---------------------------------------

    def test_threshold_1_0_equals_full_context_window(self):
        """Threshold of 1.0 exposes the entire context window for document content."""
        with (
            patch.object(settings, "llm_provider", LLMProvider.ANTHROPIC),
            patch.object(settings, "summary_model_input_token_limit_threshold", 1.0),
        ):
            assert settings.summary_content_threshold_tokens == 200_000

    def test_threshold_0_0_produces_zero_budget(self):
        """Threshold of 0.0 produces zero-token budget; every non-empty call triggers batching."""
        with (
            patch.object(settings, "llm_provider", LLMProvider.OPENAI),
            patch.object(settings, "summary_model_input_token_limit_threshold", 0.0),
        ):
            assert settings.summary_content_threshold_tokens == 0

    # -- Edge: unknown / fallback provider -------------------------------------

    def test_unknown_provider_falls_back_to_128k_window(self):
        """Unrecognised provider values fall back to 128k context window."""
        with (
            patch.object(settings, "llm_provider", "custom_llm"),
            patch.object(settings, "summary_model_input_token_limit_threshold", 1.0),
        ):
            assert settings.summary_content_threshold_tokens == 128_000


# ==============================================================================
# Part 2 - generate_summary(): Positive Cases
# ==============================================================================


class TestGenerateSummaryPositive:
    """Happy-path tests - content within and beyond the configured threshold."""

    def test_under_threshold_single_llm_call(self, mock_summary_chain, mock_loader, mock_llm):
        """Content well below threshold produces no splitting, exactly one LLM invocation."""
        mock_summary_chain.ainvoke = AsyncMock(return_value=_ds("Direct summary", "Title"))
        with (
            patch(f"{_MOD}.LLMRegistry.get_llm", return_value=mock_llm),
            patch(f"{_MOD}.estimate_tokens", return_value=100),
            patch("aviator.settings.Settings.summary_content_threshold_tokens", 1_000),
        ):
            result = generate_summary("Short content")

        assert result.summary == "Direct summary"
        assert result.title == "Title"
        mock_summary_chain.ainvoke.assert_called_once()

    def test_at_threshold_boundary_no_split(self, mock_summary_chain, mock_loader, mock_llm):
        """content_tokens == threshold: the '>' condition is NOT met, so no splitting."""
        threshold = 500
        mock_summary_chain.ainvoke = AsyncMock(return_value=_ds("At-boundary summary"))

        with (
            patch(f"{_MOD}.LLMRegistry.get_llm", return_value=mock_llm),
            patch(f"{_MOD}.estimate_tokens", return_value=threshold),
            patch("aviator.settings.Settings.summary_content_threshold_tokens", threshold),
        ):
            result = generate_summary("Medium content")

        assert result.summary == "At-boundary summary"
        mock_summary_chain.ainvoke.assert_called_once()

    def test_one_token_over_threshold_triggers_split(self, mock_summary_chain, mock_loader, mock_llm):
        """content_tokens = threshold + 1: the '>' condition fires; Map-Reduce is used."""
        threshold = 500
        mock_summary_chain.ainvoke = AsyncMock(side_effect=[_ds("Batch 1"), _ds("Final")])
        with (
            patch(f"{_MOD}.LLMRegistry.get_llm", return_value=mock_llm),
            patch(f"{_MOD}.estimate_tokens", side_effect=[threshold + 1, 200, 100]),
            patch(f"{_MOD}.RecursiveCharacterTextSplitter") as cls,
            patch("aviator.settings.Settings.summary_content_threshold_tokens", threshold),
        ):
            cls.return_value.split_text.return_value = ["one batch"]
            result = generate_summary("Just over threshold content")

        assert result.summary == "Final"
        assert mock_summary_chain.ainvoke.call_count == 2

    def test_two_batch_map_reduce_returns_final_summary(self, mock_summary_chain, mock_loader, mock_llm):
        """Two batches: 2 map calls + 1 reduce call = 3 total LLM invocations."""
        mock_summary_chain.ainvoke = AsyncMock(
            side_effect=[
                _ds("Batch 1 summary"),
                _ds("Batch 2 summary"),
                _ds("Final unified summary", "Final Title"),
            ]
        )
        with (
            patch(f"{_MOD}.LLMRegistry.get_llm", return_value=mock_llm),
            patch(f"{_MOD}.estimate_tokens", side_effect=[600, 200, 200, 100]),
            patch(f"{_MOD}.RecursiveCharacterTextSplitter") as cls,
            patch("aviator.settings.Settings.summary_content_threshold_tokens", 500),
        ):
            cls.return_value.split_text.return_value = ["batch one text", "batch two text"]
            result = generate_summary("Very long document")

        assert result.summary == "Final unified summary"
        assert result.title == "Final Title"
        assert mock_summary_chain.ainvoke.call_count == 3

    def test_batch_summaries_joined_with_double_newline(self, mock_summary_chain, mock_loader, mock_llm):
        r"""Verify the reduction call receives batch summaries separated by '\n\n'."""
        reduce_inputs: list[str] = []

        async def capturing_invoke(payload, **_):
            captured_content = payload.get("content", "")
            results = [_ds("A"), _ds("B"), _ds("FINAL")]
            reduce_inputs.append(captured_content)
            return results[len(reduce_inputs) - 1]

        mock_summary_chain.ainvoke = capturing_invoke

        with (
            patch(f"{_MOD}.LLMRegistry.get_llm", return_value=mock_llm),
            patch(f"{_MOD}.estimate_tokens", side_effect=[600, 200, 200, 100]),
            patch(f"{_MOD}.RecursiveCharacterTextSplitter") as cls,
            patch("aviator.settings.Settings.summary_content_threshold_tokens", 500),
        ):
            cls.return_value.split_text.return_value = ["text1", "text2"]
            generate_summary("Long content")

        # The 3rd ainvoke call (reduce) must receive joined summaries
        assert reduce_inputs[2] == "A\n\nB"

    def test_three_batch_map_reduce(self, mock_summary_chain, mock_loader, mock_llm):
        """Three batches produce 3 map + 1 reduce = 4 LLM calls."""
        mock_summary_chain.ainvoke = AsyncMock(side_effect=[_ds("B1"), _ds("B2"), _ds("B3"), _ds("Final")])
        with (
            patch(f"{_MOD}.LLMRegistry.get_llm", return_value=mock_llm),
            patch(f"{_MOD}.estimate_tokens", side_effect=[900, 200, 200, 200, 100]),
            patch(f"{_MOD}.RecursiveCharacterTextSplitter") as cls,
            patch("aviator.settings.Settings.summary_content_threshold_tokens", 500),
        ):
            cls.return_value.split_text.return_value = ["t1", "t2", "t3"]
            result = generate_summary("Very large document with three sections")

        assert result.summary == "Final"
        assert mock_summary_chain.ainvoke.call_count == 4

    def test_single_batch_from_splitter_still_triggers_reduce(self, mock_summary_chain, mock_loader, mock_llm):
        """Even if the splitter returns only one chunk, the reduce step still runs."""
        mock_summary_chain.ainvoke = AsyncMock(side_effect=[_ds("Single batch summary"), _ds("Reduced")])
        with (
            patch(f"{_MOD}.LLMRegistry.get_llm", return_value=mock_llm),
            patch(f"{_MOD}.estimate_tokens", side_effect=[600, 200, 100]),
            patch(f"{_MOD}.RecursiveCharacterTextSplitter") as cls,
            patch("aviator.settings.Settings.summary_content_threshold_tokens", 500),
        ):
            cls.return_value.split_text.return_value = ["single batch text"]
            result = generate_summary("Slightly too large content")

        assert result.summary == "Reduced"
        assert mock_summary_chain.ainvoke.call_count == 2


# ==============================================================================
# Part 3 - generate_summary(): Negative / Error Cases
# ==============================================================================


class TestGenerateSummaryNegative:
    """Error-path tests - LLM misconfiguration, missing prompt, invocation failure."""

    def test_llm_value_error_raises_code_202(self, mock_loader):
        """LLMRegistry raising ValueError must produce WorkspaceSummaryError(202)."""
        with (
            patch(f"{_MOD}.LLMRegistry.get_llm", side_effect=ValueError("no LLM configured")),
            patch(f"{_MOD}.estimate_tokens", return_value=10),
            patch("aviator.settings.Settings.summary_content_threshold_tokens", 1_000),
            pytest.raises(WorkspaceSummaryError) as exc,
        ):
            generate_summary("content")

        assert exc.value.code == 202
        assert "LLM configuration error" in exc.value.message

    def test_llm_key_error_raises_code_202(self, mock_loader):
        """LLMRegistry raising KeyError must also produce WorkspaceSummaryError(202)."""
        with (
            patch(f"{_MOD}.LLMRegistry.get_llm", side_effect=KeyError("missing key")),
            patch(f"{_MOD}.estimate_tokens", return_value=10),
            patch("aviator.settings.Settings.summary_content_threshold_tokens", 1_000),
            pytest.raises(WorkspaceSummaryError) as exc,
        ):
            generate_summary("content")

        assert exc.value.code == 202

    def test_prompt_not_found_propagates_unchanged(self):
        """PromptNotFoundError from PromptsLoader must propagate without wrapping."""
        mock_instance = MagicMock()
        mock_instance.load_prompt.side_effect = PromptNotFoundError("summarize")

        with (
            patch(f"{_MOD}.LLMRegistry.get_llm", return_value=MagicMock()),
            patch(f"{_MOD}.PromptsLoader", return_value=mock_instance),
            patch(f"{_MOD}.estimate_tokens", return_value=10),
            patch("aviator.settings.Settings.summary_content_threshold_tokens", 1_000),
            pytest.raises(PromptNotFoundError),
        ):
            generate_summary("content")

    def test_llm_invocation_failure_raises_retryable_code_205(self, mock_summary_chain, mock_loader, mock_llm):
        """Structured output failures become retryable summary errors."""
        mock_summary_chain.ainvoke = AsyncMock(side_effect=Exception("Structured output not supported by this model"))
        with (
            patch(f"{_MOD}.LLMRegistry.get_llm", return_value=mock_llm),
            patch(f"{_MOD}.estimate_tokens", return_value=10),
            patch("aviator.settings.Settings.summary_content_threshold_tokens", 1_000),
            pytest.raises(WorkspaceSummaryRetryableError) as exc,
        ):
            generate_summary("content")

        assert exc.value.code == 205
        assert "invocation failed" in exc.value.message.lower()

    def test_llm_initialization_failure_is_retryable(self, mock_loader):
        """Unexpected provider initialization failures become retryable."""
        with (
            patch(f"{_MOD}.LLMRegistry.get_llm", side_effect=RuntimeError("endpoint not reachable")),
            patch(f"{_MOD}.estimate_tokens", return_value=10),
            patch("aviator.settings.Settings.summary_content_threshold_tokens", 1_000),
            pytest.raises(WorkspaceSummaryRetryableError) as exc,
        ):
            generate_summary("content")

        assert exc.value.code == 202
        assert "initialization failed" in exc.value.message.lower()


# ==============================================================================
# Part 4 - generate_summary(): Edge Cases
# ==============================================================================


class TestGenerateSummaryEdgeCases:
    """Boundary conditions and unusual inputs."""

    def test_empty_string_goes_direct_to_llm(self, mock_summary_chain, mock_loader, mock_llm):
        """Empty string: estimate_tokens returns 0, which is <= any positive threshold."""
        mock_summary_chain.ainvoke = AsyncMock(return_value=_ds("empty doc summary"))

        with (
            patch(f"{_MOD}.LLMRegistry.get_llm", return_value=mock_llm),
            patch("aviator.settings.Settings.summary_content_threshold_tokens", 1_000),
        ):
            result = generate_summary("")

        assert result.summary == "empty doc summary"
        mock_summary_chain.ainvoke.assert_called_once()

    def test_zero_threshold_triggers_split_on_any_content(self, mock_summary_chain, mock_loader, mock_llm):
        """Threshold = 0: even a 1-token document is sent through Map-Reduce."""
        mock_summary_chain.ainvoke = AsyncMock(side_effect=[_ds("Batch result"), _ds("Reduced")])
        with (
            patch(f"{_MOD}.LLMRegistry.get_llm", return_value=mock_llm),
            patch(f"{_MOD}.estimate_tokens", side_effect=[1, 0, 0]),
            patch(f"{_MOD}.RecursiveCharacterTextSplitter") as cls,
            patch("aviator.settings.Settings.summary_content_threshold_tokens", 0),
        ):
            cls.return_value.split_text.return_value = ["tiny batch"]
            result = generate_summary("x")

        assert result.summary == "Reduced"
        assert mock_summary_chain.ainvoke.call_count == 2

    def test_full_threshold_large_content_within_window_goes_direct(self, mock_summary_chain, mock_loader, mock_llm):
        """Threshold 1.0 x Gemini 1M = 1M tokens: 999k tokens goes direct (no split)."""
        mock_summary_chain.ainvoke = AsyncMock(return_value=_ds("Full-window summary"))

        with (
            patch(f"{_MOD}.LLMRegistry.get_llm", return_value=mock_llm),
            patch.object(settings, "llm_provider", LLMProvider.GOOGLE_GENAI),
            patch.object(settings, "summary_model_input_token_limit_threshold", 1.0),
            patch(f"{_MOD}.estimate_tokens", return_value=999_999),
        ):
            result = generate_summary("Huge but within context window")

        assert result.summary == "Full-window summary"
        mock_summary_chain.ainvoke.assert_called_once()

    def test_splitter_receives_chunk_size_equal_to_threshold_times_four(
        self, mock_summary_chain, mock_loader, mock_llm
    ):
        """RecursiveCharacterTextSplitter chunk_size = threshold * 4 (4 chars ~ 1 token)."""
        mock_summary_chain.ainvoke = AsyncMock(side_effect=[_ds("B1"), _ds("Reduced")])
        threshold = 500

        with (
            patch(f"{_MOD}.LLMRegistry.get_llm", return_value=mock_llm),
            patch(f"{_MOD}.estimate_tokens", side_effect=[600, 200, 100]),
            patch(f"{_MOD}.RecursiveCharacterTextSplitter") as cls,
            patch("aviator.settings.Settings.summary_content_threshold_tokens", threshold),
        ):
            cls.return_value.split_text.return_value = ["batch1"]
            generate_summary("content over threshold")

        init_kwargs = cls.call_args.kwargs
        assert init_kwargs["chunk_size"] == threshold * 4

    def test_very_high_threshold_with_standard_document(self, mock_summary_chain, mock_loader, mock_llm):
        """Standard-length document (few thousand tokens) never triggers split at 70%+ threshold."""
        mock_summary_chain.ainvoke = AsyncMock(return_value=_ds("Normal summary"))

        with (
            patch(f"{_MOD}.LLMRegistry.get_llm", return_value=mock_llm),
            patch(f"{_MOD}.estimate_tokens", return_value=5_000),
            # AWS Bedrock: 300k x 0.7 = 210k > 5k -- no split
            patch.object(settings, "llm_provider", LLMProvider.AWS_BEDROCK),
            patch.object(settings, "summary_model_input_token_limit_threshold", 0.7),
        ):
            result = generate_summary("A realistic document with thousands of tokens")

        assert result.summary == "Normal summary"
        mock_summary_chain.ainvoke.assert_called_once()
