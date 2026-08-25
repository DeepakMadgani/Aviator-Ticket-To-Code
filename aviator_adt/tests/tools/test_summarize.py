"""Tests for summarize tool."""

from unittest.mock import AsyncMock, patch

import pytest
from langgraph.types import Command

from aviator.models import StateModel, WhereClauseReferenceModel
from aviator.tools.summarize import generate_summary

DEFAULT_SCHEMA = "public"
TEST_TOOL_CALL_ID = "test-call-id"


class TestFormatHelpers:
    """Tests for formatting helper functions."""

    def test_format_summary(self):
        from aviator.tools.summarize import _format_summary

        result = _format_summary({"document_id": "doc-1", "summary": "A brief summary."})
        assert result == "Document: A brief summary."

    def test_format_summary_with_different_content(self):
        from aviator.tools.summarize import _format_summary

        result = _format_summary({"document_id": "doc-1", "summary": "Content here."})
        assert result == "Document: Content here."


class TestGenerateSummary:
    """Tests for generate_summary tool."""

    @pytest.mark.anyio
    async def test_workspace_summary(self):
        """Scenario 1: workspace IDs route to filtered summary flow."""
        from aviator.tools.summarize import _SummaryResult

        state = StateModel(
            messages=[],
            where=[WhereClauseReferenceModel(workspaceID="ws-1")],
            user={"id": "user-1"},
        )

        with (
            patch(
                "aviator.tools.summarize._handle_filtered_summary",
                new_callable=AsyncMock,
                return_value=_SummaryResult(text="Workspace overview."),
            ) as mock_filtered,
            patch("aviator.tools.summarize.tenant_id_to_schema_name", return_value=DEFAULT_SCHEMA),
        ):
            result = await generate_summary.ainvoke(
                {"type": "tool_call", "name": "generate_summary", "args": {"state": state}, "id": TEST_TOOL_CALL_ID}
            )

        assert isinstance(result, Command)
        assert result.update["messages"][0].content == "Workspace overview."
        # No disclaimer: complete summary
        assert "summary_disclaimer" not in result.update
        mock_filtered.assert_called_once()

    @pytest.mark.anyio
    async def test_multi_doc_summary(self):
        """Scenario 2: where with multiple docs routes through filtered flow."""
        from aviator.tools.summarize import _SummaryResult

        state = StateModel(
            messages=[],
            where=[
                WhereClauseReferenceModel(documentID="doc-1"),
                WhereClauseReferenceModel(documentID="doc-2"),
            ],
            user={"id": "user-1"},
        )

        with (
            patch(
                "aviator.tools.summarize._handle_filtered_summary",
                new_callable=AsyncMock,
                return_value=_SummaryResult(text="Multi-doc summary."),
            ) as mock_filtered,
        ):
            result = await generate_summary.ainvoke(
                {"type": "tool_call", "name": "generate_summary", "args": {"state": state}, "id": TEST_TOOL_CALL_ID}
            )

        assert isinstance(result, Command)
        assert result.update["messages"][0].content == "Multi-doc summary."
        # No disclaimer: complete summary
        assert "summary_disclaimer" not in result.update
        mock_filtered.assert_called_once()

    @pytest.mark.anyio
    async def test_single_doc_summary(self):
        """Scenario 3: where with one doc routes through filtered flow."""
        from aviator.tools.summarize import _SummaryResult

        state = StateModel(
            messages=[],
            where=[WhereClauseReferenceModel(documentID="doc-1")],
            user={"id": "user-1"},
        )

        with (
            patch(
                "aviator.tools.summarize._handle_filtered_summary",
                new_callable=AsyncMock,
                return_value=_SummaryResult(text="The document summary."),
            ),
        ):
            result = await generate_summary.ainvoke(
                {"type": "tool_call", "name": "generate_summary", "args": {"state": state}, "id": TEST_TOOL_CALL_ID}
            )

        assert isinstance(result, Command)
        assert result.update["messages"][0].content == "The document summary."

    @pytest.mark.anyio
    async def test_no_context_returns_fallback(self):
        """No where filter returns a fallback message."""
        state = StateModel(messages=[], where=[], user=None)

        result = await generate_summary.ainvoke(
            {"type": "tool_call", "name": "generate_summary", "args": {"state": state}, "id": TEST_TOOL_CALL_ID}
        )

        assert isinstance(result, Command)
        assert "no context provided" in result.update["messages"][0].content.lower()
        # Error path: is_partial_summary should not be set
        assert "is_partial_summary" not in result.update

    @pytest.mark.anyio
    async def test_no_accessible_docs_workspace(self):
        """Workspace summary with no matching docs returns message."""
        state = StateModel(
            messages=[],
            where=[WhereClauseReferenceModel(workspaceID="ws-1")],
            user={"id": "user-1"},
        )

        mock_adapter = AsyncMock()
        mock_adapter.aget_distinct_document_ids.return_value = []

        with (
            patch("aviator.tools.summarize.tenant_id_to_schema_name", return_value=DEFAULT_SCHEMA),
            patch("aviator.tools.summarize.get_search_filter", return_value={}),
            patch("aviator.tools.summarize.vector_store") as mock_vs,
        ):
            mock_vs.get_adapter.return_value = mock_adapter
            result = await generate_summary.ainvoke(
                {"type": "tool_call", "name": "generate_summary", "args": {"state": state}, "id": TEST_TOOL_CALL_ID}
            )

        assert isinstance(result, Command)
        assert result.update["messages"][0].content == "No documents found matching the current filters."
        # Error path: summary_disclaimer should not be set
        assert "summary_disclaimer" not in result.update

    @pytest.mark.anyio
    async def test_no_summaries_single_doc(self):
        """Single-doc where with no summaries returns filtered-flow message."""
        state = StateModel(
            messages=[],
            where=[WhereClauseReferenceModel(documentID="doc-1")],
            user={"id": "user-1"},
        )

        mock_adapter = AsyncMock()
        mock_adapter.aget_distinct_document_ids.return_value = ["doc-1"]

        with (
            patch("aviator.tools.summarize.get_search_filter", return_value={"document_id": {"$eq": "doc-1"}}),
            patch("aviator.tools.summarize.vector_store") as mock_vs,
            patch("aviator.tools.summarize._get_accessible_documents", new_callable=AsyncMock, return_value=["doc-1"]),
            patch("aviator.tools.summarize._retrieve_summaries_of_docs", new_callable=AsyncMock, return_value=[]),
        ):
            mock_vs.get_adapter.return_value = mock_adapter
            result = await generate_summary.ainvoke(
                {"type": "tool_call", "name": "generate_summary", "args": {"state": state}, "id": TEST_TOOL_CALL_ID}
            )

        assert isinstance(result, Command)
        assert result.update["messages"][0].content == "No summaries available for the matching documents."
        # Partial: ingestion in progress (0 out of 1)
        assert "Above result is based on 0 out of 1" in result.update["summary_disclaimer"]

    @pytest.mark.anyio
    async def test_partial_summary_some_docs_missing(self):
        """When X < Y docs have summaries, summary_disclaimer is set with correct counts."""
        state = StateModel(
            messages=[],
            where=[
                WhereClauseReferenceModel(documentID="doc-1"),
                WhereClauseReferenceModel(documentID="doc-2"),
                WhereClauseReferenceModel(documentID="doc-3"),
            ],
            user={"id": "user-1"},
        )

        mock_adapter = AsyncMock()
        mock_adapter.aget_distinct_document_ids.return_value = ["doc-1", "doc-2", "doc-3"]

        with (
            patch("aviator.tools.summarize.get_search_filter", return_value={}),
            patch("aviator.tools.summarize.vector_store") as mock_vs,
            patch(
                "aviator.tools.summarize._get_accessible_documents",
                new_callable=AsyncMock,
                return_value=["doc-1", "doc-2", "doc-3"],
            ),
            patch(
                "aviator.tools.summarize._retrieve_summaries_of_docs",
                new_callable=AsyncMock,
                return_value=[
                    {"document_id": "doc-1", "summary": "Summary one."},
                    {"document_id": "doc-2", "summary": "Summary two."},
                ],
            ),
            patch(
                "aviator.tools.summarize._load_prompt",
                new_callable=AsyncMock,
                return_value="Summarize: {context}",
            ),
            patch(
                "aviator.tools.summarize._map_reduce_summaries",
                new_callable=AsyncMock,
                return_value="Partial workspace overview.",
            ),
        ):
            mock_vs.get_adapter.return_value = mock_adapter
            result = await generate_summary.ainvoke(
                {"type": "tool_call", "name": "generate_summary", "args": {"state": state}, "id": TEST_TOOL_CALL_ID}
            )

        assert isinstance(result, Command)
        assert result.update["messages"][0].content == "Partial workspace overview."
        assert "Above result is based on 2 out of 3" in result.update["summary_disclaimer"]

    @pytest.mark.anyio
    async def test_complete_summary_all_docs_present(self):
        """When all accessible docs have summaries, no disclaimer is set."""
        state = StateModel(
            messages=[],
            where=[
                WhereClauseReferenceModel(documentID="doc-1"),
                WhereClauseReferenceModel(documentID="doc-2"),
            ],
            user={"id": "user-1"},
        )

        mock_adapter = AsyncMock()
        mock_adapter.aget_distinct_document_ids.return_value = ["doc-1", "doc-2"]

        with (
            patch("aviator.tools.summarize.get_search_filter", return_value={}),
            patch("aviator.tools.summarize.vector_store") as mock_vs,
            patch(
                "aviator.tools.summarize._get_accessible_documents",
                new_callable=AsyncMock,
                return_value=["doc-1", "doc-2"],
            ),
            patch(
                "aviator.tools.summarize._retrieve_summaries_of_docs",
                new_callable=AsyncMock,
                return_value=[
                    {"document_id": "doc-1", "summary": "Summary one."},
                    {"document_id": "doc-2", "summary": "Summary two."},
                ],
            ),
            patch(
                "aviator.tools.summarize._load_prompt",
                new_callable=AsyncMock,
                return_value="Summarize: {context}",
            ),
            patch(
                "aviator.tools.summarize._map_reduce_summaries",
                new_callable=AsyncMock,
                return_value="Complete workspace overview.",
            ),
        ):
            mock_vs.get_adapter.return_value = mock_adapter
            result = await generate_summary.ainvoke(
                {"type": "tool_call", "name": "generate_summary", "args": {"state": state}, "id": TEST_TOOL_CALL_ID}
            )

        assert isinstance(result, Command)
        assert result.update["messages"][0].content == "Complete workspace overview."
        # Complete: no disclaimer
        assert "summary_disclaimer" not in result.update


