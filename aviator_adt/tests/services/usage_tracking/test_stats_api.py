"""Tests for the /v1/usage-stats API endpoint."""

from datetime import date
from unittest.mock import AsyncMock, patch

from aviator.api.stats import _build_stats_series, _generate_date_buckets
from aviator.services.usage_tracking.metrics import semantic_chunks_total, semantic_documents_total
from aviator.services.usage_tracking.models import DailyTallyModel, SemanticSizeModel


class TestGenerateDateBuckets:
    """Tests for _generate_date_buckets helper."""

    def test_days(self):
        keys = _generate_date_buckets(date(2024, 1, 1), date(2024, 1, 3), "days")
        assert keys == ["2024-01-01", "2024-01-02", "2024-01-03"]

    def test_single_day(self):
        keys = _generate_date_buckets(date(2024, 6, 15), date(2024, 6, 15), "days")
        assert keys == ["2024-06-15"]

    def test_months(self):
        keys = _generate_date_buckets(date(2024, 1, 1), date(2024, 3, 31), "months")
        assert keys == ["2024-01", "2024-02", "2024-03"]

    def test_months_cross_year(self):
        keys = _generate_date_buckets(date(2023, 11, 1), date(2024, 2, 28), "months")
        assert keys == ["2023-11", "2023-12", "2024-01", "2024-02"]

    def test_years(self):
        keys = _generate_date_buckets(date(2022, 1, 1), date(2024, 12, 31), "years")
        assert keys == ["2022", "2023", "2024"]


class TestBuildStatsSeries:
    """Tests for _build_stats_series helper."""

    def test_empty_tallies_produce_zeroed_rows(self):
        data = _build_stats_series([], date(2024, 1, 1), date(2024, 1, 3), "days")
        assert len(data) == 3
        for row in data:
            assert row["chatCount"] == 0
            assert row["directChatCount"] == 0
            assert row["embeddingsRequestCount"] == 0
            assert row["chunksDeletedCount"] == 0
            assert row["documentsDeletedCount"] == 0
            assert row["input_tokens"] == 0
            assert row["output_tokens"] == 0
            assert row["llm_total_requests"] == 0

    def test_chat_tallies_mapped(self):
        tallies = [
            DailyTallyModel(
                date="2024-01-01",
                transaction_type="chat",
                total_count=5,
                input_tokens=120,
                output_tokens=30,
                llm_total_requests=6,
            ),
        ]
        data = _build_stats_series(tallies, date(2024, 1, 1), date(2024, 1, 2), "days")
        assert data[0]["chatCount"] == 5
        assert data[0]["input_tokens"] == 120
        assert data[0]["output_tokens"] == 30
        assert data[0]["llm_total_requests"] == 6
        assert data[1]["chatCount"] == 0

    def test_direct_chat_tallies_mapped(self):
        tallies = [
            DailyTallyModel(date="2024-01-01", transaction_type="direct_chat", total_count=3),
        ]
        data = _build_stats_series(tallies, date(2024, 1, 1), date(2024, 1, 1), "days")
        assert data[0]["directChatCount"] == 3

    def test_embedding_add_tallies_mapped(self):
        tallies = [
            DailyTallyModel(
                date="2024-01-01",
                transaction_type="embedding_add",
                total_count=2,
                total_documents=1,
                total_chunks=10,
            ),
        ]
        data = _build_stats_series(tallies, date(2024, 1, 1), date(2024, 1, 1), "days")
        assert data[0]["embeddingsRequestCount"] == 2
        assert data[0]["chunksCount"] == 10
        assert data[0]["documentsEmbeddedCount"] == 1

    def test_search_query_tallies_mapped(self):
        tallies = [
            DailyTallyModel(date="2024-01-01", transaction_type="search_query", total_count=7),
        ]
        data = _build_stats_series(tallies, date(2024, 1, 1), date(2024, 1, 1), "days")
        assert data[0]["semanticQueryCount"] == 7
        # search_query should NOT add to embeddingsRequestCount
        assert data[0]["embeddingsRequestCount"] == 0

    def test_embedding_delete_tallies_mapped(self):
        tallies = [
            DailyTallyModel(
                date="2024-01-01",
                transaction_type="embedding_delete",
                total_count=1,
                total_documents=1,
                total_chunks=5,
            ),
        ]
        data = _build_stats_series(tallies, date(2024, 1, 1), date(2024, 1, 1), "days")
        assert data[0]["embeddingsRequestCount"] == 0
        assert data[0]["chunksDeletedCount"] == 5
        assert data[0]["documentsDeletedCount"] == 1
        assert data[0]["chunksCount"] == 0
        assert data[0]["documentsEmbeddedCount"] == 0

    def test_embedding_update_tallies_mapped(self):
        tallies = [
            DailyTallyModel(
                date="2024-01-01",
                transaction_type="embedding_update",
                total_count=1,
                total_documents=1,
                total_chunks=3,
            ),
        ]
        data = _build_stats_series(tallies, date(2024, 1, 1), date(2024, 1, 1), "days")
        assert data[0]["embeddingsRequestCount"] == 1
        assert data[0]["chunksCount"] == 3
        assert data[0]["documentsEmbeddedCount"] == 1
        assert data[0]["chunksDeletedCount"] == 0
        assert data[0]["documentsDeletedCount"] == 0

    def test_chat_ws_tallies_mapped_to_chat_count(self):
        tallies = [
            DailyTallyModel(date="2024-01-01", transaction_type="chat", total_count=3),
            DailyTallyModel(date="2024-01-01", transaction_type="chat_ws", total_count=2),
        ]
        data = _build_stats_series(tallies, date(2024, 1, 1), date(2024, 1, 1), "days")
        assert data[0]["chatCount"] == 5

    def test_monthly_buckets(self):
        tallies = [
            DailyTallyModel(date="2024-01", transaction_type="chat", total_count=100),
        ]
        data = _build_stats_series(tallies, date(2024, 1, 1), date(2024, 3, 31), "months")
        assert len(data) == 3
        assert data[0]["chatCount"] == 100
        assert data[1]["chatCount"] == 0
        assert data[2]["chatCount"] == 0


