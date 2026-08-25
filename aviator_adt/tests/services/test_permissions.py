"""Tests for permission filtering service."""

from unittest.mock import MagicMock, patch

import pytest

from aviator.models import Chunk
from aviator.services.permissions import apply_rag_permission_filter


class TestApplyRagPermissionFilter:
    """Test suite for apply_rag_permission_filter helper."""

    @pytest.mark.anyio
    async def test_no_content_system_passes_all_items(self):
        """When no content system is configured, all items pass through."""
        items = [
            Chunk(chunk_id="c1", text="text1", document_id="d1"),
            Chunk(chunk_id="c2", text="text2", document_id="d2"),
        ]

        with patch("aviator.services.permissions.settings") as mock_settings:
            mock_settings.content_system = None
            result = await apply_rag_permission_filter(items, None)

            assert result == items

    @pytest.mark.anyio
    async def test_missing_plugin_raises_403(self):
        """When content_system is configured but no matching plugin exists, raise 403."""
        from fastapi import HTTPException

        items = [
            Chunk(chunk_id="c1", text="text1", document_id="d1"),
        ]

        with (
            patch("aviator.services.permissions.settings") as mock_settings,
            patch("aviator.services.permissions.load_rag_permission_filters", return_value=[]),
        ):
            mock_settings.content_system = "test_system"
            with pytest.raises(HTTPException) as exc_info:
                await apply_rag_permission_filter(items, {"id": "user-1"})
            assert exc_info.value.status_code == 403

    @pytest.mark.anyio
    async def test_filter_plugin_filters_items(self):
        """When plugin exists and filters successfully, return filtered items."""
        items = [
            Chunk(chunk_id="c1", text="text1", document_id="d1"),
            Chunk(chunk_id="c2", text="text2", document_id="d2"),
        ]
        filtered_items = [items[0]]  # Only first chunk passes

        def mock_filter(chunks, user):
            return filtered_items

        with (
            patch("aviator.services.permissions.settings") as mock_settings,
            patch(
                "aviator.services.permissions.load_rag_permission_filters",
                return_value=[("test_system", mock_filter)],
            ),
        ):
            mock_settings.content_system = "test_system"
            result = await apply_rag_permission_filter(items, {"id": "user-1"})

            assert result == filtered_items

    @pytest.mark.anyio
    async def test_filter_plugin_exception_returns_empty_list(self):
        """When filter plugin raises exception, fail-closed with empty list."""
        items = [
            Chunk(chunk_id="c1", text="text1", document_id="d1"),
        ]

        def failing_filter(chunks, user):
            raise RuntimeError("permission backend unavailable")

        with (
            patch("aviator.services.permissions.settings") as mock_settings,
            patch(
                "aviator.services.permissions.load_rag_permission_filters",
                return_value=[("test_system", failing_filter)],
            ),
        ):
            mock_settings.content_system = "test_system"
            result = await apply_rag_permission_filter(items, {"id": "user-1"})

            assert result == []

    @pytest.mark.anyio
    async def test_async_filter_plugin_works(self):
        """When filter plugin is async, it's awaited correctly."""
        items = [
            Chunk(chunk_id="c1", text="text1", document_id="d1"),
        ]

        async def async_filter(chunks, user):
            return chunks

        with (
            patch("aviator.services.permissions.settings") as mock_settings,
            patch(
                "aviator.services.permissions.load_rag_permission_filters",
                return_value=[("test_system", async_filter)],
            ),
        ):
            mock_settings.content_system = "test_system"
            result = await apply_rag_permission_filter(items, {"id": "user-1"})

            assert result == items

    @pytest.mark.anyio
    async def test_custom_span_name_is_used(self):
        """Custom span name is used in tracing."""
        items = [Chunk(chunk_id="c1", text="text1", document_id="d1")]

        def mock_filter(chunks, user):
            return chunks

        with (
            patch("aviator.services.permissions.settings") as mock_settings,
            patch("aviator.services.permissions.tracer") as mock_tracer,
            patch(
                "aviator.services.permissions.load_rag_permission_filters",
                return_value=[("test_system", mock_filter)],
            ),
        ):
            mock_settings.content_system = "test_system"
            mock_span = MagicMock()
            mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
            mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

            await apply_rag_permission_filter(items, None, span_name="custom_span")

            mock_tracer.start_as_current_span.assert_called_once()
            call_arg = mock_tracer.start_as_current_span.call_args[0][0]
            assert "custom_span" in call_arg