class TestGetAccessibleDocIds:
    """Tests for _get_accessible_doc_ids permission filtering."""

    @pytest.mark.anyio
    async def test_document_ids_filtered_by_permission(self):
        """Document IDs are filtered through RAG permission filter."""
        from aviator.tools.summarize import _get_accessible_doc_ids

        def mock_perm_filter(chunks, user):
            # Only allow doc-1
            return [c for c in chunks if c.chunk_id == "doc-1"]

        with (
            patch("aviator.tools.summarize.settings") as mock_settings,
            patch("aviator.services.permissions.settings") as mock_perm_settings,
            patch(
                "aviator.services.permissions.load_rag_permission_filters",
                return_value=[("test_system", mock_perm_filter)],
            ),
        ):
            mock_settings.content_system = "test_system"
            mock_perm_settings.content_system = "test_system"
            result = await _get_accessible_doc_ids(
                user={"id": "user-1"},
                document_ids=["doc-1", "doc-2", "doc-3"],
            )

        assert result == ["doc-1"]

    @pytest.mark.anyio
    async def test_no_content_system_returns_all_ids(self):
        """When no content system is set, all document IDs are returned."""
        from aviator.tools.summarize import _get_accessible_doc_ids

        with patch("aviator.tools.summarize.settings") as mock_settings:
            mock_settings.content_system = None
            result = await _get_accessible_doc_ids(
                user={"id": "user-1"},
                document_ids=["doc-1", "doc-2"],
            )

        assert result == ["doc-1", "doc-2"]

    @pytest.mark.anyio
    async def test_no_matching_filter_returns_all_ids(self):
        """When no permission filter matches the content system, all IDs are returned."""
        from aviator.tools.summarize import _get_accessible_doc_ids

        with (
            patch("aviator.tools.summarize.settings") as mock_settings,
            patch(
                "aviator.services.permissions.load_rag_permission_filters",
                return_value=[("other_system", lambda c, u: c)],
            ),
        ):
            mock_settings.content_system = "test_system"
            result = await _get_accessible_doc_ids(
                user={"id": "user-1"},
                document_ids=["doc-1", "doc-2"],
            )

        assert result == ["doc-1", "doc-2"]

    @pytest.mark.anyio
    async def test_no_ids_returns_empty(self):
        """Returns empty list when no document_ids provided."""
        from aviator.tools.summarize import _get_accessible_doc_ids

        result = await _get_accessible_doc_ids(user={"id": "user-1"})

        assert result == []


