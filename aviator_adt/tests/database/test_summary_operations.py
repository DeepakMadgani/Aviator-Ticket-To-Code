"""Unit tests for summary_operations module."""

import os
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError

from aviator.database.summary_operations import upsert_workspace_document_with_summary
from aviator.exceptions import WorkspaceSummaryError

# Set test environment before importing modules
os.environ["VECTOR_STORE"] = "memory"
os.environ["CHECKPOINTER"] = "memory"


class TestUpsertWorkspaceDocumentWithSummary:
    """Tests for upsert_workspace_document_with_summary function."""

    @pytest.fixture
    def mock_database_manager(self):
        """Mock the database manager singleton."""
        with patch("aviator.database.summary_operations.database_manager") as mock_mgr:
            mock_db = MagicMock()
            mock_session = MagicMock()
            mock_db.get_session.return_value = mock_session
            mock_mgr.get.return_value = mock_db
            yield mock_mgr, mock_db, mock_session

    @pytest.fixture
    def mock_model(self):
        """Mock DocumentSummary model."""
        with patch("aviator.database.summary_operations.DocumentSummary") as mock:
            yield mock

    def test_create_new_summary(self, mock_database_manager, mock_model):
        """Test creating a new document summary."""
        _, _, mock_session = mock_database_manager
        mock_session.query().filter_by().first.return_value = None

        result = upsert_workspace_document_with_summary(
            document_id="doc-456",
            summary_text="This is a test summary.",
            title="Test title",
        )

        assert result["success"] is True
        assert result["operation"] == "create"

        mock_model.assert_called_once_with(
            document_id="doc-456",
            summary="This is a test summary.",
            title="Test title",
            title_embeddings=None,
        )
        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()
        mock_session.close.assert_called_once()

    def test_update_existing_summary(self, mock_database_manager, mock_model):
        """Test updating an existing document summary."""
        _, _, mock_session = mock_database_manager
        from aviator.settings import settings

        existing_embeddings = [0.5] * settings.vector_size
        existing_record = MagicMock()
        existing_record.summary = "Old summary"
        existing_record.title = "Old title"
        existing_record.title_embeddings = existing_embeddings
        mock_session.query().filter_by().first.return_value = existing_record

        result = upsert_workspace_document_with_summary(
            document_id="doc-456",
            summary_text="Updated summary text.",
            title="Updated title",
            title_embeddings=None,
        )

        assert result["success"] is True
        assert result["operation"] == "update"
        assert existing_record.summary == "Updated summary text."
        assert existing_record.title == "Updated title"
        assert existing_record.title_embeddings == existing_embeddings
        mock_session.add.assert_not_called()
        mock_session.commit.assert_called_once()
        mock_session.close.assert_called_once()

        mock_session.reset_mock()

        new_embeddings = [0.9] * settings.vector_size
        result = upsert_workspace_document_with_summary(
            document_id="doc-456",
            summary_text="Another update.",
            title="Another title",
            title_embeddings=new_embeddings,
        )

        assert result["success"] is True
        assert existing_record.title_embeddings == new_embeddings

    def test_upsert_with_long_summary(self, mock_database_manager, mock_model):
        """Test upserting with a very long summary text."""
        _, _, mock_session = mock_database_manager
        mock_session.query().filter_by().first.return_value = None
        from aviator.settings import settings

        long_summary = "A" * 10000
        mock_embeddings = [0.1] * settings.vector_size

        result = upsert_workspace_document_with_summary(
            document_id="doc-long",
            summary_text=long_summary,
            title="Long summary title",
            title_embeddings=mock_embeddings,
        )

        assert result["success"] is True
        assert result["operation"] == "create"
        mock_session.commit.assert_called_once()
        call_kwargs = mock_model.call_args[1]
        assert call_kwargs["title_embeddings"] == mock_embeddings

    def test_upsert_with_special_characters(self, mock_database_manager, mock_model):
        """Test upserting with special characters in summary."""
        _, _, mock_session = mock_database_manager
        mock_session.query().filter_by().first.return_value = None

        special_summary = "Summary with 'quotes', \"double quotes\", and\nnewlines\t\ttabs"

        result = upsert_workspace_document_with_summary(
            document_id="doc-special",
            summary_text=special_summary,
            title="Special title",
        )

        assert result["success"] is True
        mock_model.assert_called_once()
        call_kwargs = mock_model.call_args[1]
        assert call_kwargs["summary"] == special_summary
        assert call_kwargs["title"] == "Special title"

    def test_database_error_triggers_rollback(self, mock_database_manager, mock_model):
        """Test that database errors trigger rollback."""
        _, _, mock_session = mock_database_manager
        mock_session.query().filter_by().first.return_value = None
        mock_session.commit.side_effect = SQLAlchemyError("Database error")

        with pytest.raises(WorkspaceSummaryError) as exc_info:
            upsert_workspace_document_with_summary(
                document_id="doc-error",
                summary_text="This will fail",
                title="Error title",
            )

        mock_session.rollback.assert_called_once()
        mock_session.close.assert_called_once()
        assert exc_info.value.code == 206

    def test_integrity_error_on_create(self, mock_database_manager, mock_model):
        """Test handling of integrity errors."""
        _, _, mock_session = mock_database_manager
        mock_session.query().filter_by().first.return_value = None
        mock_session.commit.side_effect = IntegrityError("Constraint violation", None, None)

        with pytest.raises(WorkspaceSummaryError) as exc_info:
            upsert_workspace_document_with_summary(
                document_id="doc-integrity",
                summary_text="Test",
                title="Integrity title",
            )

        assert exc_info.value.code == 206
        mock_session.rollback.assert_called_once()

    def test_operational_error_on_update(self, mock_database_manager, mock_model):
        """Test handling of operational errors."""
        _, _, mock_session = mock_database_manager

        existing_record = MagicMock()
        mock_session.query().filter_by().first.return_value = existing_record
        mock_session.commit.side_effect = OperationalError("Connection lost", None, None)

        with pytest.raises(WorkspaceSummaryError) as exc_info:
            upsert_workspace_document_with_summary(
                document_id="doc-ops-error",
                summary_text="Updated text",
                title="Ops title",
            )

        assert exc_info.value.code == 206
        mock_session.rollback.assert_called_once()

    def test_empty_summary_text(self, mock_database_manager, mock_model):
        """Test upserting with empty summary text."""
        _, _, mock_session = mock_database_manager
        mock_session.query().filter_by().first.return_value = None

        result = upsert_workspace_document_with_summary(
            document_id="doc-empty",
            summary_text="",
            title="",
            title_embeddings=None,
        )

        assert result["success"] is True
        call_kwargs = mock_model.call_args[1]
        assert call_kwargs["summary"] == ""
        assert call_kwargs["title"] == ""
        assert call_kwargs["title_embeddings"] is None

    def test_whitespace_only_summary(self, mock_database_manager, mock_model):
        """Test upserting with whitespace-only summary."""
        _, _, mock_session = mock_database_manager
        mock_session.query().filter_by().first.return_value = None

        result = upsert_workspace_document_with_summary(
            document_id="doc-whitespace",
            summary_text="   \n\t   ",
            title="   ",
        )

        assert result["success"] is True
        mock_model.assert_called_once()

    def test_session_closed_on_success(self, mock_database_manager, mock_model):
        """Test that session is closed on successful operation."""
        _, _, mock_session = mock_database_manager
        mock_session.query().filter_by().first.return_value = None

        upsert_workspace_document_with_summary(
            document_id="doc-close",
            summary_text="Test",
            title="Close title",
        )

        mock_session.close.assert_called_once()

    def test_session_closed_on_failure(self, mock_database_manager, mock_model):
        """Test that session is closed even on failure."""
        _, _, mock_session = mock_database_manager
        mock_session.query().filter_by().first.return_value = None
        mock_session.commit.side_effect = SQLAlchemyError("Unexpected error")

        with pytest.raises(WorkspaceSummaryError):
            upsert_workspace_document_with_summary(
                document_id="doc-fail",
                summary_text="Test",
                title="Failure title",
            )

        mock_session.close.assert_called_once()

    def test_filter_by_document_id(self, mock_database_manager, mock_model):
        """Test that query filters by document_id only."""
        _, _, mock_session = mock_database_manager
        mock_query = mock_session.query.return_value
        mock_filter = mock_query.filter_by.return_value
        mock_filter.first.return_value = None

        upsert_workspace_document_with_summary(
            document_id="doc-filter",
            summary_text="Test",
            title="Filter title",
        )

        mock_query.filter_by.assert_called_once_with(document_id="doc-filter")


