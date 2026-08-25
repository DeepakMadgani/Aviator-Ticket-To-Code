import os

import pytest
from fastapi.testclient import TestClient

# Set testing flag to skip database initialization (similar to VECTOR_STORE=memory)
os.environ["TESTING"] = "true"
os.environ["VECTOR_STORE"] = "memory"
os.environ["CHECKPOINTER"] = "memory"
os.environ["USAGE_TRACKING_ENABLED"] = "false"
os.environ["DEV_TOOLS"] = "true"


@pytest.fixture
def client():
    headers = {"auth-ticket": "some-valid-ticket"}

    from aviator.main import app

    with TestClient(app) as client:
        client.headers.update(headers)
        yield client
