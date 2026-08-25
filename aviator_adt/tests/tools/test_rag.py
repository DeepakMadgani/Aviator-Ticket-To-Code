"""Test suite for rag module."""

import json
from unittest.mock import AsyncMock, patch

import pytest
from langchain.messages import AIMessage
from langchain_core.documents import Document

from aviator.models import (
    Chunk,
    ContextDocumentModel,
    ContextRequestModel,
    StateModel,
    WhereClauseReferenceModel,
)
from aviator.settings import settings
from aviator.tools.rag import (
    RAGQueryModel,
    _get_rag_tool_call_count_from_state,
    api_rag_query,
    attach_metadata_to_chunks,
    get_doc_level_metadata,
    get_metadata_post_filter,
    get_search_filter,
    prepare_context_from_chunks,
    rag_query,
)


class TestRAGQueryModel:
    """Tests for RAGQueryModel."""

    def test_valid_query_model(self):
        """Test creating a valid RAG query model."""
        model = RAGQueryModel(
            search_string="test query",
            search_filter=[],
        )
        assert model.search_string == "test query"
        assert model.search_filter == []

    def test_query_model_with_document_filter(self):
        """Test RAG query model with document filter."""
        doc_ref = WhereClauseReferenceModel(document_id="doc-123")
        model = RAGQueryModel(
            search_string="test query",
            search_filter=[doc_ref],
        )
        assert len(model.search_filter) == 1
        assert model.search_filter[0].document_id == "doc-123"

    def test_query_model_with_workspace_filter(self):
        """Test RAG query model with workspace filter."""
        ws_ref = WhereClauseReferenceModel(workspace_id="ws-456")
        model = RAGQueryModel(
            search_string="test query",
            search_filter=[ws_ref],
        )
        assert len(model.search_filter) == 1
        assert model.search_filter[0].workspace_id == "ws-456"

    def test_query_model_with_mixed_filters(self):
        """Test RAG query model with mixed document and workspace filters."""
        doc_ref = WhereClauseReferenceModel(document_id="doc-123")
        ws_ref = WhereClauseReferenceModel(workspace_id="ws-456")
        model = RAGQueryModel(
            search_string="test query",
            search_filter=[doc_ref, ws_ref],
        )
        assert len(model.search_filter) == 2

    def test_default_values(self):
        """Test default values are applied."""
        model = RAGQueryModel(search_string="test")
        assert model.search_filter == []


class TestGetSearchFilter:
    """Tests for get_search_filter function."""

    def test_both_filters_empty(self):
        """Test when both state_where and query_where are empty."""
        result = get_search_filter(state_where=[], query_where=[])
        assert result is None

    def test_both_filters_none(self):
        """Test when both filters are None."""
        result = get_search_filter(state_where=None, query_where=None)
        assert result is None

    def test_state_where_only(self):
        """Test with only state_where filter."""
        doc_ref = WhereClauseReferenceModel(document_id="doc-123")
        result = get_search_filter(state_where=[doc_ref], query_where=[])
        assert "document_id" in result
        assert result == {"document_id": {"$eq": "doc-123"}}

    def test_workspace_filter(self):
        """Test with workspace filter."""
        ws_ref = WhereClauseReferenceModel(workspace_id="ws-456")
        result = get_search_filter(state_where=[ws_ref], query_where=[])
        assert "workspace_id" in result
        assert result == {"workspace_id": {"$eq": "ws-456"}}

    def test_mixed_document_and_workspace_filters(self):
        """Test with both document and workspace filters."""
        doc_ref = WhereClauseReferenceModel(document_id="doc-123")
        ws_ref = WhereClauseReferenceModel(workspace_id="ws-456")
        result = get_search_filter(state_where=[doc_ref, ws_ref], query_where=[])
        assert result == {
            "$or": [
                {"document_id": {"$eq": "doc-123"}},
                {"workspace_id": {"$eq": "ws-456"}},
            ]
        }

    def test_query_where_combined_with_state_where(self):
        """Test query_where is AND'd with state_where."""
        doc1_ref = WhereClauseReferenceModel(document_id="doc-1")
        doc2_ref = WhereClauseReferenceModel(document_id="doc-2")
        doc3_ref = WhereClauseReferenceModel(document_id="doc-3")
        result = get_search_filter(
            state_where=[doc1_ref, doc2_ref],
            query_where=[doc1_ref, doc3_ref],  # doc3 not in state_where
        )

        assert result is not None
        assert result == {"document_id": {"$in": ["doc-1", "doc-2"]}}

    def test_metadata_key_in_db_filter(self):
        """Custom metadata keys are now included in the DB filter via JSONB."""
        docbase_ref = WhereClauseReferenceModel(docbaseName="doc-001")
        result = get_search_filter(state_where=[docbase_ref])
        assert result == {"docbaseName": {"$eq": "doc-001"}}

    def test_mixed_column_and_metadata_full_filter(self):
        """AND group with mixed keys — all conditions go to DB filter."""
        ws_ref = WhereClauseReferenceModel(workspace_id="ws1")
        docbase_ref = WhereClauseReferenceModel(docbaseName="doc-001")
        result = get_search_filter(state_where=[ws_ref, docbase_ref])
        assert result == {
            "$or": [
                {"workspace_id": {"$eq": "ws1"}},
                {"docbaseName": {"$eq": "doc-001"}},
            ]
        }

    def test_or_with_metadata_included(self):
        """OR groups with metadata keys are now fully included in DB filter."""
        ws_ref = WhereClauseReferenceModel(workspace_id="ws1")
        docbase_ref = WhereClauseReferenceModel(docbaseName="doc1")
        result = get_search_filter(state_where=[ws_ref, docbase_ref])
        assert result == {
            "$or": [
                {"workspace_id": {"$eq": "ws1"}},
                {"docbaseName": {"$eq": "doc1"}},
            ]
        }

    def test_query_where_subset_of_state_where(self):
        """Test query_where items must be in state_where."""
        doc1 = WhereClauseReferenceModel(document_id="doc-1")
        doc2 = WhereClauseReferenceModel(document_id="doc-2")
        doc3 = WhereClauseReferenceModel(document_id="doc-3")

        result = get_search_filter(
            state_where=[doc1, doc2],
            query_where=[doc1, doc3],  # doc3 not in state_where
        )
        # Only doc1 should be included
        assert result == {"document_id": {"$in": ["doc-1", "doc-2"]}}

    def test_multiple_documents_in_filter(self):
        """Test multiple documents in filter."""
        doc1 = WhereClauseReferenceModel(document_id="doc-1")
        doc2 = WhereClauseReferenceModel(document_id="doc-2")

        result = get_search_filter(state_where=[doc1, doc2], query_where=[])
        assert result == {"document_id": {"$in": ["doc-1", "doc-2"]}}

    def test_multiple_workspaces_in_filter(self):
        """Test multiple workspaces in filter."""
        ws1 = WhereClauseReferenceModel(workspace_id="ws-1")
        ws2 = WhereClauseReferenceModel(workspace_id="ws-2")
        result = get_search_filter(state_where=[ws1, ws2], query_where=[])
        assert result == {"workspace_id": {"$in": ["ws-1", "ws-2"]}}

    def test_query_where_empty_with_state_where(self):
        """Test empty query_where with populated state_where."""
        doc_ref = WhereClauseReferenceModel(document_id="doc-123")

        result = get_search_filter(state_where=[doc_ref], query_where=[])
        assert result == {"document_id": {"$eq": "doc-123"}}


