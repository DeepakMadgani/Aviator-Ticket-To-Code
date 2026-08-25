"""Tests for the A2A MethodRegistry."""

from unittest.mock import AsyncMock

import pytest

from aviator.a2a.handler.registry import MethodRegistry


@pytest.fixture
def registry():
    return MethodRegistry()


class TestMethodRegistry:
    """Tests for the MethodRegistry class."""

    def test_register_and_get(self, registry):
        handler = AsyncMock()
        registry.register("message/send", handler)
        assert registry.get("message/send") is handler

    def test_get_returns_none_for_unknown_method(self, registry):
        assert registry.get("unknown/method") is None

    def test_supported_methods_empty(self, registry):
        assert registry.supported_methods() == []

    def test_supported_methods_lists_registered(self, registry):
        registry.register("message/send", AsyncMock())
        registry.register("tasks/get", AsyncMock())
        methods = registry.supported_methods()
        assert "message/send" in methods
        assert "tasks/get" in methods

    def test_overwrite_registration(self, registry):
        h1 = AsyncMock()
        h2 = AsyncMock()
        registry.register("message/send", h1)
        registry.register("message/send", h2)
        assert registry.get("message/send") is h2

    def test_multiple_methods_independent(self, registry):
        h_send = AsyncMock()
        h_get = AsyncMock()
        registry.register("message/send", h_send)
        registry.register("tasks/get", h_get)
        assert registry.get("message/send") is h_send
        assert registry.get("tasks/get") is h_get


class TestDefaultMethodRegistry:
    """Verify the module-level singleton has the expected defaults."""

    def test_default_registry_has_message_send(self):
        from aviator.a2a.handler.registry import method_registry

        assert method_registry.get("message/send") is not None

    def test_default_registry_has_message_stream(self):
        from aviator.a2a.handler.registry import method_registry

        assert method_registry.get("message/stream") is not None

    def test_default_registry_has_tasks_get(self):
        from aviator.a2a.handler.registry import method_registry

        assert method_registry.get("tasks/get") is not None

    def test_default_registry_has_tasks_cancel(self):
        from aviator.a2a.handler.registry import method_registry

        assert method_registry.get("tasks/cancel") is not None
