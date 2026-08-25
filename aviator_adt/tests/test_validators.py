"""Test Google Pub/Sub URL validator."""

import pytest
from pydantic import ValidationError
from pydantic_core import PydanticCustomError

from aviator.validators import PubSubUrl


def test_valid_pubsub_urls():
    """Test valid Google Pub/Sub URLs."""
    valid_urls = [
        "gcpubsub://projects/my-project",
        "gcpubsub://projects/aviator-dev",
        "gcpubsub://projects/test-project-123",
        "gcpubsub://projects/my-project/my-topic",
        "gcpubsub://projects/aviator-dev/embeddings-queue",
        "gcpubsub://projects/project-name/topic.with.dots",
        "gcpubsub://projects/test-project-123/topic_with_underscores",
    ]

    for url in valid_urls:
        result = PubSubUrl(url)
        assert str(result) == url


def test_invalid_scheme():
    """Test that non-gcpubsub schemes are rejected."""
    with pytest.raises(ValidationError):
        PubSubUrl("http://project/topic")

    with pytest.raises(ValidationError):
        PubSubUrl("amqp://project/topic")


def test_legacy_format_rejected():
    """Test that legacy format URLs are rejected."""
    legacy_urls = [
        "gcpubsub://my-project/my-topic",
        "gcpubsub://aviator-dev/embeddings-queue",
        "gcpubsub://test-project-123/topic_with_underscores",
    ]

    for url in legacy_urls:
        with pytest.raises(PydanticCustomError, match="PubSub URL must start with 'gcpubsub://projects/'"):
            PubSubUrl(url)


def test_missing_project_id():
    """Test that URLs without project ID are rejected."""
    with pytest.raises(PydanticCustomError, match="PubSub URL must include a project ID after 'projects/'"):
        PubSubUrl("gcpubsub://projects/")

    with pytest.raises(ValidationError):
        PubSubUrl("gcpubsub:///topic")


def test_missing_topic():
    """Test that URLs work even without explicit topic (basic URL validation only)."""
    # Note: The validator doesn't enforce topic requirement
    # This is acceptable since the actual topic validation can be done at application level
    url = PubSubUrl("gcpubsub://projects/my-project")
    assert url.project_id == "my-project"
    assert url.topic_name == ""


def test_project_id_and_topic_properties():
    """Test that project_id and topic_name properties work correctly."""
    url = PubSubUrl("gcpubsub://projects/my-project")
    assert url.project_id == "my-project"
    assert url.topic_name == ""

    url2 = PubSubUrl("gcpubsub://projects/test-123/topic_with_underscores")
    assert url2.project_id == "test-123"
    assert url2.topic_name == "topic_with_underscores"

    url3 = PubSubUrl("gcpubsub://projects/aviator-dev/embeddings-queue")
    assert url3.project_id == "aviator-dev"
    assert url3.topic_name == "embeddings-queue"


def test_basic_format_validation():
    """Test basic URL format validation."""
    # Valid URL should work
    url = PubSubUrl("gcpubsub://projects/valid-project/valid-topic")
    assert str(url) == "gcpubsub://projects/valid-project/valid-topic"

    # Invalid scheme should fail
    with pytest.raises(ValidationError):
        PubSubUrl("invalid://project/topic")
