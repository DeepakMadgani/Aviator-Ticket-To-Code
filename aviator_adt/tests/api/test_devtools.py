"""Tests for the DevTools API endpoint."""

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


class TestGetDocumentChunks:
    """Tests for GET /v1/document-chunks/{document_id}."""

    @patch("aviator.services.devtools.fetch_one", new_callable=AsyncMock)
    def test_returns_chunk_count_and_summary(self, mock_fetch_one, client: TestClient):
        mock_fetch_one.side_effect = [{"cnt": 5}, {"1": 1}]

        response = client.get("/v1/document-chunks/doc123")

        assert response.status_code == 200
        data = response.json()
        assert data["document_id"] == "doc123"
        assert data["exists"] is True
        assert data["chunk_count"] == 5
        assert data["summary"] is True

    @patch("aviator.services.devtools.fetch_one", new_callable=AsyncMock)
    def test_returns_zero_when_no_chunks(self, mock_fetch_one, client: TestClient):
        mock_fetch_one.side_effect = [{"cnt": 0}, None]

        response = client.get("/v1/document-chunks/nonexistent")

        assert response.status_code == 200
        data = response.json()
        assert data["document_id"] == "nonexistent"
        assert data["exists"] is False
        assert data["chunk_count"] == 0
        assert data["summary"] is False

    @patch("aviator.services.devtools.fetch_one", new_callable=AsyncMock)
    def test_returns_zero_when_fetch_returns_none(self, mock_fetch_one, client: TestClient):
        mock_fetch_one.side_effect = [None, None]

        response = client.get("/v1/document-chunks/doc456")

        assert response.status_code == 200
        data = response.json()
        assert data["exists"] is False
        assert data["chunk_count"] == 0
        assert data["summary"] is False

    @patch("aviator.services.devtools.fetch_one", new_callable=AsyncMock)
    def test_chunks_exist_but_no_summary(self, mock_fetch_one, client: TestClient):
        mock_fetch_one.side_effect = [{"cnt": 3}, None]

        response = client.get("/v1/document-chunks/doc789")

        assert response.status_code == 200
        data = response.json()
        assert data["document_id"] == "doc789"
        assert data["exists"] is True
        assert data["chunk_count"] == 3
        assert data["summary"] is False