class TestRetrieveSummariesOfDocs:
    """Tests for _retrieve_summaries_of_docs branching and mocks."""

    @pytest.mark.anyio
    async def test_returns_empty_for_none(self):
        from aviator.tools.summarize import _retrieve_summaries_of_docs

        result = await _retrieve_summaries_of_docs(None)

        assert result == []

    @pytest.mark.anyio
    async def test_returns_summaries_when_budget_allows(self):
        from aviator.tools.summarize import _retrieve_summaries_of_docs

        with (
            patch("aviator.tools.summarize.settings") as mock_settings,
            patch(
                "aviator.tools.summarize.retrieve_summaries_by_doc_ids",
                new_callable=AsyncMock,
                return_value=[{"document_id": "doc-1", "summary": "Summary one."}],
            ) as mock_summaries,
            patch(
                "aviator.tools.summarize.retrieve_titles_by_doc_ids",
                new_callable=AsyncMock,
            ) as mock_titles,
        ):
            mock_settings.document_summary_max_tokens = 200
            mock_settings.summary_batch_token_limit = 500
            result = await _retrieve_summaries_of_docs(["doc-1"], schema_name="tenant_x")

        assert result == [{"document_id": "doc-1", "summary": "Summary one."}]
        mock_summaries.assert_awaited_once_with(doc_ids=["doc-1"], schema_name="tenant_x")
        mock_titles.assert_not_called()

    @pytest.mark.anyio
    async def test_returns_titles_when_budget_exceeded(self):
        from aviator.tools.summarize import _retrieve_summaries_of_docs

        with (
            patch("aviator.tools.summarize.settings") as mock_settings,
            patch(
                "aviator.tools.summarize.retrieve_summaries_by_doc_ids",
                new_callable=AsyncMock,
            ) as mock_summaries,
            patch(
                "aviator.tools.summarize.retrieve_titles_by_doc_ids",
                new_callable=AsyncMock,
                return_value=[{"document_id": "doc-1", "summary": "Doc One"}],
            ) as mock_titles,
        ):
            mock_settings.document_summary_max_tokens = 300
            mock_settings.summary_batch_token_limit = 100
            result = await _retrieve_summaries_of_docs(["doc-1"], schema_name="tenant_x")

        assert result == [{"document_id": "doc-1", "summary": "Doc One"}]
        mock_titles.assert_awaited_once_with(doc_ids=["doc-1"], schema_name="tenant_x")
        mock_summaries.assert_not_called()


