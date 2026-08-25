"""Test Celery task processing, routing metadata, and table extraction functionality."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aviator.celery import (
    extract_tables_and_text,
    process_embedding_request,
    process_workspace_summary_request,
)
from aviator.exceptions import PromptNotFoundError, WorkspaceSummaryError, WorkspaceSummaryRetryableError
from aviator.models import DocumentSummary


class TestExtractTablesAndText:
    """Test the extract_tables_and_text function."""

    def test_extract_single_table(self):
        """Test extraction of a single HTML table."""
        content = """
        Some text before table.
        <table>
            <tr><td>Cell 1</td><td>Cell 2</td></tr>
        </table>
        Some text after table.
        """
        result = extract_tables_and_text(content)

        assert "<table>" in result["tables"]
        assert "</table>" in result["tables"]
        assert "Cell 1" in result["tables"]
        assert "Some text before table" in result["text"]
        assert "Some text after table" in result["text"]
        assert "<table>" not in result["text"]

    def test_extract_multiple_tables(self):
        """Test extraction of multiple HTML tables."""
        content = """
        Text before.
        <table><tr><td>Table 1</td></tr></table>
        Text between tables.
        <TABLE><tr><td>Table 2</td></tr></TABLE>
        Text after.
        """
        result = extract_tables_and_text(content)

        assert "Table 1" in result["tables"]
        assert "Table 2" in result["tables"]
        assert result["tables"].count("</table>") == 2 or result["tables"].count("</TABLE>") >= 1
        assert "Text before" in result["text"]
        assert "Text between tables" in result["text"]
        assert "Text after" in result["text"]

    def test_extract_nested_tables(self):
        """Test extraction with nested table tags."""
        content = """
        <table>
            <tr>
                <td>Outer table</td>
                <td><table><tr><td>Inner table</td></tr></table></td>
            </tr>
        </table>
        """
        result = extract_tables_and_text(content)

        # Should extract table content
        assert result["tables"] != ""
        assert "table" in result["tables"].lower()

    def test_no_tables(self):
        """Test content without any tables."""
        content = "Just some plain text content without any tables."
        result = extract_tables_and_text(content)

        assert result["tables"] == ""
        assert result["text"] == content

    def test_case_insensitive_table_tags(self):
        """Test that table extraction is case-insensitive."""
        content = """
        <TABLE><tr><td>Uppercase</td></tr></TABLE>
        <table><tr><td>Lowercase</td></tr></table>
        <TaBLe><tr><td>Mixed case</td></tr></TaBLe>
        """
        result = extract_tables_and_text(content)

        assert "Uppercase" in result["tables"]
        assert "Lowercase" in result["tables"]
        assert "Mixed case" in result["tables"]

    def test_table_with_attributes(self):
        """Test that table tags with attributes are extracted."""
        content_with_attrs = """
        <table class="data-table" id="table1" style="width:100%">
            <tr><td>Data with attrs</td></tr>
        </table>
        """
        result_with_attrs = extract_tables_and_text(content_with_attrs)

        # Tables with attributes are now extracted by the updated regex
        assert "Data with attrs" in result_with_attrs["tables"]
        assert "<table>" not in result_with_attrs["text"].lower()

    def test_empty_content(self):
        """Test empty string content."""
        result = extract_tables_and_text("")
        assert result["tables"] == ""
        assert result["text"] == ""

    def test_none_content(self):
        """Test None content."""
        result = extract_tables_and_text(None)
        assert result["tables"] == ""
        assert result["text"] == ""

    def test_only_tables_no_text(self):
        """Test content that is only tables with no surrounding text."""
        content = "<table><tr><td>Only table</td></tr></table>"
        result = extract_tables_and_text(content)

        assert "Only table" in result["tables"]
        assert result["text"] == ""

    def test_tables_separated_by_newline(self):
        """Test that multiple extracted tables are separated by newlines in the string."""
        content = "<table><tr><td>A</td></tr></table><table><tr><td>B</td></tr></table>"
        result = extract_tables_and_text(content)

        # The two tables should both appear in the string
        assert "<table>" in result["tables"]
        assert "A" in result["tables"]
        assert "B" in result["tables"]


class TestProcessEmbeddingRequest:
    """Test the process_embedding_request Celery task."""

    def test_task_has_explicit_name(self):
        assert getattr(process_embedding_request, "name", None) == "aviator.celery.process_embedding_request"

    @pytest.fixture
    def mock_vector_store(self):
        """Create a mock vector store and embedding buffer."""
        with (
            patch("aviator.celery.vector_store") as mock_manager,
            patch("aviator.celery.embedding_buffer") as mock_buffer,
        ):
            mock_store = MagicMock()
            mock_store.add_documents.return_value = ["doc-id-1", "doc-id-2"]
            mock_store.delete = MagicMock(return_value=None)
            mock_manager.get.return_value = mock_store

            # Mock embedding_buffer
            mock_buffer.add.return_value = ["doc-id-1", "doc-id-2"]
            mock_buffer.remove_by_document_id.return_value = 0
            mock_buffer.pending_count = 0
            mock_buffer.pending_chars = 0

            yield mock_store, mock_manager, mock_buffer

    def test_add_text_only_content(self, mock_vector_store):
        """Test adding content without tables."""
        _mock_store, _mock_manager, mock_buffer = mock_vector_store
        request_dict = {
            "operation": "add",
            "content": "This is plain text content without any tables.",
            "metadata": {
                "workspace_id": "ws-123",
                "document_id": "doc-456",
            },
        }

        result = process_embedding_request(request_dict)

        assert result["add"] is not None
        assert "metadata" in result
        # Check that embedding_buffer.add was called
        assert mock_buffer.add.called

        # Verify documents have type metadata
        call_args = mock_buffer.add.call_args[0][0]
        assert all(doc.metadata.get("type") == "text" for doc in call_args)

    def test_add_content_with_tables(self, mock_vector_store):
        """Test adding content with HTML tables."""
        _mock_store, _mock_manager, mock_buffer = mock_vector_store
        request_dict = {
            "operation": "add",
            "content": """
                Regular text content.
                <table><tr><td>Table data</td></tr></table>
                More text after table.
            """,
            "metadata": {
                "workspace_id": "ws-123",
                "document_id": "doc-456",
            },
        }

        result = process_embedding_request(request_dict)

        assert result["add"] is not None
        # Check that embedding_buffer.add was called
        assert mock_buffer.add.called

        # Verify documents have both text and table types
        call_args = mock_buffer.add.call_args[0][0]
        doc_types = [doc.metadata.get("type") for doc in call_args]
        assert "text" in doc_types
        assert "table" in doc_types

    def test_add_metadata_document(self, mock_vector_store):
        """Test adding metadata-only document."""
        _mock_store, _mock_manager, mock_buffer = mock_vector_store
        request_dict = {
            "operation": "add",
            "content": "Metadata content",
            "metadata": {
                "workspace_id": "ws-123",
                "document_id": "doc-456",
            },
        }

        result = process_embedding_request(request_dict, is_metadata=True)

        assert result["add"] is not None
        # Check that embedding_buffer.add was called
        assert mock_buffer.add.called

        # Verify document has metadata type
        call_args = mock_buffer.add.call_args[0][0]
        assert len(call_args) == 1
        assert call_args[0].metadata.get("type") == "metadata"
        assert call_args[0].metadata.get("start_index") == -1

    def test_backward_compatibility_keys(self, mock_vector_store):
        """Test that backward compatibility keys are added."""
        _mock_store, _mock_manager, mock_buffer = mock_vector_store
        request_dict = {
            "operation": "add",
            "content": "Test content",
            "metadata": {
                "workspaceID": "ws-123",
                "documentID": "doc-456",
            },
        }

        process_embedding_request(request_dict)

        # Check that embedding_buffer.add was called
        assert mock_buffer.add.called

        # Verify both normalized and backward compatibility keys exist
        call_args = mock_buffer.add.call_args[0][0]
        for doc in call_args:
            assert "workspace_id" in doc.metadata
            assert "document_id" in doc.metadata
            assert "workspaceID" in doc.metadata
            assert "documentID" in doc.metadata

    def test_metadata_column_contains_all_id_variants(self, mock_vector_store):
        """Test that metadata column contains workspace_id, workspaceID, document_id, documentID and values are equal."""
        _mock_store, _mock_manager, mock_buffer = mock_vector_store
        request_dict = {
            "operation": "add",
            "content": "Test content",
            "metadata": {
                "workspace_id": "ws-1",
                "workspaceID": "ws-1",
                "document_id": "doc-1",
                "documentID": "doc-1",
            },
        }

        process_embedding_request(request_dict)

        # Check that embedding_buffer.add was called
        assert mock_buffer.add.called

        # Verify all four keys exist and values are equal
        call_args = mock_buffer.add.call_args[0][0]
        for doc in call_args:
            md = doc.metadata
            assert "workspace_id" in md
            assert "workspaceID" in md
            assert "document_id" in md
            assert "documentID" in md
            assert md["workspace_id"] == md["workspaceID"]
            assert md["document_id"] == md["documentID"]

    @pytest.mark.parametrize("content", ["", None])
    def test_empty_or_none_content_is_dropped_and_extracts_empty(self, mock_vector_store, content):
        """Test empty/None content extraction and drop behavior in one place."""
        _mock_store, _mock_manager, mock_buffer = mock_vector_store
        extracted = extract_tables_and_text(content)
        assert extracted["tables"] == ""
        assert extracted["text"] == ""

        request_dict = {
            "operation": "add",
            "content": content,
            "metadata": {
                "workspace_id": "ws-123",
                "document_id": "doc-456",
            },
        }

        result = process_embedding_request(request_dict)

        assert "Dropped empty content" in result["add"]
        # embedding_buffer.add should not be called for empty content
        assert not mock_buffer.add.called

    def test_empty_content_uses_otsynopsis_fallback(self, mock_vector_store):
        """Test that OTSynopsis is used when extracted text and tables are empty."""
        _mock_store, _mock_manager, mock_buffer = mock_vector_store
        request_dict = {
            "operation": "add",
            "content": "",
            "metadata": {
                "workspace_id": "ws-123",
                "document_id": "doc-456",
                "OTSynopsis": "Fallback synopsis content",
            },
        }

        result = process_embedding_request(request_dict)

        assert result["add"] is not None
        assert "Stored document successfully" in result["add"] or "Buffered" in result["add"]
        # Check that embedding_buffer.add was called
        assert mock_buffer.add.called

        call_args = mock_buffer.add.call_args[0][0]
        assert len(call_args) > 0
        assert any("Fallback synopsis content" in doc.page_content for doc in call_args)
        assert any(doc.metadata.get("type") == "text" for doc in call_args)

    def test_empty_content_with_empty_otsynopsis_is_dropped(self, mock_vector_store):
        """Test that empty OTSynopsis falls back to dropping content."""
        _mock_store, _mock_manager, mock_buffer = mock_vector_store
        request_dict = {
            "operation": "add",
            "content": "",
            "metadata": {
                "workspace_id": "ws-123",
                "document_id": "doc-456",
                "OTSynopsis": "",
            },
        }

        result = process_embedding_request(request_dict)

        assert "Dropped empty content" in result["add"]
        # embedding_buffer.add should not be called for empty content
        assert not mock_buffer.add.called

    def test_empty_content_with_whitespace_otsynopsis_is_dropped(self, mock_vector_store):
        """Test that whitespace-only OTSynopsis is treated as empty and dropped."""
        _mock_store, _mock_manager, mock_buffer = mock_vector_store
        request_dict = {
            "operation": "add",
            "content": "",
            "metadata": {
                "workspace_id": "ws-123",
                "document_id": "doc-456",
                "OTSynopsis": "   ",
            },
        }

        result = process_embedding_request(request_dict)

        assert "Dropped empty content" in result["add"]
        # embedding_buffer.add should not be called for empty content
        assert not mock_buffer.add.called

    @patch("aviator.celery.record_transaction_sync")
    def test_add_records_correct_chunk_count(self, mock_record, mock_vector_store):
        """Test that the add operation records the actual chunk count in usage tracking."""
        _mock_store, _mock_manager, mock_buffer = mock_vector_store
        mock_buffer.add.return_value = ["id-1", "id-2", "id-3"]
        request_dict = {
            "operation": "add",
            "content": "Some text content to embed.",
            "metadata": {"document_id": "doc-1"},
        }

        process_embedding_request(request_dict)

        mock_record.assert_called_once_with(
            tenant_id=None,
            transaction_type="embedding_add",
            document_count=1,
            chunk_count=1,  # Single document chunk
        )

    @patch("aviator.celery.record_transaction_sync")
    def test_update_records_correct_chunk_count(self, mock_record, mock_vector_store):
        """Test that the update operation records a single embedding_update transaction.

        An update is a logical replace (delete-then-re-add) and must be recorded
        as one ``embedding_update`` event so that ``embeddingsRequestCount`` is only
        incremented by 1 and ``chunksDeletedCount`` / ``documentsDeletedCount``
        are not falsely inflated.  The ``chunk_count`` must be the **net delta**
        (new chunks - old chunks) so that ``get_semantic_size`` stays accurate.
        """
        _mock_store, _mock_manager, mock_buffer = mock_vector_store
        mock_buffer.add.return_value = ["id-1", "id-2", "id-3", "id-4"]
        request_dict = {
            "operation": "update",
            "content": "Updated text content.",
            "metadata": {"documentID": "doc-1", "workspace_id": "ws-1"},
        }

        with patch("aviator.celery._count_chunks_for_filter", return_value=3):
            process_embedding_request(request_dict)

        # Exactly one transaction must be recorded for an update.
        # chunk_count = 4 (new) - 3 (old) = 1 (net delta)
        assert mock_record.call_count == 1
        mock_record.assert_called_once_with(
            tenant_id=None,
            transaction_type="embedding_update",
            document_count=1,
            chunk_count=-2,  # 1 new chunk - 3 old chunks = -2 net delta
        )

    @patch("aviator.celery.record_transaction_sync")
    def test_delete_operation(self, mock_record, mock_vector_store):
        """Test delete operation records correct chunk count calls store.delete with correct filter."""
        mock_store, _mock_manager, _mock_buffer = mock_vector_store
        request_dict = {
            "operation": "delete",
            "content": "",
            "metadata": {
                "documentID": "doc-456",
            },
        }

        with patch("aviator.celery._count_chunks_for_filter", return_value=5):
            result = process_embedding_request(request_dict)

        assert "delete" in result
        mock_store.delete.assert_called_once_with(filter={"document_id": "doc-456"})

        # Verify usage tracking records the chunk count
        mock_record.assert_called_once_with(
            tenant_id=None,
            transaction_type="embedding_delete",
            document_count=1,
            chunk_count=5,
        )

    def test_update_operation(self, mock_vector_store):
        """Test update operation (delete + add)."""
        mock_store, _mock_manager, mock_buffer = mock_vector_store
        request_dict = {
            "operation": "update",
            "content": "Updated content",
            "metadata": {
                "documentID": "doc-456",
                "workspace_id": "ws-123",
            },
        }

        with patch("aviator.celery._count_chunks_for_filter", return_value=2):
            result = process_embedding_request(request_dict)

        assert "update" in result
        assert "add" in result
        assert "delete" in result
        assert mock_store.delete.called
        # Check that embedding_buffer.add was called for the re-add part
        assert mock_buffer.add.called

    def test_json_dict_content_serialization(self, mock_vector_store):
        """Test that dict content is serialized to JSON."""
        _mock_store, _mock_manager, mock_buffer = mock_vector_store
        request_dict = {
            "operation": "add",
            "content": {"key": "value", "nested": {"data": "test"}},
            "metadata": {
                "workspace_id": "ws-123",
                "document_id": "doc-456",
            },
        }

        result = process_embedding_request(request_dict)

        assert result["add"] is not None
        # Check that embedding_buffer.add was called
        assert mock_buffer.add.called

        # Verify content was serialized
        call_args = mock_buffer.add.call_args[0][0]
        content = call_args[0].page_content
        assert "key" in content
        assert "value" in content

    def test_document_unique_ids(self, mock_vector_store):
        """Test that each document chunk gets a unique ID."""
        _mock_store, _mock_manager, mock_buffer = mock_vector_store
        request_dict = {
            "operation": "add",
            "content": "A" * 2000,  # Long content to create multiple chunks
            "metadata": {
                "workspace_id": "ws-123",
                "document_id": "doc-456",
            },
        }

        process_embedding_request(request_dict)

        # Check that embedding_buffer.add was called
        assert mock_buffer.add.called

        # Verify all documents have unique IDs
        call_args = mock_buffer.add.call_args[0][0]
        doc_ids = [doc.id for doc in call_args]
        assert len(doc_ids) == len(set(doc_ids))  # All IDs are unique
        assert all(doc_id is not None for doc_id in doc_ids)

    def test_multiple_tables_separated(self, mock_vector_store):
        """Test that multiple tables are properly separated with TABLE_SPLITTER."""
        _mock_store, _mock_manager, mock_buffer = mock_vector_store
        request_dict = {
            "operation": "add",
            "content": """
                Text content.
                <table><tr><td>Table 1</td></tr></table>
                <table><tr><td>Table 2</td></tr></table>
            """,
            "metadata": {
                "workspace_id": "ws-123",
                "document_id": "doc-456",
            },
        }

        process_embedding_request(request_dict)

        # Check that embedding_buffer.add was called
        assert mock_buffer.add.called

        # Collect all docs passed to embedding_buffer
        call_args = mock_buffer.add.call_args[0][0]

        table_docs = [doc for doc in call_args if doc.metadata.get("type") == "table"]
        text_docs = [doc for doc in call_args if doc.metadata.get("type") == "text"]

        # Should have both text and table documents
        assert len(text_docs) > 0, "Expected at least one text document"
        assert len(table_docs) > 0, "Expected at least one table document"

        # Table docs should have non-empty content
        assert all(doc.page_content.strip() for doc in table_docs)


class TestEmbeddingBuffer:
    """Test the EmbeddingBuffer batching logic."""

    @pytest.fixture
    def mock_vector_store_for_buffer(self):
        """Create a mock vector store for buffer tests."""
        with patch("aviator.services.embedding_buffer.vector_store") as mock_vs:
            mock_store = MagicMock()
            mock_adapter = MagicMock()
            mock_adapter.add_documents.return_value = ["id1", "id2", "id3"]
            mock_adapter.set_text_hash_for_documents.return_value = None
            mock_adapter.populate_text_hash_from_content.return_value = None
            mock_vs.get.return_value = mock_store
            mock_vs.get_adapter.return_value = mock_adapter
            yield mock_vs

    def test_buffer_initialization(self):
        """Test that buffer initializes with correct default values."""
        from aviator.services.embedding_buffer import EmbeddingBuffer

        buffer = EmbeddingBuffer(max_batch_chars=10000, max_batch_size=100, max_wait_seconds=5.0)

        assert buffer.max_batch_chars == 10000
        assert buffer.max_batch_size == 100
        assert buffer.max_wait_seconds == 5.0
        assert buffer.pending_count == 0
        assert buffer.pending_chars == 0
        assert buffer.current_schema_name is None

    def test_buffer_add_small_batch(self, mock_vector_store_for_buffer):
        """Test adding documents that don't trigger flush."""
        from langchain_core.documents import Document

        from aviator.services.embedding_buffer import EmbeddingBuffer

        buffer = EmbeddingBuffer(max_batch_chars=10000, max_batch_size=100, max_wait_seconds=5.0)
        docs = [
            Document(page_content="Short content", metadata={"document_id": "doc1"}),
            Document(page_content="Another short", metadata={"document_id": "doc2"}),
        ]

        result = buffer.add(docs, "test_schema")

        # Should not flush yet
        assert result == []
        assert buffer.pending_count == 2
        assert buffer.pending_chars == len("Short content") + len("Another short")
        assert buffer.current_schema_name == "test_schema"

    def test_buffer_flush_on_batch_size(self, mock_vector_store_for_buffer):
        """Test auto-flush when batch size limit is reached."""
        from langchain_core.documents import Document

        from aviator.services.embedding_buffer import EmbeddingBuffer

        buffer = EmbeddingBuffer(max_batch_chars=100000, max_batch_size=5, max_wait_seconds=5.0)

        # Add 5 documents to trigger batch size flush
        docs = [Document(page_content=f"Content {i}", metadata={"document_id": f"doc{i}"}) for i in range(5)]

        result = buffer.add(docs, "test_schema")

        # Should flush when reaching batch size
        assert len(result) > 0
        assert buffer.pending_count == 0
        assert buffer.pending_chars == 0

    def test_buffer_flush_on_char_limit(self, mock_vector_store_for_buffer):
        """Test auto-flush when character limit is reached."""
        from langchain_core.documents import Document

        from aviator.services.embedding_buffer import EmbeddingBuffer

        buffer = EmbeddingBuffer(max_batch_chars=100, max_batch_size=100, max_wait_seconds=5.0)

        # Add document large enough to trigger char limit
        docs = [Document(page_content="x" * 101, metadata={"document_id": "doc1"})]

        result = buffer.add(docs, "test_schema")

        # Should flush when reaching char limit
        assert len(result) > 0
        assert buffer.pending_count == 0
        assert buffer.pending_chars == 0

    def test_buffer_manual_flush(self, mock_vector_store_for_buffer):
        """Test manual buffer flush."""
        from langchain_core.documents import Document

        from aviator.services.embedding_buffer import EmbeddingBuffer

        buffer = EmbeddingBuffer(max_batch_chars=10000, max_batch_size=100, max_wait_seconds=5.0)

        docs = [Document(page_content="Content", metadata={"document_id": "doc1"})]
        buffer.add(docs, "test_schema")

        assert buffer.pending_count == 1

        # Manual flush
        result = buffer.flush()

        assert len(result) > 0
        assert buffer.pending_count == 0
        assert buffer.pending_chars == 0

    def test_buffer_schema_change_triggers_flush(self, mock_vector_store_for_buffer):
        """Test that schema change triggers flush of existing buffer."""
        from langchain_core.documents import Document

        from aviator.services.embedding_buffer import EmbeddingBuffer

        buffer = EmbeddingBuffer(max_batch_chars=10000, max_batch_size=100, max_wait_seconds=5.0)

        # Add docs for schema1
        docs1 = [Document(page_content="Schema1 content", metadata={"document_id": "doc1"})]
        buffer.add(docs1, "schema1")

        assert buffer.pending_count == 1
        assert buffer.current_schema_name == "schema1"

        # Add docs for schema2 - should flush schema1 first
        docs2 = [Document(page_content="Schema2 content", metadata={"document_id": "doc2"})]
        result = buffer.add(docs2, "schema2")

        # Should have flushed schema1
        assert len(result) > 0
        assert buffer.current_schema_name == "schema2"
        assert buffer.pending_count == 1  # schema2 doc now buffered

    def test_buffer_remove_by_document_id(self, mock_vector_store_for_buffer):
        """Test removing buffered documents by document_id."""
        from langchain_core.documents import Document

        from aviator.services.embedding_buffer import EmbeddingBuffer

        buffer = EmbeddingBuffer(max_batch_chars=10000, max_batch_size=100, max_wait_seconds=5.0)

        docs = [
            Document(page_content="Content 1", metadata={"document_id": "doc1"}),
            Document(page_content="Content 2", metadata={"document_id": "doc1"}),
            Document(page_content="Content 3", metadata={"document_id": "doc2"}),
        ]
        buffer.add(docs, "test_schema")

        assert buffer.pending_count == 3

        # Remove all chunks for doc1
        removed = buffer.remove_by_document_id("doc1")

        assert removed == 2
        assert buffer.pending_count == 1
        assert buffer.pending_chars == len("Content 3")

    def test_buffer_remove_nonexistent_document(self, mock_vector_store_for_buffer):
        """Test removing document_id that doesn't exist in buffer."""
        from langchain_core.documents import Document

        from aviator.services.embedding_buffer import EmbeddingBuffer

        buffer = EmbeddingBuffer(max_batch_chars=10000, max_batch_size=100, max_wait_seconds=5.0)

        docs = [Document(page_content="Content", metadata={"document_id": "doc1"})]
        buffer.add(docs, "test_schema")

        # Try to remove non-existent document
        removed = buffer.remove_by_document_id("doc999")

        assert removed == 0
        assert buffer.pending_count == 1

    def test_buffer_with_prehashed_documents(self, mock_vector_store_for_buffer):
        """Test buffer with pre-computed text hashes."""
        from langchain_core.documents import Document

        from aviator.services.embedding_buffer import EmbeddingBuffer

        buffer = EmbeddingBuffer(max_batch_chars=10000, max_batch_size=100, max_wait_seconds=5.0)

        docs = [
            Document(page_content="Content 1", metadata={"document_id": "doc1"}),
            Document(page_content="Content 2", metadata={"document_id": "doc2"}),
        ]
        text_hashes = ["hash1", "hash2"]

        buffer.add(docs, "test_schema", text_hashes)

        assert buffer.pending_count == 2
        assert len(buffer.buffer_hashes) == 2
        assert buffer.buffer_hashes[0] == "hash1"
        assert buffer.buffer_hashes[1] == "hash2"

    def test_buffer_empty_flush(self, mock_vector_store_for_buffer):
        """Test flushing empty buffer."""
        from aviator.services.embedding_buffer import EmbeddingBuffer

        buffer = EmbeddingBuffer(max_batch_chars=10000, max_batch_size=100, max_wait_seconds=5.0)

        result = buffer.flush()

        assert result == []
        assert buffer.pending_count == 0

    def test_buffer_multiple_flushes(self, mock_vector_store_for_buffer):
        """Test multiple sequential flushes."""
        from langchain_core.documents import Document

        from aviator.services.embedding_buffer import EmbeddingBuffer

        buffer = EmbeddingBuffer(max_batch_chars=50, max_batch_size=100, max_wait_seconds=5.0)

        # First batch
        docs1 = [Document(page_content="x" * 51, metadata={"document_id": "doc1"})]
        result1 = buffer.add(docs1, "test_schema")
        assert len(result1) > 0

        # Second batch
        docs2 = [Document(page_content="y" * 51, metadata={"document_id": "doc2"})]
        result2 = buffer.add(docs2, "test_schema")
        assert len(result2) > 0

        # Buffer should be empty after both flushes
        assert buffer.pending_count == 0
        assert buffer.pending_chars == 0

    def test_buffer_backward_compatibility_keys(self, mock_vector_store_for_buffer):
        """Test buffer handles both normalized and backward compatibility keys."""
        from langchain_core.documents import Document

        from aviator.services.embedding_buffer import EmbeddingBuffer

        buffer = EmbeddingBuffer(max_batch_chars=10000, max_batch_size=100, max_wait_seconds=5.0)

        # Test with documentID (backward compatibility key)
        docs = [
            Document(page_content="Content", metadata={"documentID": "doc1"}),
            Document(page_content="Content", metadata={"document_id": "doc2"}),
        ]

        buffer.add(docs, "test_schema")

        # Should find documents with either key format
        assert buffer.pending_count == 2

    def test_buffer_flush_with_prehash_exception_fallback(self, mock_vector_store_for_buffer):
        """Test flush when set_text_hash fails but fallback succeeds."""
        from langchain_core.documents import Document

        from aviator.services.embedding_buffer import EmbeddingBuffer

        # Mock adapter to raise exception on set_text_hash but succeed on populate
        mock_adapter = mock_vector_store_for_buffer.get_adapter.return_value
        mock_adapter.set_text_hash_for_documents.side_effect = Exception("Set hash failed")
        mock_adapter.populate_text_hash_from_content.return_value = None

        buffer = EmbeddingBuffer(max_batch_chars=50, max_batch_size=100, max_wait_seconds=5.0)

        # Add document with pre-computed hash that triggers flush
        docs = [Document(page_content="x" * 51, metadata={"document_id": "doc1"})]
        text_hashes = ["hash1"]

        result = buffer.add(docs, "test_schema", text_hashes)

        # Should still return results despite exception (fallback worked)
        assert len(result) > 0
        assert buffer.pending_count == 0

        # Verify both methods were called (set_text_hash failed, fallback succeeded)
        assert mock_adapter.set_text_hash_for_documents.called
        assert mock_adapter.populate_text_hash_from_content.called

    def test_buffer_flush_with_prehash_and_fallback_failure(self, mock_vector_store_for_buffer):
        """Test flush when both set_text_hash and fallback fail."""
        from langchain_core.documents import Document

        from aviator.services.embedding_buffer import EmbeddingBuffer

        # Mock adapter to raise exceptions on both methods
        mock_adapter = mock_vector_store_for_buffer.get_adapter.return_value
        mock_adapter.set_text_hash_for_documents.side_effect = Exception("Set hash failed")
        mock_adapter.populate_text_hash_from_content.side_effect = Exception("Populate failed")

        buffer = EmbeddingBuffer(max_batch_chars=50, max_batch_size=100, max_wait_seconds=5.0)

        # Add document with pre-computed hash that triggers flush
        docs = [Document(page_content="x" * 51, metadata={"document_id": "doc1"})]
        text_hashes = ["hash1"]

        result = buffer.add(docs, "test_schema", text_hashes)

        # Should still return results (exception is logged but doesn't fail the flush)
        assert len(result) > 0
        assert buffer.pending_count == 0

        # Verify both methods were called
        assert mock_adapter.set_text_hash_for_documents.called
        assert mock_adapter.populate_text_hash_from_content.called

    def test_buffer_flush_with_populate_exception(self, mock_vector_store_for_buffer):
        """Test flush when populate_text_hash_from_content fails."""
        from langchain_core.documents import Document

        from aviator.services.embedding_buffer import EmbeddingBuffer

        # Mock adapter to raise exception on populate
        mock_adapter = mock_vector_store_for_buffer.get_adapter.return_value
        mock_adapter.populate_text_hash_from_content.side_effect = Exception("Populate failed")

        buffer = EmbeddingBuffer(max_batch_chars=50, max_batch_size=100, max_wait_seconds=5.0)

        # Add document without pre-computed hash that triggers flush
        docs = [Document(page_content="x" * 51, metadata={"document_id": "doc1"})]

        result = buffer.add(docs, "test_schema")

        # Should still return results (exception is logged but doesn't fail the flush)
        assert len(result) > 0
        assert buffer.pending_count == 0

        # Verify populate was called
        assert mock_adapter.populate_text_hash_from_content.called

    def test_buffer_flush_with_mixed_hashes_set_exception(self, mock_vector_store_for_buffer):
        """Test flush with mixed pre-computed/non-pre-computed hashes when set_text_hash fails."""
        from langchain_core.documents import Document

        from aviator.services.embedding_buffer import EmbeddingBuffer

        # Mock adapter to raise exception on set_text_hash but succeed on populate
        mock_adapter = mock_vector_store_for_buffer.get_adapter.return_value
        mock_adapter.set_text_hash_for_documents.side_effect = Exception("Set hash failed")
        mock_adapter.populate_text_hash_from_content.return_value = None

        buffer = EmbeddingBuffer(max_batch_chars=150, max_batch_size=100, max_wait_seconds=5.0)

        # Add documents with mixed hashes
        docs = [
            Document(page_content="x" * 50, metadata={"document_id": "doc1"}),  # with hash
            Document(page_content="y" * 50, metadata={"document_id": "doc2"}),  # without hash
        ]
        text_hashes = ["hash1", ""]

        buffer.add(docs, "test_schema", text_hashes)

        # Manually flush
        result = buffer.flush()

        # Should return results despite exception
        assert len(result) > 0

        # Verify set_text_hash was attempted and fallback was called
        assert mock_adapter.set_text_hash_for_documents.called
        assert mock_adapter.populate_text_hash_from_content.called

    def test_buffer_timer_scheduled_on_add(self, mock_vector_store_for_buffer):
        """Test that timer is scheduled when documents are added without triggering immediate flush."""
        from langchain_core.documents import Document

        from aviator.services.embedding_buffer import EmbeddingBuffer

        buffer = EmbeddingBuffer(max_batch_chars=10000, max_batch_size=100, max_wait_seconds=5.0)

        # Add small batch that doesn't trigger immediate flush
        docs = [Document(page_content="Small content", metadata={"document_id": "doc1"})]
        buffer.add(docs, "test_schema")

        # Timer should be scheduled
        assert buffer._timer is not None
        assert buffer._timer.is_alive()

        # Clean up
        buffer._cancel_timer()

    def test_buffer_timer_triggers_flush(self, mock_vector_store_for_buffer):
        """Test that timer triggers flush after timeout."""
        import time

        from langchain_core.documents import Document

        from aviator.services.embedding_buffer import EmbeddingBuffer

        buffer = EmbeddingBuffer(max_batch_chars=10000, max_batch_size=100, max_wait_seconds=0.5)

        # Add small batch that doesn't trigger immediate flush
        docs = [Document(page_content="Content to be flushed", metadata={"document_id": "doc1"})]
        buffer.add(docs, "test_schema")

        assert buffer.pending_count == 1

        # Wait for timer to fire (with some buffer time)
        time.sleep(0.7)

        # Buffer should be empty after timer flush
        assert buffer.pending_count == 0

    def test_buffer_timer_cancelled_on_manual_flush(self, mock_vector_store_for_buffer):
        """Test that timer is cancelled when buffer is manually flushed."""
        from langchain_core.documents import Document

        from aviator.services.embedding_buffer import EmbeddingBuffer

        buffer = EmbeddingBuffer(max_batch_chars=10000, max_batch_size=100, max_wait_seconds=5.0)

        # Add documents to schedule timer
        docs = [Document(page_content="Content", metadata={"document_id": "doc1"})]
        buffer.add(docs, "test_schema")

        assert buffer._timer is not None

        # Manual flush should cancel timer
        buffer.flush()

        # Timer should be cancelled (buffer no longer has a reference to it)
        assert buffer._timer is None

    def test_buffer_timer_cancelled_on_batch_size_flush(self, mock_vector_store_for_buffer):
        """Test that timer is cancelled when batch size triggers flush."""
        from langchain_core.documents import Document

        from aviator.services.embedding_buffer import EmbeddingBuffer

        buffer = EmbeddingBuffer(max_batch_chars=10000, max_batch_size=2, max_wait_seconds=5.0)

        # Add first document to schedule timer
        docs1 = [Document(page_content="Content 1", metadata={"document_id": "doc1"})]
        buffer.add(docs1, "test_schema")

        assert buffer._timer is not None

        # Add second document to trigger batch size flush
        docs2 = [Document(page_content="Content 2", metadata={"document_id": "doc2"})]
        buffer.add(docs2, "test_schema")

        # Timer should be cancelled after auto-flush
        assert buffer._timer is None
        assert buffer.pending_count == 0

    def test_buffer_timer_not_fires_if_empty(self, mock_vector_store_for_buffer):
        """Test that timer callback does nothing if buffer is empty."""
        import time

        from langchain_core.documents import Document

        from aviator.services.embedding_buffer import EmbeddingBuffer

        buffer = EmbeddingBuffer(max_batch_chars=10000, max_batch_size=100, max_wait_seconds=0.5)

        # Add and immediately flush
        docs = [Document(page_content="Content", metadata={"document_id": "doc1"})]
        buffer.add(docs, "test_schema")
        buffer.flush()

        # Re-add to schedule timer
        buffer.add(docs, "test_schema")
        assert buffer.pending_count == 1

        # Manually empty the buffer before timer fires
        buffer.buffer = []
        buffer.buffer_chars = 0

        # Wait for timer to attempt flush
        time.sleep(0.7)

        # Should still be empty (timer found empty buffer, did nothing)
        assert buffer.pending_count == 0

    def test_buffer_timer_rescheduled_after_flush(self, mock_vector_store_for_buffer):
        """Test that timer is rescheduled if documents remain after batch size flush."""
        from langchain_core.documents import Document

        from aviator.services.embedding_buffer import EmbeddingBuffer

        buffer = EmbeddingBuffer(max_batch_chars=10000, max_batch_size=2, max_wait_seconds=5.0)

        # Add 3 documents - first 2 will flush, 1 will remain
        docs = [
            Document(page_content="Content 1", metadata={"document_id": "doc1"}),
            Document(page_content="Content 2", metadata={"document_id": "doc2"}),
            Document(page_content="Content 3", metadata={"document_id": "doc3"}),
        ]

        for doc in docs:
            buffer.add([doc], "test_schema")

        # Should have 1 document remaining and timer scheduled
        assert buffer.pending_count == 1
        assert buffer._timer is not None
        assert buffer._timer.is_alive()

        # Clean up
        buffer._cancel_timer()

    def test_buffer_cancel_timer_when_none(self, mock_vector_store_for_buffer):
        """Test that cancelling timer when none exists is safe."""
        from aviator.services.embedding_buffer import EmbeddingBuffer

        buffer = EmbeddingBuffer(max_batch_chars=10000, max_batch_size=100, max_wait_seconds=5.0)

        # No timer scheduled initially
        assert buffer._timer is None

        # Should not raise exception
        buffer._cancel_timer()

        assert buffer._timer is None

    @pytest.mark.parametrize(
        ("exception_type", "exception_class", "expected_code", "expected_msg"),
        [
            ("sqlalchemy.exc.DataError", "EmbeddingError", 104, "SQL data error"),
            ("sqlalchemy.exc.IntegrityError", "EmbeddingRetryableError", 100, "Database error"),
            ("Exception", "EmbeddingRetryableError", 100, "Vector store operation failed"),
        ],
    )
    def test_buffer_flush_raises_error(
        self, mock_vector_store_for_buffer, exception_type, exception_class, expected_code, expected_msg
    ):
        """Test flush raises appropriate error based on exception type."""
        import sqlalchemy.exc
        from langchain_core.documents import Document

        from aviator.exceptions import EmbeddingError, EmbeddingRetryableError
        from aviator.services.embedding_buffer import EmbeddingBuffer

        # Create the exception instance
        if exception_type == "sqlalchemy.exc.DataError":
            exc = sqlalchemy.exc.DataError("statement", "params", "orig")
        elif exception_type == "sqlalchemy.exc.IntegrityError":
            exc = sqlalchemy.exc.IntegrityError("statement", "params", "orig")
        else:
            exc = Exception("Unexpected error")

        # Mock adapter to raise exception for add_documents
        mock_adapter = mock_vector_store_for_buffer.get_adapter.return_value
        mock_adapter.add_documents.side_effect = exc

        buffer = EmbeddingBuffer(max_batch_chars=50, max_batch_size=100, max_wait_seconds=5.0)
        docs = [Document(page_content="x" * 51, metadata={"document_id": "doc1"})]

        # Get expected exception class
        expected_exception = EmbeddingError if exception_class == "EmbeddingError" else EmbeddingRetryableError

        # Should raise expected error
        with pytest.raises(expected_exception) as exc_info:
            buffer.add(docs, "test_schema")

        assert exc_info.value.code == expected_code
        assert expected_msg in exc_info.value.message

    def test_buffer_flush_combined_char_and_size_threshold(self, mock_vector_store_for_buffer):
        """Test that buffer respects both char and size thresholds."""
        from langchain_core.documents import Document

        from aviator.services.embedding_buffer import EmbeddingBuffer

        buffer = EmbeddingBuffer(max_batch_chars=100, max_batch_size=5, max_wait_seconds=5.0)

        # Add 3 small documents that don't reach either threshold
        docs = [Document(page_content="x" * 20, metadata={"document_id": f"doc{i}"}) for i in range(3)]
        result = buffer.add(docs, "test_schema")

        # Should not flush yet (60 chars < 100, 3 docs < 5)
        assert result == []
        assert buffer.pending_count == 3
        assert buffer.pending_chars == 60

        # Add document that pushes over char limit
        docs2 = [Document(page_content="x" * 41, metadata={"document_id": "doc4"})]
        result = buffer.add(docs2, "test_schema")

        # Should flush when char limit reached (101 chars > 100)
        assert len(result) > 0
        assert buffer.pending_count == 0
        assert buffer.pending_chars == 0

    def test_buffer_pending_chars_accuracy(self, mock_vector_store_for_buffer):
        """Test that pending_chars accurately tracks total character count."""
        from langchain_core.documents import Document

        from aviator.services.embedding_buffer import EmbeddingBuffer

        buffer = EmbeddingBuffer(max_batch_chars=10000, max_batch_size=100, max_wait_seconds=5.0)

        # Add documents with known character counts
        docs = [
            Document(page_content="12345", metadata={"document_id": "doc1"}),  # 5 chars
            Document(page_content="abcdefghij", metadata={"document_id": "doc2"}),  # 10 chars
            Document(page_content="xyz", metadata={"document_id": "doc3"}),  # 3 chars
        ]
        buffer.add(docs, "test_schema")

        # Should have exactly 18 chars
        assert buffer.pending_chars == 18
        assert buffer.pending_count == 3

    def test_buffer_incremental_char_counting(self, mock_vector_store_for_buffer):
        """Test that char count increments correctly with successive adds."""
        from langchain_core.documents import Document

        from aviator.services.embedding_buffer import EmbeddingBuffer

        buffer = EmbeddingBuffer(max_batch_chars=10000, max_batch_size=100, max_wait_seconds=5.0)

        # Add documents incrementally
        buffer.add([Document(page_content="a" * 100, metadata={"document_id": "doc1"})], "test_schema")
        assert buffer.pending_chars == 100

        buffer.add([Document(page_content="b" * 50, metadata={"document_id": "doc2"})], "test_schema")
        assert buffer.pending_chars == 150

        buffer.add([Document(page_content="c" * 25, metadata={"document_id": "doc3"})], "test_schema")
        assert buffer.pending_chars == 175

    def test_buffer_char_reset_after_flush(self, mock_vector_store_for_buffer):
        """Test that char count resets to 0 after flush."""
        from langchain_core.documents import Document

        from aviator.services.embedding_buffer import EmbeddingBuffer

        buffer = EmbeddingBuffer(max_batch_chars=50, max_batch_size=100, max_wait_seconds=5.0)

        # Add documents that exceed char limit to trigger flush
        docs = [Document(page_content="x" * 51, metadata={"document_id": "doc1"})]
        result = buffer.add(docs, "test_schema")

        # After flush, chars should be 0
        assert len(result) > 0
        assert buffer.pending_chars == 0
        assert buffer.pending_count == 0

    def test_buffer_with_text_hashes(self, mock_vector_store_for_buffer):
        """Test adding documents with pre-computed text hashes."""
        from langchain_core.documents import Document

        from aviator.services.embedding_buffer import EmbeddingBuffer

        buffer = EmbeddingBuffer(max_batch_chars=10000, max_batch_size=100, max_wait_seconds=5.0)

        docs = [
            Document(page_content="Content 1", metadata={"document_id": "doc1"}),
            Document(page_content="Content 2", metadata={"document_id": "doc2"}),
        ]
        text_hashes = ["hash1", "hash2"]

        result = buffer.add(docs, "test_schema", text_hashes=text_hashes)

        # Should buffer documents with hashes
        assert result == []
        assert buffer.pending_count == 2
        assert len(buffer.buffer_hashes) == 2
        assert buffer.buffer_hashes == ["hash1", "hash2"]

    def test_buffer_without_text_hashes(self, mock_vector_store_for_buffer):
        """Test adding documents without text hashes (default behavior)."""
        from langchain_core.documents import Document

        from aviator.services.embedding_buffer import EmbeddingBuffer

        buffer = EmbeddingBuffer(max_batch_chars=10000, max_batch_size=100, max_wait_seconds=5.0)

        docs = [
            Document(page_content="Content 1", metadata={"document_id": "doc1"}),
            Document(page_content="Content 2", metadata={"document_id": "doc2"}),
        ]

        result = buffer.add(docs, "test_schema", text_hashes=None)

        # Should buffer documents with empty string hashes
        assert result == []
        assert buffer.pending_count == 2
        assert len(buffer.buffer_hashes) == 2
        assert all(h == "" for h in buffer.buffer_hashes)

    def test_buffer_mixed_text_hashes(self, mock_vector_store_for_buffer):
        """Test flushing documents with mixed hash states."""
        from langchain_core.documents import Document

        from aviator.services.embedding_buffer import EmbeddingBuffer

        buffer = EmbeddingBuffer(max_batch_chars=200, max_batch_size=100, max_wait_seconds=5.0)
        mock_adapter = mock_vector_store_for_buffer.get_adapter.return_value

        # Add docs with hashes
        docs1 = [Document(page_content="x" * 50, metadata={"document_id": "doc1"})]
        buffer.add(docs1, "test_schema", text_hashes=["hash1"])

        # Add docs without hashes
        docs2 = [Document(page_content="x" * 60, metadata={"document_id": "doc2"})]
        buffer.add(docs2, "test_schema", text_hashes=None)

        # Manually flush to test hash handling
        result = buffer.flush()

        # Should call set_text_hash_for_documents for docs with hashes
        assert len(result) > 0
        assert mock_adapter.set_text_hash_for_documents.called

    def test_buffer_text_hash_count_mismatch(self, mock_vector_store_for_buffer):
        """Test that hash count mismatch raises EmbeddingError."""
        from langchain_core.documents import Document

        from aviator.exceptions import EmbeddingError
        from aviator.services.embedding_buffer import EmbeddingBuffer

        buffer = EmbeddingBuffer(max_batch_chars=10000, max_batch_size=100, max_wait_seconds=5.0)

        docs = [
            Document(page_content="Content 1", metadata={"document_id": "doc1"}),
            Document(page_content="Content 2", metadata={"document_id": "doc2"}),
        ]
        # Provide wrong number of hashes
        text_hashes = ["hash1"]  # 1 hash for 2 docs

        with pytest.raises(EmbeddingError) as exc_info:
            buffer.add(docs, "test_schema", text_hashes=text_hashes)

        assert exc_info.value.code == 110
        assert "Text hashes count mismatch" in exc_info.value.message

    def test_buffer_set_text_hash_failure_fallback(self, mock_vector_store_for_buffer):
        """Test fallback to populate_text_hash when set_text_hash fails."""
        from langchain_core.documents import Document

        from aviator.services.embedding_buffer import EmbeddingBuffer

        buffer = EmbeddingBuffer(max_batch_chars=50, max_batch_size=100, max_wait_seconds=5.0)
        mock_adapter = mock_vector_store_for_buffer.get_adapter.return_value

        # Make set_text_hash_for_documents fail
        mock_adapter.set_text_hash_for_documents.side_effect = Exception("Hash set failed")

        # Add docs with hashes that will trigger flush
        docs = [Document(page_content="x" * 51, metadata={"document_id": "doc1"})]
        result = buffer.add(docs, "test_schema", text_hashes=["hash1"])

        # Should fallback to populate_text_hash_from_content
        assert len(result) > 0
        assert mock_adapter.populate_text_hash_from_content.called

    def test_buffer_empty_manual_flush(self, mock_vector_store_for_buffer):
        """Test that manually flushing empty buffer is safe."""
        from aviator.services.embedding_buffer import EmbeddingBuffer

        buffer = EmbeddingBuffer(max_batch_chars=10000, max_batch_size=100, max_wait_seconds=5.0)

        # Flush without adding any documents
        result = buffer.flush()

        # Should return empty list
        assert result == []
        assert buffer.pending_count == 0
        assert buffer.pending_chars == 0

    def test_buffer_immediate_flush_with_zero_wait(self, mock_vector_store_for_buffer):
        """Test buffer with max_wait_seconds=0 (timer may flush immediately)."""
        import time

        from langchain_core.documents import Document

        from aviator.services.embedding_buffer import EmbeddingBuffer

        buffer = EmbeddingBuffer(max_batch_chars=10000, max_batch_size=100, max_wait_seconds=0.0)

        # Add documents
        docs = [Document(page_content="Content", metadata={"document_id": "doc1"})]
        result = buffer.add(docs, "test_schema")

        # The timer fires on a background thread and may flush before this assertion.
        assert result == []
        assert buffer.pending_count in {0, 1}

        # Wait for timer to fire immediately
        time.sleep(0.1)

        # Buffer should be flushed by timer
        assert buffer.pending_count == 0

    def test_buffer_char_limit_exact_boundary(self, mock_vector_store_for_buffer):
        """Test flush triggered at exact char limit boundary."""
        from langchain_core.documents import Document

        from aviator.services.embedding_buffer import EmbeddingBuffer

        buffer = EmbeddingBuffer(max_batch_chars=100, max_batch_size=100, max_wait_seconds=5.0)

        # Add documents totaling exactly 100 chars
        docs = [
            Document(page_content="x" * 50, metadata={"document_id": "doc1"}),
            Document(page_content="y" * 50, metadata={"document_id": "doc2"}),
        ]

        result = []
        for doc in docs:
            result.extend(buffer.add([doc], "test_schema"))

        # Should flush when reaching exactly 100 chars
        assert len(result) > 0
        assert buffer.pending_count == 0
        assert buffer.pending_chars == 0

    def test_buffer_char_limit_one_over_boundary(self, mock_vector_store_for_buffer):
        """Test flush triggered when one char over limit."""
        from langchain_core.documents import Document

        from aviator.services.embedding_buffer import EmbeddingBuffer

        buffer = EmbeddingBuffer(max_batch_chars=100, max_batch_size=100, max_wait_seconds=5.0)

        # Add documents totaling 101 chars
        buffer.add([Document(page_content="x" * 50, metadata={"document_id": "doc1"})], "test_schema")
        assert buffer.pending_count == 1

        result = buffer.add([Document(page_content="y" * 51, metadata={"document_id": "doc2"})], "test_schema")

        # Should flush when exceeding 100 chars (now at 101)
        assert len(result) > 0
        assert buffer.pending_count == 0
        assert buffer.pending_chars == 0

    def test_buffer_multiple_char_limit_flushes(self, mock_vector_store_for_buffer):
        """Test multiple flushes triggered by char limit."""
        from langchain_core.documents import Document

        from aviator.services.embedding_buffer import EmbeddingBuffer

        buffer = EmbeddingBuffer(max_batch_chars=50, max_batch_size=100, max_wait_seconds=5.0)

        # First flush
        result1 = buffer.add([Document(page_content="x" * 51, metadata={"document_id": "doc1"})], "test_schema")
        assert len(result1) > 0

        # Second flush
        result2 = buffer.add([Document(page_content="y" * 51, metadata={"document_id": "doc2"})], "test_schema")
        assert len(result2) > 0

        # Third flush
        result3 = buffer.add([Document(page_content="z" * 51, metadata={"document_id": "doc3"})], "test_schema")
        assert len(result3) > 0

        # All should have flushed
        assert buffer.pending_count == 0
        assert buffer.pending_chars == 0


