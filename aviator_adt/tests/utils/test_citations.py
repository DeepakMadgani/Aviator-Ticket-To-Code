"""Tests for citation extraction utilities."""

from langchain.messages import ToolMessage

from aviator.models import Chunk, ContextDocumentModel
from aviator.utils.citations import (
    extract_chunk_ids_from_text,
    extract_chunks_from_tool_messages,
    replace_chunk_ids_with_citation_numbers,
)


class TestExtractChunkIdsFromText:
    """Tests for extract_chunk_ids_from_text function."""

    def test_extract_single_citation(self):
        """Test extracting a single chunk ID."""
        text = "This is some information [chunk-123]."
        result = extract_chunk_ids_from_text(text)
        assert result == ["chunk-123"]

    def test_extract_multiple_citations(self):
        """Test extracting multiple chunk IDs."""
        text = "First fact [chunk-abc]. Second fact [chunk-def]."
        result = extract_chunk_ids_from_text(text)
        assert result == ["chunk-abc", "chunk-def"]

    def test_extract_duplicate_citations(self):
        """Test that duplicates are removed."""
        text = "Fact one [chunk-123]. Fact two also from [chunk-123]."
        result = extract_chunk_ids_from_text(text)
        assert result == ["chunk-123"]

    def test_skip_markdown_links(self):
        """Test that markdown link text is not extracted as chunk ID."""
        text = "See [link text](http://example.com) and [chunk-abc-123]."
        result = extract_chunk_ids_from_text(text)
        # "link text" should be skipped as it's very short or common
        assert "chunk-abc-123" in result

    def test_skip_footnotes(self):
        """Test that footnote markers are skipped."""
        text = "Some text[^1] and [chunk-abc]."
        result = extract_chunk_ids_from_text(text)
        assert result == ["chunk-abc"]

    def test_skip_numbers(self):
        """Test that simple numbers are skipped."""
        text = "Reference [1] and [chunk-abc]."
        result = extract_chunk_ids_from_text(text)
        assert result == ["chunk-abc"]

    def test_empty_text(self):
        """Test with empty text."""
        assert extract_chunk_ids_from_text("") == []
        assert extract_chunk_ids_from_text(None) == []

    def test_no_citations(self):
        """Test text without any citations."""
        text = "This is plain text without any citations."
        result = extract_chunk_ids_from_text(text)
        assert result == []