class TestFilteredSummaryMultiDocBranch:
    """Tests for the multi_document branch inside _handle_filtered_summary."""

    @pytest.mark.anyio
    async def test_multi_doc_uses_multi_doc_prompt(self):
        """When resolve type is multi_document, multi_doc_summary prompt is used."""
        from aviator.tools.summarize import _handle_filtered_summary, _SummaryResult

        state = StateModel(
            messages=[],
            where=[
                WhereClauseReferenceModel(documentID="doc-1"),
                WhereClauseReferenceModel(documentID="doc-2"),
            ],
            user={"id": "user-1"},
        )

        mock_adapter = AsyncMock()
        mock_adapter.aget_distinct_document_ids.return_value = ["doc-1", "doc-2"]

        with (
            patch("aviator.tools.summarize.get_search_filter", return_value={}),
            patch("aviator.tools.summarize.vector_store") as mock_vs,
            patch(
                "aviator.tools.summarize._get_accessible_documents",
                new_callable=AsyncMock,
                return_value=["doc-1", "doc-2"],
            ),
            patch(
                "aviator.tools.summarize._retrieve_summaries_of_docs",
                new_callable=AsyncMock,
                return_value=[
                    {"document_id": "doc-1", "summary": "Summary one."},
                    {"document_id": "doc-2", "summary": "Summary two."},
                ],
            ),
            patch(
                "aviator.tools.summarize._load_prompt", new_callable=AsyncMock, return_value="Summarize."
            ) as mock_load,
            patch(
                "aviator.tools.summarize._map_reduce_summaries",
                new_callable=AsyncMock,
                return_value="Final multi-doc summary.",
            ) as mock_map_reduce,
        ):
            mock_vs.get_adapter.return_value = mock_adapter
            result = await _handle_filtered_summary(state, "summarize this")

        assert isinstance(result, _SummaryResult)
        assert result.text == "Final multi-doc summary."
        # Complete: all docs had summaries, no disclaimer
        assert result.disclaimer is None
        mock_load.assert_called_once_with("multi_doc_summary")
        mock_map_reduce.assert_called_once()

    @pytest.mark.anyio
    async def test_single_doc_returns_summary_directly(self):
        """When only one accessible doc, returns stored summary without LLM call."""
        from aviator.tools.summarize import _handle_filtered_summary, _SummaryResult

        state = StateModel(
            messages=[],
            where=[WhereClauseReferenceModel(documentID="doc-1")],
            user={"id": "user-1"},
        )

        mock_adapter = AsyncMock()
        mock_adapter.aget_distinct_document_ids.return_value = ["doc-1"]

        with (
            patch("aviator.tools.summarize.get_search_filter", return_value={}),
            patch("aviator.tools.summarize.vector_store") as mock_vs,
            patch(
                "aviator.tools.summarize._get_accessible_documents",
                new_callable=AsyncMock,
                return_value=["doc-1"],
            ),
            patch(
                "aviator.tools.summarize._retrieve_summaries_of_docs",
                new_callable=AsyncMock,
                return_value=[{"document_id": "doc-1", "summary": "The document summary."}],
            ),
        ):
            mock_vs.get_adapter.return_value = mock_adapter
            result = await _handle_filtered_summary(state, "summarize this")

        assert isinstance(result, _SummaryResult)
        assert result.text == "The document summary."
        # Complete: single doc had its summary, no disclaimer
        assert result.disclaimer is None