class TestUpsertWithSchemaName:
    """Tests for schema_name parameter in upsert and delete operations."""

    @pytest.fixture
    def mock_db_schema(self):
        """Mock database manager with schema-aware get_session."""
        with patch("aviator.database.summary_operations.database_manager") as mock_mgr:
            mock_db = MagicMock()
            mock_session = MagicMock()
            mock_db.get_session.return_value = mock_session
            mock_mgr.get.return_value = mock_db
            yield mock_mgr, mock_db, mock_session

    def test_upsert_passes_schema_name_to_get_session(self, mock_db_schema):
        """Test that upsert passes schema_name to get_session for tenant routing."""
        _, mock_db, mock_session = mock_db_schema
        mock_session.query().filter_by().first.return_value = None

        with patch("aviator.database.summary_operations.DocumentSummary"):
            upsert_workspace_document_with_summary(
                document_id="doc-tenant",
                summary_text="Tenant summary",
                title="Tenant title",
                schema_name="tenant_acme",
            )

        mock_db.get_session.assert_called_once_with("tenant_acme")

    def test_upsert_without_schema_uses_none(self, mock_db_schema):
        """Test that upsert without schema_name passes None to get_session."""
        _, mock_db, mock_session = mock_db_schema
        mock_session.query().filter_by().first.return_value = None

        with patch("aviator.database.summary_operations.DocumentSummary"):
            upsert_workspace_document_with_summary(
                document_id="doc-default",
                summary_text="Default summary",
                title="Default title",
            )

        mock_db.get_session.assert_called_once_with(None)

    def test_delete_passes_schema_name_to_get_session(self, mock_db_schema):
        """Test that delete passes schema_name to get_session for tenant routing."""
        _, mock_db, mock_session = mock_db_schema
        mock_session.query().filter_by().delete.return_value = 1

        from aviator.database.summary_operations import delete_workspace_document_summary

        delete_workspace_document_summary(document_id="doc-to-delete", schema_name="tenant_xyz")

        mock_db.get_session.assert_called_once_with("tenant_xyz")

    def test_delete_without_schema_uses_none(self, mock_db_schema):
        """Test that delete without schema_name passes None to get_session."""
        _, mock_db, _mock_session = mock_db_schema

        from aviator.database.summary_operations import delete_workspace_document_summary

        delete_workspace_document_summary(document_id="doc-delete-default")

        mock_db.get_session.assert_called_once_with(None)

    def test_delete_success(self, mock_db_schema):
        """Test that delete returns success dict."""
        _, _, mock_session = mock_db_schema

        from aviator.database.summary_operations import delete_workspace_document_summary

        result = delete_workspace_document_summary(document_id="doc-del", schema_name="tenant_abc")

        assert result["success"] is True
        mock_session.commit.assert_called_once()
        mock_session.close.assert_called_once()

    def test_delete_db_error_raises_workspace_summary_error(self, mock_db_schema):
        """Test that delete raises WorkspaceSummaryError on SQLAlchemy failure."""
        _, _, mock_session = mock_db_schema
        mock_session.commit.side_effect = SQLAlchemyError("DB failure")

        from aviator.database.summary_operations import delete_workspace_document_summary

        with pytest.raises(WorkspaceSummaryError) as exc_info:
            delete_workspace_document_summary(document_id="doc-err", schema_name="tenant_err")

        assert exc_info.value.code == 206
        mock_session.rollback.assert_called_once()
        mock_session.close.assert_called_once()