class TestGetMetadataPostFilter:
    """Tests for get_metadata_post_filter function (deprecated — always returns None)."""

    def test_column_only_returns_none(self):
        """Post-filter is deprecated; all filtering is now at the DB level."""
        ws_ref = WhereClauseReferenceModel(workspace_id="ws1")
        pf = get_metadata_post_filter(state_where=[ws_ref])
        assert pf is None

    def test_custom_key_returns_none(self):
        """Post-filter is deprecated; all filtering is now at the DB level."""
        doc_ref = WhereClauseReferenceModel(docbaseName="doc-001")
        pf = get_metadata_post_filter(state_where=[doc_ref])
        assert pf is None

    def test_merged_state_and_query_returns_none(self):
        """Post-filter is deprecated; all filtering is now at the DB level."""
        ws_ref = WhereClauseReferenceModel(workspace_id="ws1")
        doc_ref = WhereClauseReferenceModel(docbaseName="mydb")
        pf = get_metadata_post_filter(
            state_where=[ws_ref],
            query_where=[doc_ref],
        )
        assert pf is None


class TestPrepareContextFromChunks:
    """Tests for prepare_context_from_chunks function."""

    @staticmethod
    def _result_item(
        chunk_id: str,
        document_id: str,
        workspace_id: str,
        text: str,
        distance: float | None,
        start_index: int | None,
    ):
        doc = Document(
            page_content=text,
            id=chunk_id,
            metadata={
                "chunk_id": chunk_id,
                "document_id": document_id,
                "workspace_id": workspace_id,
                "start_index": start_index,
            },
        )
        return (doc, distance)

    def test_empty_chunks_list(self):
        """Test with empty chunks list."""
        result = prepare_context_from_chunks([])
        assert result == []

    def test_single_chunk(self):
        """Test with a single chunk."""
        result = prepare_context_from_chunks([self._result_item("chunk-1", "doc-1", "ws-1", "Test content", 0.5, 0)])
        assert len(result) == 1
        assert result[0].document_id == "doc-1"
        assert len(result[0].chunks) == 1
        assert result[0].chunks[0].text == "Test content"
        assert result[0].chunks[0].distance == 0.5

    def test_multiple_chunks_same_document(self):
        """Test multiple chunks from same document are preserved individually."""
        result = prepare_context_from_chunks(
            [
                self._result_item("chunk-1", "doc-1", "ws-1", "First chunk", 0.5, 0),
                self._result_item("chunk-2", "doc-1", "ws-1", "Second chunk", 0.6, 100),
            ]
        )

        assert len(result) == 1
        assert result[0].document_id == "doc-1"
        assert [chunk.chunk_id for chunk in result[0].chunks] == ["chunk-1", "chunk-2"]

    def test_chunks_sorted_by_start_index(self):
        """Test chunk order is preserved as provided."""
        result = prepare_context_from_chunks(
            [
                self._result_item("chunk-1", "doc-1", "ws-1", "Second", 0.5, 100),
                self._result_item("chunk-2", "doc-1", "ws-1", "First", 0.6, 0),
            ]
        )

        assert [chunk.text for chunk in result[0].chunks] == ["Second", "First"]

    def test_multiple_documents(self):
        """Test chunks from different documents remain separate."""
        result = prepare_context_from_chunks(
            [
                self._result_item("chunk-1", "doc-1", "ws-1", "Doc 1 content", 0.5, 0),
                self._result_item("chunk-2", "doc-2", "ws-1", "Doc 2 content", 0.3, 0),
            ]
        )

        assert len(result) == 2
        # Maintains insertion order (order of first chunk from each document)
        assert result[0].document_id == "doc-1"
        assert result[1].document_id == "doc-2"

    def test_combined_chunks_sorted_by_distance(self):
        """Test chunks maintain insertion order and original distances."""
        result = prepare_context_from_chunks(
            [
                self._result_item("chunk-1", "doc-1", "ws-1", "Content 1", 0.8, 0),
                self._result_item("chunk-2", "doc-2", "ws-1", "Content 2", 0.3, 0),
            ]
        )

        # Maintains insertion order
        assert result[0].document_id == "doc-1"
        assert result[1].document_id == "doc-2"
        assert result[0].chunks[0].distance == 0.8
        assert result[1].chunks[0].distance == 0.3

    def test_chunks_with_none_distance(self):
        """Test chunks with None distance are preserved."""
        result = prepare_context_from_chunks(
            [
                self._result_item("chunk-1", "doc-1", "ws-1", "Content", None, 0),
                self._result_item("chunk-2", "doc-1", "ws-1", "More content", 0.5, 100),
            ]
        )

        assert len(result) == 1
        assert result[0].chunks[0].distance is None
        assert result[0].chunks[1].distance == 0.5

    def test_chunks_with_none_start_index(self):
        """Test chunks with None start_index are preserved."""
        result = prepare_context_from_chunks(
            [
                self._result_item("chunk-1", "doc-1", "ws-1", "Content 1", 0.5, None),
                self._result_item("chunk-2", "doc-1", "ws-1", "Content 2", 0.6, 100),
            ]
        )

        assert len(result) == 1
        assert result[0].chunks[0].start_index is None
        assert result[0].chunks[1].start_index == 100

    def test_chunk_ids_combined(self):
        """Test chunk IDs are not combined."""
        result = prepare_context_from_chunks(
            [
                self._result_item("chunk-1", "doc-1", "ws-1", "Content 1", 0.5, 0),
                self._result_item("chunk-2", "doc-1", "ws-1", "Content 2", 0.6, 100),
                self._result_item("chunk-3", "doc-1", "ws-1", "Content 3", 0.7, 200),
            ]
        )

        assert len(result) == 1
        assert [chunk.chunk_id for chunk in result[0].chunks] == ["chunk-1", "chunk-2", "chunk-3"]

    def test_different_workspaces_different_groups(self):
        """Test that chunks with different workspace_ids remain separate."""
        result = prepare_context_from_chunks(
            [
                self._result_item("chunk-1", "doc-1", "ws-1", "Workspace 1", 0.5, 0),
                self._result_item("chunk-2", "doc-1", "ws-2", "Workspace 2", 0.6, 0),
            ]
        )

        assert len(result) == 2


