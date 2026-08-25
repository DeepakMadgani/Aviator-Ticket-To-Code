"""Tests for aviator.services.summary — batch summary generation."""

from unittest.mock import MagicMock, patch

from aviator.exceptions import WorkspaceSummaryError
from aviator.models import DocumentSummary


class TestGenerateSummariesBatch:
    """Tests for generate_summaries_batch()."""

    def test_empty_input_returns_empty_list(self):
        from aviator.services.summary import generate_summaries_batch

        assert generate_summaries_batch([]) == []

    def test_simple_docs_use_batch_call(self):
        """All docs within threshold go through .batch() in a single call."""
        from aviator.services.summary import generate_summaries_batch

        summaries = [
            DocumentSummary(title="T1", summary="S1"),
            DocumentSummary(title="T2", summary="S2"),
        ]

        mock_runnable = MagicMock()
        mock_runnable.batch.return_value = summaries

        with (
            patch("aviator.services.summary.estimate_tokens", return_value=100),
            patch("aviator.services.summary._build_summary_runnable", return_value=mock_runnable),
        ):
            results = generate_summaries_batch(["text one", "text two"])

        assert results == summaries
        mock_runnable.batch.assert_called_once()
        # Verify inputs passed to .batch()
        call_args = mock_runnable.batch.call_args
        assert call_args[0][0] == [{"content": "text one"}, {"content": "text two"}]

    def test_complex_docs_fallback_to_generate_summary(self):
        """Docs exceeding threshold fall back to generate_summary() (map-reduce)."""
        from aviator.services.summary import generate_summaries_batch

        expected = DocumentSummary(title="Big", summary="Big summary")

        with (
            patch("aviator.services.summary.estimate_tokens", return_value=999_999),
            patch("aviator.services.summary.generate_summary", return_value=expected) as mock_gen,
        ):
            results = generate_summaries_batch(["huge content"])

        assert results == [expected]
        mock_gen.assert_called_once_with("huge content")

    def test_mixed_simple_and_complex(self):
        """Batch correctly partitions simple and complex docs."""
        from aviator.services.summary import generate_summaries_batch

        simple_summary = DocumentSummary(title="Simple", summary="S")
        complex_summary = DocumentSummary(title="Complex", summary="C")

        def fake_estimate(content):
            # "big" content is over threshold, "small" is under
            return 999_999 if "big" in content else 100

        mock_runnable = MagicMock()
        mock_runnable.batch.return_value = [simple_summary, simple_summary]

        with (
            patch("aviator.services.summary.estimate_tokens", side_effect=fake_estimate),
            patch("aviator.services.summary._build_summary_runnable", return_value=mock_runnable),
            patch("aviator.services.summary.generate_summary", return_value=complex_summary),
        ):
            results = generate_summaries_batch(["small text", "big content", "another small"])

        # Index 0 and 2 are simple (batched), index 1 is complex (fallback)
        assert results[0] == simple_summary
        assert results[1] == complex_summary
        assert results[2] == simple_summary
        # .batch() called with 2 simple inputs
        batch_inputs = mock_runnable.batch.call_args[0][0]
        assert len(batch_inputs) == 2

    def test_partial_batch_failures_return_none(self):
        """If some docs fail in .batch(), those return None while others succeed."""
        from aviator.services.summary import generate_summaries_batch

        ok_summary = DocumentSummary(title="OK", summary="Good")

        mock_runnable = MagicMock()
        mock_runnable.batch.return_value = [ok_summary, RuntimeError("LLM fail"), ok_summary]

        with (
            patch("aviator.services.summary.estimate_tokens", return_value=100),
            patch("aviator.services.summary._build_summary_runnable", return_value=mock_runnable),
        ):
            results = generate_summaries_batch(["a", "b", "c"])

        assert results[0] == ok_summary
        assert results[1] is None  # Failed
        assert results[2] == ok_summary

    def test_complex_doc_failure_returns_none(self):
        """If a complex doc's generate_summary() raises, result is None."""
        from aviator.services.summary import generate_summaries_batch

        with (
            patch("aviator.services.summary.estimate_tokens", return_value=999_999),
            patch("aviator.services.summary.generate_summary", side_effect=RuntimeError("boom")),
        ):
            results = generate_summaries_batch(["big content"])

        assert results == [None]

    def test_runnable_build_failure_marks_all_simple_as_none(self):
        """If _build_summary_runnable fails, all simple docs get None."""
        from aviator.services.summary import generate_summaries_batch

        with (
            patch("aviator.services.summary.estimate_tokens", return_value=100),
            patch(
                "aviator.services.summary._build_summary_runnable",
                side_effect=WorkspaceSummaryError(202, "LLM config error"),
            ),
        ):
            results = generate_summaries_batch(["a", "b"])

        assert results == [None, None]

    def test_max_concurrency_passed_to_batch(self):
        """max_concurrency is forwarded to the .batch() config."""
        from aviator.services.summary import generate_summaries_batch

        mock_runnable = MagicMock()
        mock_runnable.batch.return_value = [DocumentSummary(title="T", summary="S")]

        with (
            patch("aviator.services.summary.estimate_tokens", return_value=100),
            patch("aviator.services.summary._build_summary_runnable", return_value=mock_runnable),
        ):
            generate_summaries_batch(["text"], max_concurrency=10)

        config = mock_runnable.batch.call_args[1].get("config") or mock_runnable.batch.call_args[0][1]
        assert config.get("max_concurrency") == 10