class TestExtractChunksFromToolMessages:
    """Tests for extract_chunks_from_tool_messages function."""

    def test_extract_from_dict_artifact(self):
        """Test extraction when artifact contains dicts (common case)."""
        # Simulate what LangGraph actually stores (dict, not object)
        artifact = [
            {
                "DOCUMENT_ID": "doc1",
                "WORKSPACE_ID": "ws1",
                "metadata": {"title": "Test Doc"},
                "chunks": [
                    {"CHUNK_ID": "c1", "text": "content1", "distance": 0.1, "start_index": 0},
                    {"CHUNK_ID": "c2", "text": "content2", "distance": 0.2, "start_index": 10},
                ],
            }
        ]

        tool_msg = ToolMessage(
            content="[RAG result]",  # Content is string representation
            artifact=artifact,  # Artifact contains actual data (dicts)
            tool_call_id="test-123",
            name="rag_query",
        )

        chunk_map, doc_groups = extract_chunks_from_tool_messages([tool_msg])

        assert len(chunk_map) == 2
        assert "c1" in chunk_map
        assert "c2" in chunk_map
        assert chunk_map["c1"].document_id == "doc1"
        assert chunk_map["c1"].workspace_id == "ws1"
        assert chunk_map["c1"].chunk.text == "content1"

        assert len(doc_groups) == 1
        assert doc_groups[0].document_id == "doc1"
        assert len(doc_groups[0].chunks) == 2

    def test_extract_from_object_artifact(self):
        """Test extraction when artifact contains ContextDocumentModel objects."""
        doc = ContextDocumentModel(
            document_id="doc2",
            workspace_id="ws2",
            chunks=[
                Chunk(chunk_id="c3", text="content3", distance=0.3, start_index=0),
            ],
            metadata={"title": "Doc 2"},
        )

        tool_msg = ToolMessage(
            content="[RAG result]",
            artifact=[doc],  # Already a ContextDocumentModel object
            tool_call_id="test-456",
            name="rag_query",
        )

        chunk_map, doc_groups = extract_chunks_from_tool_messages([tool_msg])

        assert len(chunk_map) == 1
        assert "c3" in chunk_map
        assert chunk_map["c3"].document_id == "doc2"
        assert len(doc_groups) == 1

    def test_fallback_to_content_when_no_artifact(self):
        """Test fallback to parsing content when artifact is not available."""
        import json

        # Simulate older LangGraph or custom tool that doesn't set artifact
        doc_dict = {
            "DOCUMENT_ID": "doc3",
            "WORKSPACE_ID": "ws3",
            "metadata": None,
            "chunks": [
                {"CHUNK_ID": "c4", "text": "content4", "distance": 0.4, "start_index": 0},
            ],
        }

        tool_msg = ToolMessage(
            content=json.dumps([doc_dict]),  # Content has the JSON
            tool_call_id="test-789",
            name="rag_query",
        )
        # Note: artifact is not set (defaults to None)

        chunk_map, _doc_groups = extract_chunks_from_tool_messages([tool_msg])

        assert len(chunk_map) == 1
        assert "c4" in chunk_map
        assert chunk_map["c4"].document_id == "doc3"

    def test_skip_non_rag_tools(self):
        """Test that non-rag_query tools are skipped."""
        tool_msg = ToolMessage(
            content="some result",
            artifact=["data"],
            tool_call_id="test-999",
            name="other_tool",  # Not rag_query
        )

        chunk_map, doc_groups = extract_chunks_from_tool_messages([tool_msg])

        assert len(chunk_map) == 0
        assert len(doc_groups) == 0

    def test_handle_multiple_documents(self):
        """Test extraction across multiple documents."""
        artifact = [
            {
                "DOCUMENT_ID": "doc1",
                "WORKSPACE_ID": "ws1",
                "metadata": None,
                "chunks": [{"CHUNK_ID": "c1", "text": "text1", "distance": 0.1, "start_index": 0}],
            },
            {
                "DOCUMENT_ID": "doc2",
                "WORKSPACE_ID": "ws1",
                "metadata": None,
                "chunks": [{"CHUNK_ID": "c2", "text": "text2", "distance": 0.2, "start_index": 0}],
            },
        ]

        tool_msg = ToolMessage(
            content="[multi-doc result]",
            artifact=artifact,
            tool_call_id="test-multi",
            name="rag_query",
        )

        chunk_map, doc_groups = extract_chunks_from_tool_messages([tool_msg])

        assert len(chunk_map) == 2
        assert len(doc_groups) == 2
        assert doc_groups[0].document_id == "doc1"
        assert doc_groups[1].document_id == "doc2"


class TestBuildReferencesFromCitations:
    """Tests for build_references_from_citations function."""

    # TODO: Add tests for building references from citations


class TestReplaceChunkIdsWithCitationNumbers:
    """Tests for replacing inline chunk IDs with citation numbers."""

    def test_replace_chunk_ids_using_reference_models(self):
        """Replaces only chunk IDs that exist in the references payload."""
        from aviator import models

        references = [
            models.ReferenceModel(
                document_id="doc-1",
                workspace_id="ws-1",
                chunks=[
                    models.ReferenceChunkModel(chunk_id="chunk-a", citation=1, content="Alpha"),
                    models.ReferenceChunkModel(chunk_id="chunk-b", citation=2, content="Beta"),
                ],
            )
        ]

        result = replace_chunk_ids_with_citation_numbers(
            "Alpha fact [chunk-a]. Beta fact [chunk-b]. [unknown] stays.",
            references,
        )

        assert result == "Alpha fact [1]. Beta fact [2]. [unknown] stays."

    def test_replace_chunk_ids_using_reference_dicts(self):
        """Supports plain dict references as returned in serialized payloads."""
        references = [
            {
                "chunks": [
                    {"chunkID": "019cdb48-0292-7f90-ae82-3ee440d924db", "citation": 1, "content": "Alpha"},
                ]
            }
        ]

        result = replace_chunk_ids_with_citation_numbers(
            "Fact [019cdb48-0292-7f90-ae82-3ee440d924db].",
            references,
        )

        assert result == "Fact [1]."