class TestApiRaqQuery:
    """Tests for api_rag_query function."""

    @pytest.mark.anyio
    async def test_api_query_with_results(self):
        """Test API query that returns results."""
        mock_doc = Document(
            page_content="Test content",
            id="chunk-1",
            metadata={
                "chunk_id": "chunk-1",
                "document_id": "doc-1",
                "workspace_id": "ws-1",
                "start_index": 0,
            },
        )

        mock_store = AsyncMock()
        mock_store.asimilarity_search_with_relevance_scores.return_value = [(mock_doc, 0.9)]

        with patch("aviator.tools.rag.vector_store.get_adapter", return_value=mock_store):
            context = ContextRequestModel(query="test query", num_results=10, metadata=[])
            result = await api_rag_query(context)

            assert len(result) == 1
            assert result[0].document_id == "doc-1"
            assert len(result[0].chunks) == 1
            assert result[0].chunks[0].text == "Test content"

    @pytest.mark.anyio
    async def test_api_query_with_no_results(self):
        """Test API query that returns no results."""
        mock_store = AsyncMock()
        mock_store.asimilarity_search_with_relevance_scores.return_value = []

        with patch("aviator.tools.rag.vector_store.get_adapter", return_value=mock_store):
            context = ContextRequestModel(query="test query", num_results=10, metadata=[])
            result = await api_rag_query(context)

            assert result == []

    @pytest.mark.anyio
    async def test_api_query_with_document_filter(self):
        """Test API query with document filter."""
        doc_ref = WhereClauseReferenceModel(document_id="doc-123")
        mock_store = AsyncMock()
        mock_store.asimilarity_search_with_relevance_scores.return_value = []

        with patch("aviator.tools.rag.vector_store.get_adapter", return_value=mock_store):
            context = ContextRequestModel(query="test query", num_results=10, metadata=[doc_ref])
            await api_rag_query(context)

            # Verify the filter was passed correctly
            call_kwargs = mock_store.asimilarity_search_with_relevance_scores.call_args.kwargs
            assert "metadata_filter" in call_kwargs
            assert "document_id" in call_kwargs["metadata_filter"]

    @pytest.mark.anyio
    async def test_api_query_uses_threshold(self):
        """Test that API query uses the threshold from context."""
        mock_store = AsyncMock()
        mock_store.asimilarity_search_with_relevance_scores.return_value = []

        with patch("aviator.tools.rag.vector_store.get_adapter", return_value=mock_store):
            context = ContextRequestModel(query="test query", num_results=10, metadata=[], threshold=0.7)
            await api_rag_query(context)

            call_kwargs = mock_store.asimilarity_search_with_relevance_scores.call_args.kwargs
            assert call_kwargs["score_threshold"] == 0.7

    @pytest.mark.anyio
    async def test_api_query_uses_num_results_as_limit(self):
        """Test API query uses num_results from the context as the search limit."""
        mock_store = AsyncMock()
        mock_store.asimilarity_search_with_relevance_scores.return_value = []

        with patch("aviator.tools.rag.vector_store.get_adapter", return_value=mock_store):
            context = ContextRequestModel(query="list all items", num_results=10, metadata=[])
            await api_rag_query(context)

        call_kwargs = mock_store.asimilarity_search_with_relevance_scores.call_args.kwargs
        assert call_kwargs["k"] == 10


