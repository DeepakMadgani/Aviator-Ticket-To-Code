"""Tests for beat task wrappers."""

from unittest.mock import AsyncMock, patch

import pytest

from aviator.tasks import beat_tasks

pytestmark = pytest.mark.anyio


def test_cleanup_usage_transactions_task_wraps_service_calls() -> None:
    """Task should return the merged cleanup summary from usage-tracking services."""
    with (
        patch("aviator.services.usage_tracking.cleanup.cleanup_old_transactions", return_value=11),
        patch("aviator.services.usage_tracking.cleanup.cleanup_old_tallies", return_value=4),
    ):
        result = beat_tasks.cleanup_usage_transactions.run()

    assert result == {"deleted_transactions": 11, "deleted_tallies": 4}


async def test_cleanup_checkpoints_task_wraps_service_call() -> None:
    """Task should await cleanup service and return the deleted rows count."""
    with patch(
        "aviator.services.checkpoint_cleanup.cleanup_old_checkpoints",
        new=AsyncMock(return_value=9),
    ) as mock_cleanup:
        result = await beat_tasks.cleanup_checkpoints.run()

    assert result == {"deleted_checkpoints": 9}
    mock_cleanup.assert_awaited_once()