class TestGetStatsEndpoint:
    """Tests for the GET /v1/usage-stats endpoint via TestClient."""

    @patch("aviator.api.stats.settings")
    def test_returns_501_when_disabled(self, mock_settings, client):
        mock_settings.usage_tracking_enabled = False
        response = client.get("/v1/usage-stats")
        assert response.status_code == 501

    @patch("aviator.api.stats.get_semantic_size", new_callable=AsyncMock)
    @patch("aviator.api.stats.get_usage_stats", new_callable=AsyncMock)
    @patch("aviator.api.stats.settings")
    def test_returns_stats_with_defaults(self, mock_settings, mock_usage_stats, mock_semantic_size, client):
        mock_settings.usage_tracking_enabled = True
        mock_settings.usage_tracking_max_query_days = 1000
        mock_settings.default_schema = "public"
        mock_usage_stats.return_value = []
        mock_semantic_size.return_value = SemanticSizeModel(total_documents=5, total_chunks=50)

        response = client.get("/v1/usage-stats")
        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert "semanticSize" in data
        assert data["semanticSize"]["documentsEmbeddedTotal"] == 5
        assert data["semanticSize"]["chunksTotal"] == 50
        assert "storedChunksMb" not in data["semanticSize"]
        assert data["timezone"] == "UTC"

        # Verify Prometheus gauges were updated
        assert semantic_documents_total.labels(tenant_id="public")._value.get() == 5.0
        assert semantic_chunks_total.labels(tenant_id="public")._value.get() == 50.0

    @patch("aviator.api.stats.get_semantic_size", new_callable=AsyncMock)
    @patch("aviator.api.stats.get_usage_stats", new_callable=AsyncMock)
    @patch("aviator.api.stats.settings")
    def test_returns_400_on_conflicting_params(self, mock_settings, mock_usage_stats, mock_semantic_size, client):
        mock_settings.usage_tracking_enabled = True
        mock_settings.usage_tracking_max_query_days = 1000

        response = client.get("/v1/usage-stats?from_offset=-7&from_date=2024-01-01")
        assert response.status_code == 400

    @patch("aviator.api.stats.get_semantic_size", new_callable=AsyncMock)
    @patch("aviator.api.stats.get_usage_stats", new_callable=AsyncMock)
    @patch("aviator.api.stats.settings")
    def test_uses_tenant_from_query(self, mock_settings, mock_usage_stats, mock_semantic_size, client):
        mock_settings.usage_tracking_enabled = True
        mock_settings.usage_tracking_max_query_days = 1000
        mock_settings.default_schema = "public"
        mock_usage_stats.return_value = []
        mock_semantic_size.return_value = SemanticSizeModel()

        response = client.get("/v1/usage-stats?tenant_id=acme&from_offset=-7")
        assert response.status_code == 200
        data = response.json()
        assert data["tenantId"] == "acme"

    @patch("aviator.api.stats.settings")
    def test_returns_400_for_invalid_from_date_format(self, mock_settings, client):
        mock_settings.usage_tracking_enabled = True
        response = client.get("/v1/usage-stats?from_date=2024-1")
        assert response.status_code == 400
        assert "from_date" in response.json()["detail"]
        assert "YYYY-MM-DD" in response.json()["detail"]

    @patch("aviator.api.stats.settings")
    def test_returns_400_for_invalid_to_date_format(self, mock_settings, client):
        mock_settings.usage_tracking_enabled = True
        response = client.get("/v1/usage-stats?to_date=not-a-date")
        assert response.status_code == 400
        assert "to_date" in response.json()["detail"]
        assert "YYYY-MM-DD" in response.json()["detail"]
