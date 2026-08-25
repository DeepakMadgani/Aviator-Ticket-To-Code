"""Tests for docdata_retrieval service."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aviator.services import docdata_retrieval


def _build_mock_pool(rows=None, execute_side_effect=None):
    mock_cur = AsyncMock()
    mock_cur.execute = AsyncMock(side_effect=execute_side_effect)
    mock_cur.fetchall = AsyncMock(return_value=rows or [])
    mock_cur_cm = AsyncMock()
    mock_cur_cm.__aenter__.return_value = mock_cur
    mock_cur_cm.__aexit__.return_value = None

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur_cm
    mock_conn_cm = AsyncMock()
    mock_conn_cm.__aenter__.return_value = mock_conn
    mock_conn_cm.__aexit__.return_value = None

    mock_pool = MagicMock()
    mock_pool.connection.return_value = mock_conn_cm
    return mock_pool, mock_cur


class TestInClause:
    """Tests for _in_clause helper."""

    def test_in_clause_with_ids(self):
        result = docdata_retrieval._in_clause(["a", "b", "c"])

        assert result == "%s, %s, %s"


class TestExecuteQuery:
    """Tests for _execute_query helper."""

    @pytest.mark.anyio
    async def test_execute_query_success(self):
        mock_pool, mock_cur = _build_mock_pool(rows=[("doc-1", "summary")])

        with patch(
            "aviator.services.docdata_retrieval.DatabaseManager.get_pool",
            new_callable=AsyncMock,
            return_value=mock_pool,
        ) as mock_get_pool:
            rows = await docdata_retrieval._execute_query("SELECT 1", ["doc-1"], "test")

        assert rows == [("doc-1", "summary")]
        mock_get_pool.assert_awaited_once()
        mock_cur.execute.assert_awaited_once_with("SELECT 1", ["doc-1"])
        mock_cur.fetchall.assert_awaited_once()

    @pytest.mark.anyio
    async def test_execute_query_returns_empty_on_error(self):
        mock_pool, _mock_cur = _build_mock_pool(execute_side_effect=RuntimeError("boom"))

        with patch(
            "aviator.services.docdata_retrieval.DatabaseManager.get_pool",
            new_callable=AsyncMock,
            return_value=mock_pool,
        ):
            rows = await docdata_retrieval._execute_query("SELECT 1", ["doc-1"], "test")

        assert rows == []


class TestQualifiedTable:
    """Tests for _qualified_table helper."""

    def test_with_schema_name(self):
        result = docdata_retrieval._qualified_table("tenant_acme")

        assert result == f"tenant_acme.{docdata_retrieval.TABLE_NAME}"

    def test_without_schema_name(self):
        result = docdata_retrieval._qualified_table()

        assert result == docdata_retrieval.TABLE_NAME

    def test_none_schema_name(self):
        result = docdata_retrieval._qualified_table(None)

        assert result == docdata_retrieval.TABLE_NAME


class TestRetrieveSummariesByDocIds:
    """Tests for retrieve_summaries_by_doc_ids."""

    @pytest.mark.anyio
    async def test_returns_empty_when_no_ids(self):
        with patch(
            "aviator.services.docdata_retrieval.DatabaseManager.get_pool",
            new_callable=AsyncMock,
        ) as mock_get_pool:
            result = await docdata_retrieval.retrieve_summaries_by_doc_ids([])

        assert result == []
        mock_get_pool.assert_not_called()

    @pytest.mark.anyio
    async def test_returns_summary_rows(self):
        mock_pool, _mock_cur = _build_mock_pool(rows=[("doc-1", "Summary text")])

        with patch(
            "aviator.services.docdata_retrieval.DatabaseManager.get_pool",
            new_callable=AsyncMock,
            return_value=mock_pool,
        ):
            result = await docdata_retrieval.retrieve_summaries_by_doc_ids(["doc-1"])

        assert result == [{"document_id": "doc-1", "summary": "Summary text"}]

    @pytest.mark.anyio
    async def test_uses_schema_qualified_table(self):
        mock_pool, mock_cur = _build_mock_pool(rows=[("doc-1", "Summary text")])

        with patch(
            "aviator.services.docdata_retrieval.DatabaseManager.get_pool",
            new_callable=AsyncMock,
            return_value=mock_pool,
        ):
            await docdata_retrieval.retrieve_summaries_by_doc_ids(["doc-1"], schema_name="tenant_acme")

        executed_query = mock_cur.execute.call_args[0][0]
        assert "tenant_acme." in executed_query


class TestRetrieveTitlesByDocIds:
    """Tests for retrieve_titles_by_doc_ids."""

    @pytest.mark.anyio
    async def test_returns_empty_when_no_ids(self):
        with patch(
            "aviator.services.docdata_retrieval.DatabaseManager.get_pool",
            new_callable=AsyncMock,
        ) as mock_get_pool:
            result = await docdata_retrieval.retrieve_titles_by_doc_ids([])

        assert result == []
        mock_get_pool.assert_not_called()

    @pytest.mark.anyio
    async def test_returns_title_rows(self):
        mock_pool, _mock_cur = _build_mock_pool(rows=[("doc-1", "Doc Title")])

        with patch(
            "aviator.services.docdata_retrieval.DatabaseManager.get_pool",
            new_callable=AsyncMock,
            return_value=mock_pool,
        ):
            result = await docdata_retrieval.retrieve_titles_by_doc_ids(["doc-1"])

        assert result == [{"document_id": "doc-1", "summary": "Doc Title"}]

    @pytest.mark.anyio
    async def test_uses_schema_qualified_table(self):
        mock_pool, mock_cur = _build_mock_pool(rows=[("doc-1", "Doc Title")])

        with patch(
            "aviator.services.docdata_retrieval.DatabaseManager.get_pool",
            new_callable=AsyncMock,
            return_value=mock_pool,
        ):
            await docdata_retrieval.retrieve_titles_by_doc_ids(["doc-1"], schema_name="tenant_acme")

        executed_query = mock_cur.execute.call_args[0][0]
        assert "tenant_acme." in executed_query
