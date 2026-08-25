"""Tests for authentication dependency behavior."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from aviator.api.auth import require_authentication


class TestRequireAuthentication:
    """Test suite for require_authentication dependency factory."""

    @patch("aviator.api.auth.settings")
    def test_none_auth_handler_returns_tenant_id_in_dev_mode(self, mock_settings):
        """When no content system is configured, fallback auth may return tenant id in dev mode."""
        mock_settings.content_system = None
        mock_settings.dev_tools = True
        mock_settings.multi_tenant_enabled = True

        dependency = require_authentication()
        request = MagicMock()
        request.headers = {"tenantid": "tenant-1"}

        assert dependency(request) == {"tenantId": "tenant-1"}

    @patch("aviator.api.auth.settings")
    @patch("aviator.api.auth.plugins.get_auth_handler")
    def test_returns_plugin_handler_when_present(self, mock_get_auth_handler, mock_settings):
        """When content_system is configured and plugin exists, plugin dependency is returned."""
        plugin_handler = MagicMock()

        mock_settings.content_system = "otcm"
        mock_get_auth_handler.return_value = plugin_handler

        dependency = require_authentication("/v1/chat")

        assert dependency is plugin_handler
        mock_get_auth_handler.assert_called_once_with("/v1/chat")

    @patch("aviator.api.auth.settings")
    @patch("aviator.api.auth.plugins.get_auth_handler")
    def test_missing_plugin_handler_denies_request(self, mock_get_auth_handler, mock_settings):
        """When content_system is configured and plugin is missing, requests are denied."""
        mock_settings.content_system = "otcm"
        mock_get_auth_handler.return_value = None

        dependency = require_authentication("/v1/chat")

        with pytest.raises(HTTPException) as exc_info:
            dependency(MagicMock())

        assert exc_info.value.status_code == 401
        assert "Authentication failed for this request" in exc_info.value.detail
