"""Unit tests for database models."""

import os

from sqlalchemy import String, Text, inspect

# Set test environment before importing modules
os.environ["VECTOR_STORE"] = "memory"
os.environ["CHECKPOINTER"] = "memory"


class TestDocumentSummaryModel:
    """Tests for DocumentSummary model."""

    def test_table_name(self):
        """Test that table name is correctly set."""
        from aviator.database.models import DocumentSummary

        assert DocumentSummary.__tablename__ == "workspace_document_summaries"

    def test_primary_key_columns(self):
        """Test that document_id is the sole primary key."""
        from aviator.database.models import DocumentSummary

        mapper = inspect(DocumentSummary)
        primary_keys = [col.name for col in mapper.primary_key]

        assert primary_keys == ["document_id"]

    def test_document_id_column(self):
        """Test document_id column properties."""
        from sqlalchemy import String as SQLString

        from aviator.database.models import DocumentSummary

        document_id_col = DocumentSummary.__table__.columns["document_id"]

        assert document_id_col.primary_key is True
        assert isinstance(document_id_col.type, (String, SQLString))

    def test_summary_column(self):
        """Test summary column properties."""
        from sqlalchemy import Text as SQLText

        from aviator.database.models import DocumentSummary

        summary_col = DocumentSummary.__table__.columns["summary"]

        assert summary_col.nullable is False
        assert isinstance(summary_col.type, (Text, SQLText))

    def test_title_column(self):
        """Test title column properties."""
        from sqlalchemy import String as SQLString

        from aviator.database.models import DocumentSummary

        title_col = DocumentSummary.__table__.columns["title"]

        assert title_col.nullable is False
        assert isinstance(title_col.type, (String, SQLString))

    def test_title_embeddings_column(self):
        """Test title_embeddings column properties."""
        from pgvector.sqlalchemy import Vector

        from aviator.database.models import DocumentSummary
        from aviator.settings import settings

        title_embeddings_col = DocumentSummary.__table__.columns["title_embeddings"]

        assert title_embeddings_col.nullable is True
        assert isinstance(title_embeddings_col.type, Vector)
        assert title_embeddings_col.type.dim == settings.vector_size

    def test_model_has_all_required_columns(self):
        """Test that model has all required columns."""
        from aviator.database.models import DocumentSummary

        columns = DocumentSummary.__table__.columns.keys()

        assert "document_id" in columns
        assert "summary" in columns
        assert "title" in columns
        assert "title_embeddings" in columns
        assert len(columns) == 4

    def test_model_instantiation(self):
        """Test creating an instance of the model."""
        from aviator.database.models import DocumentSummary

        instance = DocumentSummary(
            document_id="doc-456",
            summary="This is a test summary",
            title="Test title",
        )

        assert instance.document_id == "doc-456"
        assert instance.summary == "This is a test summary"
        assert instance.title == "Test title"
        assert instance.title_embeddings is None

    def test_model_instantiation_with_embeddings(self):
        """Test creating an instance with title embeddings."""
        from aviator.database.models import DocumentSummary
        from aviator.settings import settings

        mock_embeddings = [0.1] * settings.vector_size

        instance = DocumentSummary(
            document_id="doc-456",
            summary="This is a test summary",
            title="Test title",
            title_embeddings=mock_embeddings,
        )

        assert instance.document_id == "doc-456"
        assert instance.summary == "This is a test summary"
        assert instance.title == "Test title"
        assert instance.title_embeddings == mock_embeddings
        assert len(instance.title_embeddings) == settings.vector_size

    def test_model_base_declarative(self):
        """Test that model uses declarative base."""
        from aviator.database.models import DocumentSummary

        assert hasattr(DocumentSummary, "__tablename__")
        assert hasattr(DocumentSummary, "__table__")
        assert hasattr(DocumentSummary, "__mapper__")

    def test_summary_nullable_constraint(self):
        """Test that summary column cannot be null."""
        from aviator.database.models import DocumentSummary

        summary_col = DocumentSummary.__table__.columns["summary"]
        assert summary_col.nullable is False

    def test_title_nullable_constraint(self):
        """Test that title column cannot be null."""
        from aviator.database.models import DocumentSummary

        title_col = DocumentSummary.__table__.columns["title"]
        assert title_col.nullable is False

    def test_title_embeddings_nullable_constraint(self):
        """Test that title_embeddings column can be null."""
        from aviator.database.models import DocumentSummary

        title_embeddings_col = DocumentSummary.__table__.columns["title_embeddings"]
        assert title_embeddings_col.nullable is True

    def test_model_representation_attributes(self):
        """Test that model instance has correct attributes."""
        from aviator.database.models import DocumentSummary

        instance = DocumentSummary(
            document_id="test-doc",
            summary="Test summary content",
            title="Test title content",
        )

        assert hasattr(instance, "document_id")
        assert hasattr(instance, "summary")
        assert hasattr(instance, "title")
        assert hasattr(instance, "title_embeddings")

        assert instance.document_id == "test-doc"
        assert instance.summary == "Test summary content"
        assert instance.title == "Test title content"
        assert instance.title_embeddings is None