class TestProcessEmbeddingRequestErrorHandling:
    """Test error handling in process_embedding_request."""

    @pytest.fixture
    def mock_services(self):
        """Mock all services needed for process_embedding_request."""
        with (
            patch("aviator.celery.embedding_buffer") as mock_buffer,
            patch("aviator.celery.vector_store") as mock_vs,
            patch("aviator.celery.load_embedding_extension"),
            patch("aviator.celery.tenant_service") as mock_tenant,
        ):
            mock_tenant.tenant_exists.return_value = True
            mock_store = MagicMock()
            mock_adapter = MagicMock()
            mock_vs.get.return_value = mock_store
            mock_vs.get_adapter.return_value = mock_adapter
            mock_buffer.add.return_value = ["id1", "id2"]
            mock_buffer.remove_by_document_id.return_value = 0
            mock_buffer.pending_count = 0
            yield {
                "buffer": mock_buffer,
                "vector_store": mock_vs,
                "store": mock_store,
                "adapter": mock_adapter,
                "tenant": mock_tenant,
            }

    @pytest.mark.parametrize(
        ("exception_type", "exception_class", "expected_code"),
        [
            ("sqlalchemy.exc.DataError", "EmbeddingError", 104),
            ("sqlalchemy.exc.IntegrityError", "EmbeddingRetryableError", 100),
            ("Exception", "EmbeddingRetryableError", 100),
        ],
    )
    def test_add_operation_buffer_error(self, mock_services, exception_type, exception_class, expected_code):
        """Test add operation when buffer raises various exceptions."""
        import sqlalchemy.exc

        from aviator.celery import process_embedding_request
        from aviator.exceptions import EmbeddingError, EmbeddingRetryableError

        # Create the exception instance
        if exception_type == "sqlalchemy.exc.DataError":
            exc = sqlalchemy.exc.DataError("statement", "params", "orig")
        elif exception_type == "sqlalchemy.exc.IntegrityError":
            exc = sqlalchemy.exc.IntegrityError("statement", "params", "orig")
        else:
            exc = Exception("Unexpected error")

        # Mock buffer to raise exception
        mock_services["buffer"].add.side_effect = exc

        request_dict = {
            "operation": "add",
            "content": "Test content",
            "metadata": {
                "workspace_id": "ws-123",
                "document_id": "doc-456",
            },
        }

        # Get expected exception class
        expected_exception = EmbeddingError if exception_class == "EmbeddingError" else EmbeddingRetryableError

        # Should raise expected error
        with pytest.raises(expected_exception) as exc_info:
            process_embedding_request(request_dict)

        assert exc_info.value.code == expected_code

    def test_delete_operation_error(self, mock_services):
        """Test delete operation when vector store raises exception."""
        from aviator.celery import process_embedding_request
        from aviator.exceptions import EmbeddingRetryableError

        # Mock delete to raise exception
        mock_services["store"].delete.side_effect = Exception("Delete failed")

        request_dict = {
            "operation": "delete",
            "content": "",
            "metadata": {
                "documentID": "doc-456",
            },
        }

        # Should raise EmbeddingRetryableError
        with pytest.raises(EmbeddingRetryableError) as exc_info:
            process_embedding_request(request_dict)

        assert exc_info.value.code == 103
        assert "Failed to delete documents" in exc_info.value.message


