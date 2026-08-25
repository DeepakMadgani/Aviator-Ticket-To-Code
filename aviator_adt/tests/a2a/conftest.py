"""Test configuration for a2a tests.

Ensures the full app is initialized before any individual module import
to avoid the circular import between aviator.a2a.router and aviator.api.
"""

import os

os.environ.setdefault("TESTING", "true")
os.environ.setdefault("VECTOR_STORE", "memory")
os.environ.setdefault("CHECKPOINTER", "memory")
os.environ.setdefault("USAGE_TRACKING_ENABLED", "false")

# Initialize the full app early so all cross-module imports are resolved
# in the correct order — prevents circular import between aviator.a2a.router
# and aviator.api.__init__ at import time.
import pytest

import aviator.main  # noqa: F401
from aviator.utils.limiter import limiter


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Clear the SlowAPI rate-limiter state before every test."""
    limiter.reset()
