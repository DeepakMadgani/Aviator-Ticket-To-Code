"""Tests for plugin auth handler discovery."""

from unittest.mock import MagicMock, patch

from aviator.plugins import get_auth_handler


class TestGetAuthHandler:
    """Test suite for get_auth_handler plugin discovery."""

    def _make_entry_point(self, name, load_return):
        """Create a mock entry point."""
        ep = MagicMock()
        ep.name = name
        ep.value = f"some_module:{name}"
        ep.load.return_value = load_return
        return ep

    @patch("aviator.plugins.settings")
    @patch("aviator.plugins.importlib.metadata.entry_points")
    def test_returns_callable_when_no_endpoint_param(self, mock_entry_points, mock_settings):
        """Handler without 'endpoint' param is returned as-is."""
        mock_settings.content_system = "test_system"

        def my_handler():
            return "authenticated"

        eps = MagicMock()
        eps.select.return_value = [self._make_entry_point("test_system", my_handler)]
        mock_entry_points.return_value = eps

        result = get_auth_handler(endpoint="/v1/chat")

        assert result is my_handler

    @patch("aviator.plugins.settings")
    @patch("aviator.plugins.importlib.metadata.entry_points")
    def test_invokes_handler_accepting_endpoint_param(self, mock_entry_points, mock_settings):
        """Handler with 'endpoint' param is called as a factory and its return value is used."""
        mock_settings.content_system = "test_system"

        def my_handler(endpoint=None):
            return f"auth_dep_for_{endpoint}"

        eps = MagicMock()
        eps.select.return_value = [self._make_entry_point("test_system", my_handler)]
        mock_entry_points.return_value = eps

        result = get_auth_handler(endpoint="/v1/chat")

        assert result == "auth_dep_for_/v1/chat"

    @patch("aviator.plugins.settings")
    @patch("aviator.plugins.importlib.metadata.entry_points")
    def test_returns_none_when_no_matching_handler(self, mock_entry_points, mock_settings):
        """Returns None when no entry point matches the content_system."""
        mock_settings.content_system = "unknown_system"

        eps = MagicMock()
        eps.select.return_value = [self._make_entry_point("other_system", lambda: None)]
        mock_entry_points.return_value = eps

        result = get_auth_handler()

        assert result is None

    @patch("aviator.plugins.settings")
    @patch("aviator.plugins.importlib.metadata.entry_points")
    def test_returns_none_when_no_entry_points(self, mock_entry_points, mock_settings):
        """Returns None when the auth_handlers group is empty."""
        mock_settings.content_system = "test_system"

        eps = MagicMock()
        eps.select.return_value = []
        mock_entry_points.return_value = eps

        result = get_auth_handler()

        assert result is None