class TestBulkUpsertWorkspaceDocumentsWithSummary:
    """Tests for bulk_upsert_workspace_documents_with_summary function."""

    @pytest.fixture
    def mock_database_manager(self):
        """Mock the database manager singleton."""
        with patch("aviator.database.summary_operations.database_manager") as mock_mgr:
            mock_db = MagicMock()
            mock_session = MagicMock()
            mock_db.get_session.return_value = mock_session
            mock_mgr.get.return_value = mock_db
            yield mock_mgr, mock_db, mock_session

    def test_empty_rows_returns_zero(self):
        """Test that empty rows list returns 0 without touching the database."""
        from aviator.database.summary_operations import bulk_upsert_workspace_documents_with_summary

        result = bulk_upsert_workspace_documents_with_summary(rows=[], schema_name="public")
        assert result == 0

    def test_inserts_rows_and_commits(self, mock_database_manager):
        """Test that rows are inserted via pg_insert and committed."""
        from aviator.database.summary_operations import bulk_upsert_workspace_documents_with_summary

        _, _, mock_session = mock_database_manager

        rows = [
            {
                "document_id": "doc-1",
                "summary": "Summary one",
                "title": "Title one",
                "title_embeddings": [0.1, 0.2],
            },
            {
                "document_id": "doc-2",
                "summary": "Summary two",
                "title": "Title two",
                "title_embeddings": None,
            },
        ]

        result = bulk_upsert_workspace_documents_with_summary(rows=rows, schema_name="tenant_x")

        assert result == 2
        mock_session.execute.assert_called_once()
        mock_session.commit.assert_called_once()
        mock_session.close.assert_called_once()

    def test_passes_schema_to_get_session(self, mock_database_manager):
        """Test that schema_name is forwarded to get_session."""
        from aviator.database.summary_operations import bulk_upsert_workspace_documents_with_summary

        _, mock_db, _ = mock_database_manager

        bulk_upsert_workspace_documents_with_summary(
            rows=[{"document_id": "d", "summary": "S", "title": "T", "title_embeddings": None}],
            schema_name="tenant_xyz",
        )

        mock_db.get_session.assert_called_once_with("tenant_xyz")

    def test_database_error_triggers_rollback(self, mock_database_manager):
        """Test that SQLAlchemy errors cause rollback and raise WorkspaceSummaryError."""
        from aviator.database.summary_operations import bulk_upsert_workspace_documents_with_summary

        _, _, mock_session = mock_database_manager
        mock_session.execute.side_effect = SQLAlchemyError("Connection lost")

        rows = [{"document_id": "d", "summary": "S", "title": "T", "title_embeddings": None}]

        with pytest.raises(WorkspaceSummaryError) as exc_info:
            bulk_upsert_workspace_documents_with_summary(rows=rows, schema_name="public")

        assert exc_info.value.code == 206
        mock_session.rollback.assert_called_once()
        mock_session.close.assert_called_once()

    def test_session_closed_on_success(self, mock_database_manager):
        """Test that session is always closed after successful operation."""
        from aviator.database.summary_operations import bulk_upsert_workspace_documents_with_summary

        _, _, mock_session = mock_database_manager

        bulk_upsert_workspace_documents_with_summary(
            rows=[{"document_id": "d", "summary": "S", "title": "T", "title_embeddings": None}],
        )

        mock_session.close.assert_called_once()

    def test_session_closed_on_failure(self, mock_database_manager):
        """Test that session is closed even when an error occurs."""
        from aviator.database.summary_operations import bulk_upsert_workspace_documents_with_summary

        _, _, mock_session = mock_database_manager
        mock_session.execute.side_effect = SQLAlchemyError("boom")

        with pytest.raises(WorkspaceSummaryError):
            bulk_upsert_workspace_documents_with_summary(
                rows=[{"document_id": "d", "summary": "S", "title": "T", "title_embeddings": None}],
            )

        mock_session.close.assert_called_once()