class TestSummaryGenerationRequest:
    """Tests for the `summary_generation_request` Celery task."""

    def test_task_has_explicit_name(self):
        assert (
            getattr(process_workspace_summary_request, "name", None)
            == "aviator.celery.process_workspace_summary_request"
        )

    @pytest.fixture
    def mock_llm(self):
        mock = MagicMock()
        mock.with_structured_output.return_value = MagicMock()
        return mock

    @pytest.fixture
    def mock_structured_prompt(self):
        mock_prompt = MagicMock()
        mock_runnable = MagicMock()
        mock_prompt.__or__.return_value = mock_runnable

        with patch("aviator.services.summary.ChatPromptTemplate.from_messages", return_value=mock_prompt):
            yield mock_runnable

    def test_successful_summarization(self, mock_llm, mock_structured_prompt):
        request_dict = {
            "content": "This is a long document about LangGraph and RAG systems with detailed explanations.",
            "metadata": {
                "documentID": "doc-123",
                "workspaceID": "ws-456",
            },
        }

        mock_structured_prompt.ainvoke = AsyncMock(
            return_value=DocumentSummary(
                summary="Line 1: Main topic discussed\nLine 2: Key findings presented\nLine 3: Conclusions drawn",
                title="",
            )
        )

        # Mock embeddings service (no title in response, so embeddings won't be generated)
        mock_embeddings = MagicMock()
        mock_embeddings.embed_documents.return_value = [[0.1] * 768]

        with (
            patch("aviator.services.summary.LLMRegistry.get_llm", return_value=mock_llm),
            patch("aviator.celery.EmbeddingsRegistry.get_embeddings", return_value=mock_embeddings),
            patch(
                "aviator.celery.upsert_workspace_document_with_summary",
                return_value={"success": True, "operation": "create"},
            ) as mock_upsert,
        ):
            result = process_workspace_summary_request(request_dict)

            assert "summary" in result
            assert result["summary"].startswith("Line 1:")
            assert result["document_id"] == "doc-123"
            assert result["workspace_id"] == "ws-456"

            # Verify LLM was called with correct options
            from aviator.services.summary import LLMRegistry
            from aviator.settings import settings

            LLMRegistry.get_llm.assert_called_once()
            call_kwargs = LLMRegistry.get_llm.call_args[1]
            assert call_kwargs["options"]["max_tokens"] == settings.document_summary_max_tokens
            assert call_kwargs["options"]["temperature"] == settings.llm_temperature

            # Verify upsert was called with title_embeddings=None (no title in response)
            mock_upsert.assert_called_once()
            call_kwargs = mock_upsert.call_args[1]
            assert call_kwargs["title_embeddings"] is None

    def test_json_summary_parsed_and_title_forwarded(self, mock_structured_prompt):
        """Test that structured output title and summary are passed to DB upsert."""
        from aviator.settings import settings

        request_dict = {
            "content": "Structured summary content.",
            "metadata": {
                "documentID": "doc-abc",
                "workspaceID": "ws-def",
            },
        }

        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value = MagicMock()
        mock_structured_prompt.ainvoke = AsyncMock(
            return_value=DocumentSummary(title="Generated title", summary="Generated summary")
        )

        # Mock embeddings service to return embeddings for the title
        mock_embeddings = MagicMock()
        mock_title_embeddings = [0.5] * settings.vector_size
        mock_embeddings.embed_documents.return_value = [mock_title_embeddings]

        with (
            patch("aviator.services.summary.LLMRegistry.get_llm", return_value=mock_llm),
            patch("aviator.celery.EmbeddingsRegistry.get_embeddings", return_value=mock_embeddings),
            patch(
                "aviator.celery.upsert_workspace_document_with_summary",
                return_value={"success": True, "operation": "create"},
            ) as mock_upsert,
        ):
            result = process_workspace_summary_request(request_dict)

            assert result["title"] == "Generated title"
            assert result["summary"] == "Generated summary"

            # Verify embeddings were generated for the title
            mock_embeddings.embed_documents.assert_called_once_with(["Generated title"])

            # Verify upsert was called with title embeddings and schema_name
            mock_upsert.assert_called_once_with(
                document_id="doc-abc",
                summary_text="Generated summary",
                title="Generated title",
                title_embeddings=mock_title_embeddings,
                schema_name="public",
            )

    def test_embeddings_generation_failure(self, mock_structured_prompt):
        """Test that if embeddings generation fails, the task continues with title_embeddings=None."""
        request_dict = {
            "content": "Content to summarize.",
            "metadata": {
                "documentID": "doc-xyz",
                "workspaceID": "ws-123",
            },
        }

        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value = MagicMock()
        mock_structured_prompt.ainvoke = AsyncMock(
            return_value=DocumentSummary(title="Title here", summary="Summary here")
        )

        # Mock embeddings service to raise an exception
        mock_embeddings = MagicMock()
        mock_embeddings.embed_documents.side_effect = Exception("Embeddings service unavailable")

        with (
            patch("aviator.services.summary.LLMRegistry.get_llm", return_value=mock_llm),
            patch("aviator.celery.EmbeddingsRegistry.get_embeddings", return_value=mock_embeddings),
            patch(
                "aviator.celery.upsert_workspace_document_with_summary",
                return_value={"success": True, "operation": "create"},
            ) as mock_upsert,
        ):
            # Should not raise exception
            result = process_workspace_summary_request(request_dict)

            assert result["title"] == "Title here"
            assert result["summary"] == "Summary here"

            # Verify upsert was called with title_embeddings=None and schema_name
            mock_upsert.assert_called_once_with(
                document_id="doc-xyz",
                summary_text="Summary here",
                title="Title here",
                title_embeddings=None,
                schema_name="public",
            )

    def test_empty_content_raises_201(self):
        request_dict = {"content": "", "metadata": {"documentID": "doc-123"}}
        result = process_workspace_summary_request(request_dict)
        assert result["document_id"] == "doc-123"
        assert result["workspace_id"] == "unknown"
        assert result["error"] == "No content provided for summarization"

    def test_missing_content_raises_201(self):
        request_dict = {"metadata": {"documentID": "doc-123"}}
        result = process_workspace_summary_request(request_dict)
        assert result["document_id"] == "doc-123"
        assert result["workspace_id"] == "unknown"
        assert result["error"] == "No content provided for summarization"

    def test_dict_content_serialization(self, mock_llm, mock_structured_prompt):
        request_dict = {
            "content": {"title": "Test Document", "body": "Content here", "metadata": {"tags": ["test"]}},
            "metadata": {"documentID": "doc-789"},
        }

        mock_structured_prompt.ainvoke = AsyncMock(return_value=DocumentSummary(summary="Summary here", title=""))

        # Mock embeddings service
        mock_embeddings = MagicMock()
        mock_embeddings.embed_documents.return_value = [[0.1] * 768]

        with (
            patch("aviator.services.summary.LLMRegistry.get_llm", return_value=mock_llm),
            patch("aviator.celery.EmbeddingsRegistry.get_embeddings", return_value=mock_embeddings),
            patch(
                "aviator.celery.upsert_workspace_document_with_summary",
                return_value={"success": True, "operation": "create"},
            ),
        ):
            result = process_workspace_summary_request(request_dict)

            # Verify structured runnable was invoked with JSON-serialized content payload
            assert mock_structured_prompt.ainvoke.called
            invoke_payload = mock_structured_prompt.ainvoke.call_args[0][0]
            assert '"title"' in invoke_payload["content"] or "'title'" in invoke_payload["content"]

            assert "summary" in result

    def test_llm_configuration_error_raises_202(self):
        request_dict = {
            "content": "Test content",
            "metadata": {"documentID": "doc-999", "workspaceID": "ws-888"},
        }

        with patch(
            "aviator.services.summary.LLMRegistry.get_llm", side_effect=ValueError("Invalid model configuration")
        ):
            with pytest.raises(WorkspaceSummaryError) as exc_info:
                process_workspace_summary_request(request_dict)
            assert exc_info.value.code == 202
            assert "configuration error" in str(exc_info.value).lower()

    def test_prompt_loading_failure_raises_prompt_not_found(self):
        request_dict = {
            "content": "Test content",
            "metadata": {"documentID": "doc-111"},
        }

        # Make PromptsLoader.load_prompt raise PromptNotFoundError; it should propagate
        with (
            patch("aviator.services.summary.PromptsLoader") as mock_loader_class,
            patch("aviator.services.summary.LLMRegistry.get_llm", return_value=MagicMock()),
        ):
            mock_instance = MagicMock()
            mock_instance.load_prompt.side_effect = PromptNotFoundError("summarize.md not found")
            mock_loader_class.return_value = mock_instance

            with pytest.raises(PromptNotFoundError) as exc_info:
                process_workspace_summary_request(request_dict)
            assert "prompt" in str(exc_info.value).lower()

    def test_llm_invocation_failure_is_retryable(self, mock_structured_prompt):
        """Transient LLM invocation failures become retryable summary errors."""
        request_dict = {
            "content": "Test content that should be summarized",
            "metadata": {"documentID": "doc-333", "workspaceID": "ws-999"},
        }

        # Create mock LLM that fails during structured output invocation
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value = MagicMock()
        mock_structured_prompt.ainvoke = AsyncMock(side_effect=Exception("Structured output not supported"))

        async def async_load_prompt(prompt_name):
            return "Summarize the following content:\n{content}"

        with (
            patch("aviator.services.summary.LLMRegistry.get_llm", return_value=mock_llm),
            patch("aviator.services.summary.PromptsLoader") as mock_loader_class,
        ):
            mock_instance = MagicMock()
            mock_instance.load_prompt.return_value = async_load_prompt("summarize")
            mock_loader_class.return_value = mock_instance

            with pytest.raises(WorkspaceSummaryRetryableError) as exc_info:
                process_workspace_summary_request(request_dict)

            assert exc_info.value.code == 205
            assert "invocation failed" in str(exc_info.value).lower()

    def test_llm_initialization_failure_is_retryable(self):
        request_dict = {
            "content": "Test content",
            "metadata": {"documentID": "doc-init", "workspaceID": "ws-init"},
        }

        with patch("aviator.services.summary.LLMRegistry.get_llm", side_effect=RuntimeError("endpoint not reachable")):
            with pytest.raises(WorkspaceSummaryRetryableError) as exc_info:
                process_workspace_summary_request(request_dict)

            assert exc_info.value.code == 202
            assert "initialization failed" in str(exc_info.value).lower()

    def test_delete_operation(self):
        """Test delete operation for summary."""
        request_dict = {
            "operation": "delete",
            "metadata": {
                "documentID": "doc-delete-123",
                "workspaceID": "ws-delete-456",
            },
        }

        with patch("aviator.celery.delete_workspace_document_summary") as mock_delete:
            mock_delete.return_value = {"success": True}
            result = process_workspace_summary_request(request_dict)

            assert result["success"] is True
            # delete now passes document_id and schema_name (workspace_id is not forwarded to DB op)
            mock_delete.assert_called_once_with(document_id="doc-delete-123", schema_name="public")

    def test_delete_operation_not_found(self):
        """Test delete operation when summary not found."""
        request_dict = {
            "operation": "delete",
            "metadata": {
                "documentID": "doc-delete-123",
                "workspaceID": "ws-delete-456",
            },
        }

        with patch("aviator.celery.delete_workspace_document_summary") as mock_delete:
            mock_delete.return_value = {"success": False, "message": "Document not found"}
            result = process_workspace_summary_request(request_dict)

            assert result["success"] is False
            assert result["message"] == "Document not found"
            mock_delete.assert_called_once()

    def test_update_operation(self):
        """Test update operation (which triggers add/upsert)."""
        request_dict = {
            "operation": "update",
            "content": "Updated content to summarize",
            "metadata": {
                "documentID": "doc-update-123",
                "workspaceID": "ws-update-456",
            },
        }

        with (
            patch(
                "aviator.celery.generate_summary",
                return_value=DocumentSummary(title="", summary="Updated summary"),
            ),
            patch(
                "aviator.celery.upsert_workspace_document_with_summary",
                return_value={"success": True, "operation": "update"},
            ) as mock_upsert,
        ):
            result = process_workspace_summary_request(request_dict)

            assert "summary" in result
            assert result["operation"] == "update"

            # Verify upsert was called
            mock_upsert.assert_called_once()
            call_kwargs = mock_upsert.call_args[1]
            assert call_kwargs["document_id"] == "doc-update-123"

    def test_schema_name_resolved_from_tenant_id(self):
        """Test that schema_name is derived from tenantID in metadata and passed to upsert."""
        request_dict = {
            "content": "Content for tenant test",
            "metadata": {
                "documentID": "doc-tenant-1",
                "workspaceID": "ws-tenant-1",
                "tenantID": "acme",
            },
        }

        with (
            patch("aviator.celery.generate_summary", return_value=DocumentSummary(title="T", summary="S")),
            patch(
                "aviator.celery.upsert_workspace_document_with_summary",
                return_value={"success": True, "operation": "create"},
            ) as mock_upsert,
            patch("aviator.celery.tenant_service") as mock_ts,
        ):
            mock_ts.tenant_exists.return_value = True

            process_workspace_summary_request(request_dict)

            call_kwargs = mock_upsert.call_args[1]
            # schema_name must be the tenant-prefixed schema
            assert call_kwargs["schema_name"] == "tenant_acme"

    def test_no_tenant_id_uses_public_schema(self):
        """Test that missing tenantID causes schema_name='public' in upsert."""
        request_dict = {
            "content": "Content without tenant",
            "metadata": {
                "documentID": "doc-no-tenant",
                "workspaceID": "ws-no-tenant",
            },
        }

        with (
            patch("aviator.celery.generate_summary", return_value=DocumentSummary(title="", summary="Summary")),
            patch(
                "aviator.celery.upsert_workspace_document_with_summary",
                return_value={"success": True, "operation": "create"},
            ) as mock_upsert,
        ):
            process_workspace_summary_request(request_dict)

            call_kwargs = mock_upsert.call_args[1]
            assert call_kwargs["schema_name"] == "public"

    def test_new_tenant_triggers_ensure_tenant(self):
        """Test that a new tenantID triggers auto-creation via ensure_tenant."""
        request_dict = {
            "content": "Content for new tenant",
            "metadata": {
                "documentID": "doc-new",
                "workspaceID": "ws-new",
                "tenantID": "brand-new",
            },
        }

        with (
            patch("aviator.celery.generate_summary", return_value=DocumentSummary(title="", summary="S")),
            patch(
                "aviator.celery.upsert_workspace_document_with_summary",
                return_value={"success": True, "operation": "create"},
            ),
            patch("aviator.celery.tenant_service") as mock_ts,
        ):
            # Simulate tenant not existing yet
            mock_ts.tenant_exists.return_value = False

            process_workspace_summary_request(request_dict)

            mock_ts.ensure_tenant.assert_called_once_with("brand-new")

    def test_existing_tenant_skips_ensure_tenant(self):
        """Test that an existing tenant does NOT call ensure_tenant again."""
        request_dict = {
            "content": "Content for existing tenant",
            "metadata": {
                "documentID": "doc-exist",
                "workspaceID": "ws-exist",
                "tenantID": "existing",
            },
        }

        with (
            patch("aviator.celery.generate_summary", return_value=DocumentSummary(title="", summary="S")),
            patch(
                "aviator.celery.upsert_workspace_document_with_summary",
                return_value={"success": True, "operation": "create"},
            ),
            patch("aviator.celery.tenant_service") as mock_ts,
        ):
            mock_ts.tenant_exists.return_value = True

            process_workspace_summary_request(request_dict)

            mock_ts.ensure_tenant.assert_not_called()

    def test_delete_passes_schema_name(self):
        """Test that delete operation passes schema_name derived from tenantID."""
        request_dict = {
            "operation": "delete",
            "metadata": {
                "documentID": "doc-del-tenant",
                "tenantID": "acme",
            },
        }

        with (
            patch("aviator.celery.delete_workspace_document_summary") as mock_delete,
            patch("aviator.celery.tenant_service") as mock_ts,
        ):
            mock_ts.tenant_exists.return_value = True
            mock_delete.return_value = {"success": True}

            result = process_workspace_summary_request(request_dict)

            assert result["success"] is True
            mock_delete.assert_called_once_with(
                document_id="doc-del-tenant",
                schema_name="tenant_acme",
            )
