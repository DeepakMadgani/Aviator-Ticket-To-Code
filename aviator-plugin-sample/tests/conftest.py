"""Pytest configuration and shared fixtures."""

import os
from unittest.mock import Mock, patch

import pytest


# Mock Google credentials before any imports
@pytest.fixture(scope="session", autouse=True)
def mock_google_credentials():
    """Mock Google Application Default Credentials for all tests."""
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "mock_credentials.json"

    with patch("google.auth.default") as mock_auth:
        mock_credentials = Mock()
        mock_credentials.token = "mock_token"
        mock_auth.return_value = (mock_credentials, "mock_project")
        yield


# Mock LLM Registry to avoid actual LLM calls
@pytest.fixture(scope="session", autouse=True)
def mock_llm_registry():
    """Mock LLMRegistry to avoid actual LLM initialization."""
    try:
        with patch("aviator.services.llm.LLMRegistry.get_model") as mock_get_model:
            mock_llm = Mock()
            mock_llm.invoke = Mock(return_value="mock response")
            mock_get_model.return_value = mock_llm
            yield
    except ImportError:
        # If aviator package not available, skip mocking
        yield


@pytest.fixture
def mock_state():
    """Create a mock state object for testing."""
    state = Mock()
    state.messages = []
    state.query = "test query"
    return state


@pytest.fixture
def sample_weather_data():
    """Sample weather API response."""
    return {
        "location": {"name": "Toronto"},
        "current": {
            "temp_c": 15.0,
            "condition": {"text": "Sunny"},
            "wind_kph": 10.0,
            "humidity": 65,
        },
    }


@pytest.fixture
def sample_holidays_data():
    """Sample holidays API response."""
    return {
        "holidays": [
            {
                "name": "New Year's Day",
                "date": "2026-01-01",
                "observed": "2026-01-01",
                "public": True,
            },
            {
                "name": "Canada Day",
                "date": "2026-07-01",
                "observed": "2026-07-01",
                "public": True,
            },
        ]
    }


@pytest.fixture
async def async_client():
    """Create an async HTTP client for testing."""
    import httpx

    async with httpx.AsyncClient() as client:
        yield client
