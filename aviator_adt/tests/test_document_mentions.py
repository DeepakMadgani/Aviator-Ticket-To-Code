"""Tests for document mention parsing functionality."""

from aviator.models import WhereClauseReferenceModel
from aviator.utils.document_mentions import extract_document_mentions


class TestDocumentMentionParsing:
    """Test suite for document mention extraction."""

    def test_extract_single_document_with_spaces(self):
        """Test parsing a single document mention with spaces around ID."""
        message = "Can you summarize /report.pdf (ID: 123-456)?"
        refs = extract_document_mentions(message)

        assert len(refs) == 1
        assert refs[0].document_id == "123-456"

    def test_extract_single_document_without_spaces(self):
        """Test parsing a single document mention without spaces."""
        message = "Check /analysis.docx(ID: 789) for details"
        refs = extract_document_mentions(message)

        assert len(refs) == 1
        assert refs[0].document_id == "789"

    def test_extract_multiple_documents(self):
        """Test parsing multiple document mentions in one message."""
        message = "Compare /doc1.pdf (ID: 111) and /doc2.pdf(ID: 222)"
        refs = extract_document_mentions(message)

        assert len(refs) == 2
        assert refs[0].document_id == "111"
        assert refs[1].document_id == "222"

    def test_no_document_mentions(self):
        """Test message with no document mentions."""
        message = "What is the weather today?"
        refs = extract_document_mentions(message)

        assert len(refs) == 0

    def test_case_insensitive_id_keyword(self):
        """Test that ID keyword is case insensitive."""
        message = "Check /file.txt (id: 999)"
        refs = extract_document_mentions(message)

        assert len(refs) == 1
        assert refs[0].document_id == "999"

    def test_document_id_with_uuid(self):
        """Test document IDs with UUID format (common in production)."""
        message = "/doc.pdf (ID: 90b6124e-ff0c-4b58-9afc-dc6792ea0593)"
        refs = extract_document_mentions(message)

        assert len(refs) == 1
        assert refs[0].document_id == "90b6124e-ff0c-4b58-9afc-dc6792ea0593"

    def test_returns_document_reference_model(self):
        """Test that returned objects are WhereClauseReferenceModel instances."""
        message = "Check /file.pdf (ID: 123)"
        refs = extract_document_mentions(message)

        assert len(refs) == 1
        assert isinstance(refs[0], WhereClauseReferenceModel)

    def test_incomplete_pattern_not_matched(self):
        """Test that incomplete patterns are not matched."""
        incomplete_patterns = [
            "/file.pdf (ID:",
            "/file.pdf ID: 123)",
            "/file.pdf (123)",
            "file.pdf (ID: 123)",  # Missing leading slash
        ]

        for pattern in incomplete_patterns:
            refs = extract_document_mentions(pattern)
            assert len(refs) == 0, f"Should not match: {pattern}"