class TestResolveSummaryType:
    """Tests for _resolve_summary_type with where intent and accessible docs."""

    @pytest.mark.anyio
    async def test_one_accessible_doc_forces_single(self):
        """One accessible document should always resolve to single_document."""
        from aviator.tools.summarize import _resolve_summary_type

        where = [
            WhereClauseReferenceModel(documentID="doc-1"),
            WhereClauseReferenceModel(documentID="doc-2"),
        ]
        result = _resolve_summary_type(where, ["doc-1"])

        assert result == "single_document"

    @pytest.mark.anyio
    async def test_multi_where_and_multiple_accessible_stays_multi(self):
        """Multi-document where intent should stay multi with >1 accessible docs."""
        from aviator.tools.summarize import _resolve_summary_type

        where = [
            WhereClauseReferenceModel(documentID="doc-1"),
            WhereClauseReferenceModel(documentID="doc-2"),
        ]
        result = _resolve_summary_type(where, ["doc-1", "doc-2"])

        assert result == "multi_document"

    @pytest.mark.anyio
    async def test_non_document_where_defaults_overall(self):
        """Workspace-only where intent should resolve to overall with multiple docs."""
        from aviator.tools.summarize import _resolve_summary_type

        where = [WhereClauseReferenceModel(workspaceID="ws-1")]
        result = _resolve_summary_type(where, ["doc-1", "doc-2"])

        assert result == "overall"


class TestMapReduceSummaries:
    """Tests for _map_reduce_summaries map-reduce pattern."""

    @pytest.mark.anyio
    async def test_empty_summaries_returns_empty(self):
        """Returns empty string for empty input."""
        from aviator.tools.summarize import _map_reduce_summaries

        result = await _map_reduce_summaries("prompt", [], "summarize")
        assert result == ""

    @pytest.mark.anyio
    async def test_single_summary_returns_as_is(self):
        """Returns single summary unchanged."""
        from aviator.tools.summarize import _map_reduce_summaries

        result = await _map_reduce_summaries("prompt", [{"summary": "Only one summary."}], "summarize")
        assert result == "Only one summary."

    @pytest.mark.anyio
    async def test_single_batch_direct_invocation(self):
        """Single batch with multiple summaries is summarized directly without reduction."""
        from aviator.tools.summarize import _map_reduce_summaries

        with (
            patch(
                "aviator.tools.summarize._batch_summaries_by_tokens",
                return_value=["batch1"],  # Single batch
            ),
            patch(
                "aviator.tools.summarize._invoke_llm",
                new_callable=AsyncMock,
                return_value="Direct summary.",
            ) as mock_llm,
        ):
            result = await _map_reduce_summaries(
                "prompt", [{"summary": "Summary 1"}, {"summary": "Summary 2"}], "summarize"
            )

        assert result == "Direct summary."
        mock_llm.assert_called_once_with("prompt", "batch1", "summarize")

    @pytest.mark.anyio
    async def test_multiple_batches_with_reduction(self):
        """Multiple batches are reduced iteratively."""
        from aviator.tools.summarize import _map_reduce_summaries

        with (
            patch(
                "aviator.tools.summarize._batch_summaries_by_tokens",
                side_effect=[
                    ["batch1", "batch2"],  # First iteration: 2 batches
                ],
            ),
            patch(
                "aviator.tools.summarize._invoke_llm_batch",
                new_callable=AsyncMock,
                return_value=["Final summary."],  # Only one summary after first reduction
            ) as mock_batch,
        ):
            result = await _map_reduce_summaries(
                "prompt",
                [
                    {"summary": "Summary 1"},
                    {"summary": "Summary 2"},
                ],
                "summarize",
            )

        assert result == "Final summary."
        mock_batch.assert_called_once()


