"""Test Google Pub/Sub integration for Celery."""

import os
from unittest.mock import patch

from aviator.settings import Settings


@patch.dict("os.environ", clear=False)
def test_pubsub_broker_url_construction():
    """Test that Pub/Sub broker URL is constructed correctly."""
    # Remove BROKER_URL from environment to allow the test to set its own broker config
    os.environ.pop("BROKER_URL", None)

    settings = Settings(broker_type="pubsub", pubsub_project_id="test-project", broker_url=None)

    expected_url = "gcpubsub://projects/test-project"
    assert str(settings.broker_url) == expected_url


def test_pubsub_emulator_host_environment():
    """Test that emulator host setting is stored in configuration."""
    settings = Settings(broker_type="pubsub", pubsub_project_id="test-project", pubsub_emulator_host="localhost:8432")

    # The emulator host is stored in settings but not automatically set in environment
    # (the functionality is commented out in the current implementation)
    assert settings.pubsub_emulator_host == "localhost:8432"


def test_pubsub_defaults():
    """Test default values for Pub/Sub settings."""
    # Test with environment isolation to get true defaults,
    # but keep essential test environment variables and minimum required vars
    base_env = {
        "TESTING": "true",
        "VECTOR_STORE": "memory",
        "CHECKPOINTER": "memory",
        "USAGE_TRACKING_ENABLED": "false",
        "DEV_TOOLS": "true",
        "POSTGRES_PASSWORD": "test-password",  # Minimal required env var for Settings validation
    }
    with patch.dict("os.environ", base_env, clear=True):
        settings = Settings(broker_type="pubsub")

        assert "aviator-project" in str(settings.broker_url)
        assert str(settings.broker_url) == "gcpubsub://projects/aviator-project"
        assert settings.broker_queue_name == "aviator-embeddings"


def test_pubsub_task_serialization_defaults():
    """Test that appropriate serialization is configured for Pub/Sub."""
    # This test would verify that JSON serialization is used
    # when Pub/Sub is the broker type
    assert True  # Placeholder for future implementation