class TestRagQuery:
    """Tests for rag_query tool function."""

    @pytest.mark.anyio
    async def test_rag_query_basic(self):
        """Test basic RAG query execution."""
        mock_doc = Document(
            page_content="Test content",
            id="chunk-1",
            metadata={
                "chunk_id": "chunk-1",
                "document_id": "doc-1",
                "workspace_id": "ws-1",
                "start_index": 0,
            },
        )

        mock_store = AsyncMock()
        mock_store.asimilarity_search_with_relevance_scores.return_value = [(mock_doc, 0.9)]

        state = StateModel(messages=[], where=[], user=None)

        with (
            patch("aviator.tools.rag.vector_store.get_adapter", return_value=mock_store),
            patch("aviator.tools.rag.get_stream_writer"),
        ):
            result = await rag_query.ainvoke({"query": {"search_string": "test query"}, "state": state})

            # rag_query returns JSON string
            assert isinstance(result, str)
            documents = json.loads(result)
            assert len(documents) == 1
            assert documents[0]["DOCUMENT_ID"] == "doc-1"
            assert documents[0]["WORKSPACE_ID"] == "ws-1"
            assert len(documents[0]["chunks"]) == 1
            # Check fields using their aliases from Chunk model
            assert documents[0]["chunks"][0]["CHUNK_ID"] == "chunk-1"
            assert documents[0]["chunks"][0]["CHUNK_CONTENT"] == "Test content"

    @pytest.mark.anyio
    async def test_rag_query_list_query_uses_rag_list_query_limit(self):
        """Test tool list queries use the computed list-query limit."""
        mock_store = AsyncMock()
        mock_store.asimilarity_search_with_relevance_scores.return_value = []

        state = StateModel(messages=[], where=[], user=None)

        with (
            patch("aviator.tools.rag.vector_store.get_adapter", return_value=mock_store),
            patch("aviator.tools.rag.get_stream_writer"),
            patch("aviator.tools.rag.is_list_query", new=AsyncMock(return_value=True)),
        ):
            await rag_query.ainvoke({"query": {"search_string": "list all items"}, "state": state})

        call_kwargs = mock_store.asimilarity_search_with_relevance_scores.call_args.kwargs
        assert call_kwargs["k"] == settings.rag_list_query_limit

    @pytest.mark.anyio
    async def test_rag_query_list_query_splits_limit_across_rag_tool_calls(self):
        """Test list-query limit is split across sibling rag_query tool calls."""
        mock_store = AsyncMock()
        mock_store.asimilarity_search_with_relevance_scores.return_value = []

        tool_calls = [
            {"id": "call-1", "name": "rag_query", "args": {"search_string": "first"}, "type": "tool_call"},
            {"id": "call-2", "name": "rag_query", "args": {"search_string": "second"}, "type": "tool_call"},
            {"id": "call-3", "name": "current_time", "args": {}, "type": "tool_call"},
        ]
        state = StateModel(messages=[AIMessage(content="", tool_calls=tool_calls)], where=[], user=None)

        with (
            patch("aviator.tools.rag.vector_store.get_adapter", return_value=mock_store),
            patch("aviator.tools.rag.get_stream_writer"),
            patch("aviator.tools.rag.is_list_query", new=AsyncMock(return_value=True)),
        ):
            await rag_query.ainvoke({"query": {"search_string": "list all items"}, "state": state})

        expected_limit = max(settings.rag_default_limit, settings.rag_list_query_limit // 2)
        call_kwargs = mock_store.asimilarity_search_with_relevance_scores.call_args.kwargs
        assert call_kwargs["k"] == expected_limit


class TestRagToolCallCountFromState:
    """Tests for counting rag_query tool calls from state."""

    def test_count_from_latest_assistant_tool_call_message(self):
        """Count only rag_query calls from the latest assistant tool-call batch."""
        older = AIMessage(
            content="",
            tool_calls=[
                {"id": "old-1", "name": "rag_query", "args": {}, "type": "tool_call"},
            ],
        )
        latest = AIMessage(
            content="",
            tool_calls=[
                {"id": "new-1", "name": "rag_query", "args": {}, "type": "tool_call"},
                {"id": "new-2", "name": "rag_query", "args": {}, "type": "tool_call"},
                {"id": "new-3", "name": "current_time", "args": {}, "type": "tool_call"},
            ],
        )
        state = StateModel(messages=[older, latest], where=[], user=None)

        assert _get_rag_tool_call_count_from_state(state) == 2

    def test_count_falls_back_to_one_when_no_tool_calls(self):
        """Fallback to one to keep backward-compatible limit behavior."""
        state = StateModel(messages=[], where=[], user=None)
        assert _get_rag_tool_call_count_from_state(state) == 1


class TestMetadataRetrieval:
    """Tests for document-level metadata retrieval."""

    @pytest.mark.anyio
    async def test_get_doc_level_metadata_disabled(self, monkeypatch):
        """Test that metadata fetching can be disabled via settings."""

        monkeypatch.setattr(settings, "add_doc_metadata_to_context", False)

        chunk = ContextDocumentModel(
            document_id="doc-1",
            workspace_id="ws-1",
            chunks=[Chunk(chunk_id="chunk-1", text="Content")],
        )

        result = await get_doc_level_metadata([chunk], None)

        assert result == {}

    @pytest.mark.anyio
    async def test_get_doc_level_metadata_no_content_system(self, monkeypatch):
        """Test that metadata fetch is skipped when content_system is None."""

        monkeypatch.setattr(settings, "add_doc_metadata_to_context", True)
        monkeypatch.setattr(settings, "content_system", None)

        chunk = ContextDocumentModel(
            document_id="doc-1",
            workspace_id="ws-1",
            chunks=[Chunk(chunk_id="chunk-1", text="Content")],
        )

        result = await get_doc_level_metadata([chunk], None)

        assert result == {}

    @pytest.mark.anyio
    async def test_get_doc_level_metadata_empty_chunks(self, monkeypatch):
        """Test metadata retrieval with empty chunks list."""

        monkeypatch.setattr(settings, "add_doc_metadata_to_context", True)
        monkeypatch.setattr(settings, "content_system", "test_system")

        result = await get_doc_level_metadata([], None)

        assert result == {}

    @pytest.mark.anyio
    async def test_get_doc_level_metadata_successful(self, monkeypatch):
        """Test successful metadata retrieval from plugin."""

        monkeypatch.setattr(settings, "add_doc_metadata_to_context", True)
        monkeypatch.setattr(settings, "content_system", "test_system")

        chunk1 = ContextDocumentModel(
            document_id="doc-1",
            workspace_id="ws-1",
            chunks=[Chunk(chunk_id="chunk-1", text="Content 1")],
        )
        chunk2 = ContextDocumentModel(
            document_id="doc-2",
            workspace_id="ws-1",
            chunks=[Chunk(chunk_id="chunk-2", text="Content 2")],
        )

        mock_metadata = {
            "doc-1": {"title": "Document 1", "author": "John"},
            "doc-2": {"title": "Document 2", "author": "Jane"},
        }

        def mock_metadata_retriever(user, doc_ids):
            return mock_metadata

        with patch(
            "aviator.tools.rag.get_rag_metadata_retriever",
            return_value=mock_metadata_retriever,
        ):
            result = await get_doc_level_metadata([chunk1, chunk2], None)

        assert len(result) == 2
        assert result["doc-1"]["title"] == "Document 1"
        assert result["doc-2"]["title"] == "Document 2"

    @pytest.mark.anyio
    async def test_get_doc_level_metadata_plugin_error(self, monkeypatch):
        """Test handling of plugin errors during metadata retrieval."""

        monkeypatch.setattr(settings, "add_doc_metadata_to_context", True)
        monkeypatch.setattr(settings, "content_system", "test_system")

        chunk = ContextDocumentModel(
            document_id="doc-1",
            workspace_id="ws-1",
            chunks=[Chunk(chunk_id="chunk-1", text="Content")],
        )

        def mock_metadata_retriever_error(user, doc_ids):
            raise Exception("Plugin error")

        with patch(
            "aviator.tools.rag.get_rag_metadata_retriever",
            return_value=mock_metadata_retriever_error,
        ):
            result = await get_doc_level_metadata([chunk], None)

        assert result == {}

    @pytest.mark.anyio
    async def test_get_doc_level_metadata_invalid_response(self, monkeypatch):
        """Test handling of invalid metadata response from plugin."""

        monkeypatch.setattr(settings, "add_doc_metadata_to_context", True)
        monkeypatch.setattr(settings, "content_system", "test_system")

        chunk = ContextDocumentModel(
            document_id="doc-1",
            workspace_id="ws-1",
            chunks=[Chunk(chunk_id="chunk-1", text="Content")],
        )

        def mock_metadata_retriever_invalid(user, doc_ids):
            return "not a dict"  # Invalid response

        with patch(
            "aviator.tools.rag.get_rag_metadata_retriever",
            return_value=mock_metadata_retriever_invalid,
        ):
            result = await get_doc_level_metadata([chunk], None)

        assert result == {}


class TestAttachMetadata:
    """Tests for attaching metadata to chunks."""

    def test_attach_metadata_empty_map(self):
        """Test attaching empty metadata map returns original chunks."""

        chunk = ContextDocumentModel(
            document_id="doc-1",
            workspace_id="ws-1",
            chunks=[Chunk(chunk_id="chunk-1", text="Content")],
        )

        result = attach_metadata_to_chunks([chunk], {})

        assert len(result) == 1
        assert result[0].metadata is None

    def test_attach_metadata_single_chunk(self):
        """Test attaching metadata to single chunk."""

        chunk = ContextDocumentModel(
            document_id="doc-1",
            workspace_id="ws-1",
            chunks=[Chunk(chunk_id="chunk-1", text="Content")],
        )

        metadata_map = {"doc-1": {"title": "Document 1", "author": "John"}}

        result = attach_metadata_to_chunks([chunk], metadata_map)

        assert len(result) == 1
        assert result[0].metadata is not None
        assert result[0].metadata["title"] == "Document 1"
        assert result[0].metadata["author"] == "John"

    def test_attach_metadata_multiple_chunks_same_doc(self):
        """Test attaching same metadata to multiple chunks from same document."""

        chunk1 = ContextDocumentModel(
            document_id="doc-1",
            workspace_id="ws-1",
            chunks=[Chunk(chunk_id="chunk-1", text="Content 1")],
        )
        chunk2 = ContextDocumentModel(
            document_id="doc-1",
            workspace_id="ws-1",
            chunks=[Chunk(chunk_id="chunk-2", text="Content 2")],
        )

        metadata_map = {"doc-1": {"title": "Document 1", "type": "PDF"}}

        result = attach_metadata_to_chunks([chunk1, chunk2], metadata_map)

        assert len(result) == 2
        assert result[0].metadata == result[1].metadata
        assert result[0].metadata["title"] == "Document 1"

    def test_attach_metadata_partial_match(self):
        """Test attaching metadata when only some chunks have matching docs."""

        chunk1 = ContextDocumentModel(
            document_id="doc-1",
            workspace_id="ws-1",
            chunks=[Chunk(chunk_id="chunk-1", text="Content 1")],
        )
        chunk2 = ContextDocumentModel(
            document_id="doc-2",
            workspace_id="ws-1",
            chunks=[Chunk(chunk_id="chunk-2", text="Content 2")],
        )

        metadata_map = {"doc-1": {"title": "Document 1"}}

        result = attach_metadata_to_chunks([chunk1, chunk2], metadata_map)

        assert len(result) == 2
        assert result[0].metadata is not None
        assert result[0].metadata["title"] == "Document 1"
        assert result[1].metadata is None

    def test_attach_metadata_preserves_other_fields(self):
        """Test that attaching metadata preserves other chunk fields."""

        chunk = ContextDocumentModel(
            document_id="doc-1",
            workspace_id="ws-1",
            chunks=[Chunk(chunk_id="chunk-1", text="Original content", distance=0.95, start_index=100)],
        )

        metadata_map = {"doc-1": {"title": "Document 1"}}

        result = attach_metadata_to_chunks([chunk], metadata_map)

        assert result[0].chunks[0].chunk_id == "chunk-1"
        assert result[0].chunks[0].text == "Original content"
        assert result[0].chunks[0].distance == 0.95
        assert result[0].chunks[0].start_index == 100
        assert result[0].metadata["title"] == "Document 1"

    @pytest.mark.anyio
    async def test_rag_query_with_string_filter(self):
        """Test RAG query with string filter (AMS compatibility)."""
        mock_store = AsyncMock()
        mock_store.asimilarity_search_with_relevance_scores.return_value = []

        state = StateModel(messages=[], where=[], user=None)
        filter_str = json.dumps([{"document_id": "doc-123"}])

        with (
            patch("aviator.tools.rag.vector_store.get_adapter", return_value=mock_store),
            patch("aviator.tools.rag.get_stream_writer"),
        ):
            result = await rag_query.ainvoke({"query": {"search_string": "test", "filter": filter_str}, "state": state})

            # rag_query returns JSON string
            assert isinstance(result, str)
            chunks = json.loads(result)
            assert isinstance(chunks, list)

    @pytest.mark.anyio
    async def test_rag_query_uses_settings_limit(self):
        """Test RAG query uses limit from settings."""
        mock_store = AsyncMock()
        mock_store.asimilarity_search_with_relevance_scores.return_value = []

        state = StateModel(messages=[], where=[], user=None)

        with (
            patch("aviator.tools.rag.vector_store.get_adapter", return_value=mock_store),
            patch("aviator.tools.rag.get_stream_writer"),
        ):
            _result = await rag_query.ainvoke({"query": {"search_string": "test", "filter": []}, "state": state})

            call_kwargs = mock_store.asimilarity_search_with_relevance_scores.call_args.kwargs
            assert call_kwargs["k"] == settings.rag_default_limit

    @pytest.mark.anyio
    async def test_rag_query_with_invalid_string_filter(self):
        """Test RAG query with invalid JSON string filter."""
        mock_store = AsyncMock()
        mock_store.asimilarity_search_with_relevance_scores.return_value = []

        state = StateModel(messages=[], where=[], user=None)

        with (
            patch("aviator.tools.rag.vector_store.get_adapter", return_value=mock_store),
            patch("aviator.tools.rag.get_stream_writer"),
        ):
            result = await rag_query.ainvoke(
                {"query": {"search_string": "test", "filter": "invalid json"}, "state": state}
            )

            # Should fallback to empty filter
            # rag_query returns JSON string
            assert isinstance(result, str)
            chunks = json.loads(result)
            assert isinstance(chunks, list)

    @pytest.mark.anyio
    async def test_rag_query_ignores_extra_fields(self):
        """Test RAG query ignores extra fields like 'limit' if passed."""
        mock_store = AsyncMock()
        mock_store.asimilarity_search_with_relevance_scores.return_value = []

        state = StateModel(messages=[], where=[], user=None)

        with (
            patch("aviator.tools.rag.vector_store.get_adapter", return_value=mock_store),
            patch("aviator.tools.rag.get_stream_writer"),
        ):
            _result = await rag_query.ainvoke(
                {"query": {"search_string": "test", "filter": [], "extra_field": "ignored"}, "state": state}
            )

            # Should still work and use settings limit
            call_kwargs = mock_store.asimilarity_search_with_relevance_scores.call_args.kwargs
            assert call_kwargs["k"] == settings.rag_default_limit

    @pytest.mark.anyio
    async def test_rag_query_with_none_filter(self):
        """Test RAG query with None filter."""
        mock_store = AsyncMock()
        mock_store.asimilarity_search_with_relevance_scores.return_value = []

        state = StateModel(messages=[], where=[], user=None)

        with (
            patch("aviator.tools.rag.vector_store.get_adapter", return_value=mock_store),
            patch("aviator.tools.rag.get_stream_writer"),
        ):
            result = await rag_query.ainvoke({"query": {"search_string": "test", "filter": None}, "state": state})

            # rag_query returns JSON string
            assert isinstance(result, str)
            chunks = json.loads(result)
            assert isinstance(chunks, list)


class TestRagQueryWithModel:
    """Tests for rag_query tool function with RAGQueryModel parameters."""

    @pytest.mark.anyio
    async def test_rag_query_with_model_basic(self):
        """Test RAG query execution with model parameters."""
        mock_doc = Document(
            page_content="Test content",
            id="chunk-1",
            metadata={
                "chunk_id": "chunk-1",
                "document_id": "doc-1",
                "workspace_id": "ws-1",
                "start_index": 0,
            },
        )

        mock_store = AsyncMock()
        mock_store.asimilarity_search_with_relevance_scores.return_value = [(mock_doc, 0.9)]

        state = StateModel(messages=[], where=[], user=None)

        with (
            patch("aviator.tools.rag.vector_store.get_adapter", return_value=mock_store),
            patch("aviator.tools.rag.get_stream_writer"),
        ):
            result = await rag_query.ainvoke({"query": {"search_string": "test query", "filter": []}, "state": state})

            # rag_query returns JSON string
            assert isinstance(result, str)
            documents = json.loads(result)
            assert len(documents) == 1
            assert documents[0]["DOCUMENT_ID"] == "doc-1"
            assert documents[0]["WORKSPACE_ID"] == "ws-1"
            assert len(documents[0]["chunks"]) == 1
            # Check fields using their aliases from Chunk model
            assert documents[0]["chunks"][0]["CHUNK_ID"] == "chunk-1"
            assert documents[0]["chunks"][0]["CHUNK_CONTENT"] == "Test content"

    @pytest.mark.anyio
    async def test_rag_query_with_model_with_filter(self):
        """Test RAG query with document filter."""
        doc_ref = WhereClauseReferenceModel(document_id="doc-123")
        mock_store = AsyncMock()
        mock_store.asimilarity_search_with_relevance_scores.return_value = []

        state = StateModel(messages=[], where=[doc_ref], user=None)

        with (
            patch("aviator.tools.rag.vector_store.get_adapter", return_value=mock_store),
            patch("aviator.tools.rag.get_stream_writer"),
        ):
            result = await rag_query.ainvoke(
                {"query": {"search_string": "test query", "filter": [doc_ref]}, "state": state}
            )

            # rag_query returns JSON string
            assert isinstance(result, str)
            chunks = json.loads(result)
            assert isinstance(chunks, list)

    @pytest.mark.anyio
    async def test_rag_query_uses_default_settings_limit(self):
        """Test RAG query uses the default limit from settings."""
        mock_store = AsyncMock()
        mock_store.asimilarity_search_with_relevance_scores.return_value = []

        state = StateModel(messages=[], where=[], user=None)

        with (
            patch("aviator.tools.rag.vector_store.get_adapter", return_value=mock_store),
            patch("aviator.tools.rag.get_stream_writer"),
        ):
            await rag_query.ainvoke({"query": {"search_string": "test query", "filter": []}, "state": state})

            call_kwargs = mock_store.asimilarity_search_with_relevance_scores.call_args.kwargs
            assert call_kwargs["k"] == settings.rag_default_limit


class TestPermissionFiltering:
    """Tests for permission filtering in RAG query."""

    @pytest.mark.anyio
    async def test_permission_filter_applied(self, monkeypatch):
        """Test that permission filter is applied when content_system is set."""
        mock_doc = Document(
            page_content="Test content",
            id="chunk-1",
            metadata={
                "chunk_id": "chunk-1",
                "document_id": "doc-1",
                "workspace_id": "ws-1",
                "start_index": 0,
            },
        )

        mock_store = AsyncMock()
        mock_store.asimilarity_search_with_relevance_scores.return_value = [(mock_doc, 0.9)]

        # Mock permission filter
        def mock_perm_filter(chunks, user):
            return []  # Filter out all chunks

        monkeypatch.setattr(settings, "content_system", "test_system")

        with (
            patch("aviator.tools.rag.vector_store.get_adapter", return_value=mock_store),
            patch(
                "aviator.services.permissions.load_rag_permission_filters",
                return_value=[("test_system", mock_perm_filter)],
            ),
            patch("aviator.tools.rag.get_stream_writer"),
        ):
            state = StateModel(messages=[], where=[], user={"id": "user-1"})

            result = await rag_query.ainvoke({"query": {"search_string": "test query", "filter": []}, "state": state})

            # Permission filter should have filtered out all results
            # rag_query returns JSON string
            assert isinstance(result, str)
            chunks = json.loads(result)
            assert len(chunks) == 0

    @pytest.mark.anyio
    async def test_no_permission_filter_when_content_system_none(self, monkeypatch):
        """Test that no permission filter is applied when content_system is None."""
        mock_doc = Document(
            page_content="Test content",
            id="chunk-1",
            metadata={
                "chunk_id": "chunk-1",
                "document_id": "doc-1",
                "workspace_id": "ws-1",
                "start_index": 0,
            },
        )

        mock_store = AsyncMock()
        mock_store.asimilarity_search_with_relevance_scores.return_value = [(mock_doc, 0.9)]

        monkeypatch.setattr(settings, "content_system", None)

        with (
            patch("aviator.tools.rag.vector_store.get_adapter", return_value=mock_store),
            patch("aviator.tools.rag.get_stream_writer"),
        ):
            state = StateModel(messages=[], where=[], user=None)

            result = await rag_query.ainvoke({"query": {"search_string": "test query", "filter": []}, "state": state})

            # No filtering should occur
            # rag_query returns JSON string
            assert isinstance(result, str)
            chunks = json.loads(result)
            assert len(chunks) == 1

    @pytest.mark.anyio
    async def test_permission_filter_missing_for_configured_content_system_raises_403(self, monkeypatch):
        """Configured content system without matching permission filter should raise 403."""
        from fastapi import HTTPException

        mock_doc = Document(
            page_content="Test content",
            id="chunk-1",
            metadata={
                "chunk_id": "chunk-1",
                "document_id": "doc-1",
                "workspace_id": "ws-1",
                "start_index": 0,
            },
        )

        mock_store = AsyncMock()
        mock_store.asimilarity_search_with_relevance_scores.return_value = [(mock_doc, 0.9)]

        monkeypatch.setattr(settings, "content_system", "test_system")

        with (
            patch("aviator.tools.rag.vector_store.get_adapter", return_value=mock_store),
            patch("aviator.services.permissions.load_rag_permission_filters", return_value=[]),
            patch("aviator.tools.rag.get_stream_writer"),
        ):
            state = StateModel(messages=[], where=[], user={"id": "user-1"})

            with pytest.raises(HTTPException) as exc_info:
                await rag_query.ainvoke({"query": {"search_string": "test query", "filter": []}, "state": state})
            assert exc_info.value.status_code == 403

    @pytest.mark.anyio
    async def test_permission_filter_exception_for_configured_content_system_returns_empty(self, monkeypatch):
        """Permission filter failures should fail closed when content_system is configured."""
        mock_doc = Document(
            page_content="Test content",
            id="chunk-1",
            metadata={
                "chunk_id": "chunk-1",
                "document_id": "doc-1",
                "workspace_id": "ws-1",
                "start_index": 0,
            },
        )

        mock_store = AsyncMock()
        mock_store.asimilarity_search_with_relevance_scores.return_value = [(mock_doc, 0.9)]

        def failing_perm_filter(chunks, user):
            raise RuntimeError("permission backend unavailable")

        monkeypatch.setattr(settings, "content_system", "test_system")

        with (
            patch("aviator.tools.rag.vector_store.get_adapter", return_value=mock_store),
            patch(
                "aviator.services.permissions.load_rag_permission_filters",
                return_value=[("test_system", failing_perm_filter)],
            ),
            patch("aviator.tools.rag.get_stream_writer"),
        ):
            state = StateModel(messages=[], where=[], user={"id": "user-1"})

            result = await rag_query.ainvoke({"query": {"search_string": "test query", "filter": []}, "state": state})

            assert isinstance(result, str)
            chunks = json.loads(result)
            assert len(chunks) == 0