class TestBatchSummariesByTokens:
    """Tests for _batch_summaries_by_tokens function."""

    def test_batches_summaries_correctly(self):
        """Summaries are batched and joined correctly with Document: prefix."""
        from aviator.tools.summarize import _batch_summaries_by_tokens

        summaries = [
            {"document_id": "doc-1", "summary": "Summary one."},
            {"document_id": "doc-2", "summary": "Summary two."},
        ]

        with patch(
            "aviator.tools.summarize.batch_by_token_limit",
            return_value=[["Document: Summary one.", "Document: Summary two."]],
        ):
            result = _batch_summaries_by_tokens(summaries)

        assert len(result) == 1
        assert "Document: Summary one." in result[0]
        assert "Document: Summary two." in result[0]

    def test_batches_multiple_summaries(self):
        """Multiple summaries are properly formatted with prefix and batched."""
        from aviator.tools.summarize import _batch_summaries_by_tokens

        summaries = [
            {"document_id": "doc-1", "summary": "Content 1"},
            {"document_id": "doc-2", "summary": "Content 2"},
            {"document_id": "doc-3", "summary": "Content 3"},
        ]

        with patch(
            "aviator.tools.summarize.batch_by_token_limit",
            return_value=[
                ["Document: Content 1", "Document: Content 2"],
                ["Document: Content 3"],
            ],
        ):
            result = _batch_summaries_by_tokens(summaries)

        assert len(result) == 2
        assert "Document: Content 1" in result[0]
        assert "Document: Content 2" in result[0]
        assert "Document: Content 3" in result[1]


class TestInvokeLLMBatch:
    """Tests for _invoke_llm_batch parallel invocation."""

    @pytest.mark.anyio
    async def test_parallel_llm_invocation(self):
        """Multiple contexts are invoked in parallel."""
        from aviator.tools.summarize import _invoke_llm_batch

        with patch(
            "aviator.tools.summarize._invoke_llm",
            new_callable=AsyncMock,
            side_effect=["Result 1", "Result 2", "Result 3"],
        ) as mock_llm:
            result = await _invoke_llm_batch("prompt", ["context1", "context2", "context3"], "summarize")

        assert result == ["Result 1", "Result 2", "Result 3"]
        assert mock_llm.call_count == 3


class TestInvokeLLM:
    """Tests for _invoke_llm function."""

    @pytest.mark.anyio
    async def test_invoke_llm_formats_messages(self):
        """LLM is invoked with system and human messages, context in human message."""
        from aviator.tools.summarize import _invoke_llm

        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=AsyncMock(content="LLM response"))

        with patch("aviator.tools.summarize.LLMRegistry.get_llm", return_value=mock_llm):
            result = await _invoke_llm("System prompt", "Context text", "Summarize this")

        assert result == "LLM response"
        mock_llm.ainvoke.assert_called_once()
        messages = mock_llm.ainvoke.call_args[0][0]
        assert messages[0].content == "System prompt"
        assert "Context text" in messages[1].content
        assert "Summarize this" in messages[1].content


class TestLoadPrompt:
    """Tests for _load_prompt function."""

    @pytest.mark.anyio
    async def test_load_prompt_uses_settings(self):
        """Prompt loader uses correct settings."""
        from aviator.tools.summarize import _load_prompt

        mock_loader = AsyncMock()
        mock_loader.load_prompt = AsyncMock(return_value="Prompt content")

        with (
            patch("aviator.tools.summarize.settings") as mock_settings,
            patch("aviator.tools.summarize.PromptsLoader", return_value=mock_loader),
        ):
            mock_settings.llm_provider = "openai"
            mock_settings.llm_model = "gpt-5-nano"
            result = await _load_prompt("test_prompt")

        assert result == "Prompt content"
        mock_loader.load_prompt.assert_called_once_with("test_prompt")


class TestHandleFilteredSummary:
    """Tests for _handle_filtered_summary — filter-based doc discovery via vector store."""

    @pytest.mark.anyio
    async def test_filter_flow_discovers_docs_via_vector_store(self):
        """Documents are discovered via the vector store adapter using the where filter."""
        from aviator.tools.summarize import _handle_filtered_summary

        state = StateModel(
            messages=[],
            where=[WhereClauseReferenceModel(workspaceID=None, documentID=None, customKey="val1")],
            user={"id": "user-1"},
        )

        mock_adapter = AsyncMock()
        mock_adapter.aget_distinct_document_ids.return_value = ["doc-1", "doc-2"]

        with (
            patch("aviator.tools.summarize.get_search_filter", return_value={"customKey": {"$eq": "val1"}}),
            patch("aviator.tools.summarize.tenant_id_to_schema_name", return_value="public"),
            patch("aviator.tools.summarize.vector_store") as mock_vs,
            patch(
                "aviator.tools.summarize._get_accessible_documents",
                new_callable=AsyncMock,
                return_value=["doc-1", "doc-2"],
            ),
            patch(
                "aviator.tools.summarize._retrieve_summaries_of_docs",
                new_callable=AsyncMock,
                return_value=[
                    {"document_id": "doc-1", "summary": "Summary one."},
                    {"document_id": "doc-2", "summary": "Summary two."},
                ],
            ),
            patch("aviator.tools.summarize._load_prompt", new_callable=AsyncMock, return_value="Summarize."),
            patch(
                "aviator.tools.summarize._map_reduce_summaries",
                new_callable=AsyncMock,
                return_value="Filtered overview.",
            ) as mock_map_reduce,
        ):
            mock_vs.get_adapter.return_value = mock_adapter
            result = await _handle_filtered_summary(state, "summarize this")

        assert result.text == "Filtered overview."
        # Complete: no disclaimer
        assert result.disclaimer is None
        mock_adapter.aget_distinct_document_ids.assert_called_once_with(metadata_filter={"customKey": {"$eq": "val1"}})
        mock_map_reduce.assert_called_once()

    @pytest.mark.anyio
    async def test_filter_flow_no_documents_found(self):
        """Returns fallback when vector store has no matching documents."""
        from aviator.tools.summarize import _handle_filtered_summary

        state = StateModel(
            messages=[],
            where=[WhereClauseReferenceModel(workspaceID=None, documentID=None, customKey="val1")],
            user={"id": "user-1"},
        )

        mock_adapter = AsyncMock()
        mock_adapter.aget_distinct_document_ids.return_value = []

        with (
            patch("aviator.tools.summarize.get_search_filter", return_value={"customKey": {"$eq": "val1"}}),
            patch("aviator.tools.summarize.tenant_id_to_schema_name", return_value="public"),
            patch("aviator.tools.summarize.vector_store") as mock_vs,
        ):
            mock_vs.get_adapter.return_value = mock_adapter
            result = await _handle_filtered_summary(state, "summarize this")

        assert result.text == "No documents found matching the current filters."
        assert result.disclaimer is None  # Error path: no disclaimer

    @pytest.mark.anyio
    async def test_filter_flow_no_accessible_docs(self):
        """Returns fallback when permission filter removes all docs."""
        from aviator.tools.summarize import _handle_filtered_summary

        state = StateModel(
            messages=[],
            where=[WhereClauseReferenceModel(workspaceID=None, documentID=None, customKey="val1")],
            user={"id": "user-1"},
        )

        mock_adapter = AsyncMock()
        mock_adapter.aget_distinct_document_ids.return_value = ["doc-1"]

        with (
            patch("aviator.tools.summarize.get_search_filter", return_value={"customKey": {"$eq": "val1"}}),
            patch("aviator.tools.summarize.tenant_id_to_schema_name", return_value="public"),
            patch("aviator.tools.summarize.vector_store") as mock_vs,
            patch(
                "aviator.tools.summarize._get_accessible_documents",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            mock_vs.get_adapter.return_value = mock_adapter
            result = await _handle_filtered_summary(state, "summarize this")

        assert result.text == "No accessible documents found for the user."
        assert result.disclaimer is None  # Error path: no disclaimer

    @pytest.mark.anyio
    async def test_filter_flow_no_summaries_available(self):
        """Returns fallback when summaries table has no entries for discovered docs."""
        from aviator.tools.summarize import _handle_filtered_summary

        state = StateModel(
            messages=[],
            where=[WhereClauseReferenceModel(workspaceID=None, documentID=None, customKey="val1")],
            user={"id": "user-1"},
        )

        mock_adapter = AsyncMock()
        mock_adapter.aget_distinct_document_ids.return_value = ["doc-1"]

        with (
            patch("aviator.tools.summarize.get_search_filter", return_value={"customKey": {"$eq": "val1"}}),
            patch("aviator.tools.summarize.tenant_id_to_schema_name", return_value="public"),
            patch("aviator.tools.summarize.vector_store") as mock_vs,
            patch(
                "aviator.tools.summarize._get_accessible_documents",
                new_callable=AsyncMock,
                return_value=["doc-1"],
            ),
            patch(
                "aviator.tools.summarize._retrieve_summaries_of_docs",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            mock_vs.get_adapter.return_value = mock_adapter
            result = await _handle_filtered_summary(state, "summarize this")

        assert result.text == "No summaries available for the matching documents."
        assert "Above result is based on 0 out of 1" in result.disclaimer

    @pytest.mark.anyio
    async def test_filter_flow_uses_tenant_schema(self):
        """Adapter is resolved with tenant-specific schema passed from caller."""
        from aviator.tools.summarize import _handle_filtered_summary

        state = StateModel(
            messages=[],
            where=[WhereClauseReferenceModel(workspaceID=None, documentID=None, customKey="val1")],
            user={"id": "user-1", "tenantId": "acme"},
        )

        mock_adapter = AsyncMock()
        mock_adapter.aget_distinct_document_ids.return_value = []

        with (
            patch("aviator.tools.summarize.get_search_filter", return_value={}),
            patch("aviator.tools.summarize.vector_store") as mock_vs,
        ):
            mock_vs.get_adapter.return_value = mock_adapter
            await _handle_filtered_summary(state, "summarize this", schema_name="tenant_acme")

        mock_vs.get_adapter.assert_called_once_with(schema_name="tenant_acme")


class TestGenerateSummaryFilteredFlow:
    """Integration tests for generate_summary routing to filtered flow."""

    @pytest.mark.anyio
    async def test_where_with_no_workspace_no_doc_routes_to_filtered(self):
        """Where clause with only custom metadata routes to _handle_filtered_summary."""
        from aviator.tools.summarize import _SummaryResult

        state = StateModel(
            messages=[],
            where=[WhereClauseReferenceModel(workspaceID=None, documentID=None, customKey="val1")],
            user={"id": "user-1"},
        )

        with patch(
            "aviator.tools.summarize._handle_filtered_summary",
            new_callable=AsyncMock,
            return_value=_SummaryResult(text="Filtered result."),
        ) as mock_filtered:
            result = await generate_summary.ainvoke(
                {"type": "tool_call", "name": "generate_summary", "args": {"state": state}, "id": TEST_TOOL_CALL_ID}
            )

        assert isinstance(result, Command)
        assert result.update["messages"][0].content == "Filtered result."
        mock_filtered.assert_called_once()

    @pytest.mark.anyio
    async def test_workspace_id_routes_to_filtered_flow(self):
        """Where clause with workspace_id routes through the unified filtered flow."""
        from aviator.tools.summarize import _SummaryResult

        state = StateModel(
            messages=[],
            where=[WhereClauseReferenceModel(workspaceID="ws-1")],
            user={"id": "user-1"},
        )

        with (
            patch("aviator.tools.summarize.tenant_id_to_schema_name", return_value=DEFAULT_SCHEMA),
            patch(
                "aviator.tools.summarize._handle_filtered_summary",
                new_callable=AsyncMock,
                return_value=_SummaryResult(text="Workspace result."),
            ) as mock_filtered,
        ):
            result = await generate_summary.ainvoke(
                {"type": "tool_call", "name": "generate_summary", "args": {"state": state}, "id": TEST_TOOL_CALL_ID}
            )

        assert isinstance(result, Command)
        assert result.update["messages"][0].content == "Workspace result."
        mock_filtered.assert_called_once()
