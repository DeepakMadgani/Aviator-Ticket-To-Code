"""Tests for TasksHandler (tasks/get and tasks/cancel)."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from aviator.a2a.handler.tasks_handler import TasksHandler
from aviator.a2a.models import Artifact, Task, TaskState
from aviator.exceptions import TaskNotCancelableError, TaskNotFoundError


def _make_row(task_id="task-1", state="submitted", context_id="ctx-1", artifacts=None):
    return SimpleNamespace(id=task_id, state=state, context_id=context_id, artifacts=artifacts)


@pytest.fixture
def handler():
    return TasksHandler()


# ── _extract_task_id ──────────────────────────────────────────────────────────


class TestExtractTaskId:
    """Tests for TasksHandler._extract_task_id()."""

    def test_extracts_from_id_field(self):
        result = TasksHandler._extract_task_id({"id": "task-1"})
        assert result == "task-1"

    def test_extracts_from_nested_params(self):
        result = TasksHandler._extract_task_id({"params": {"id": "task-2"}})
        assert result == "task-2"

    def test_missing_id_raises_value_error(self):
        with pytest.raises(ValueError, match="Missing required field"):
            TasksHandler._extract_task_id({})

    def test_id_converted_to_string(self):
        result = TasksHandler._extract_task_id({"id": 42})
        assert result == "42"


# ── _row_to_task ──────────────────────────────────────────────────────────────


class TestRowToTask:
    """Tests for TasksHandler._row_to_task()."""

    def test_basic_row_maps_to_task(self):
        row = _make_row()
        task = TasksHandler._row_to_task(row)
        assert isinstance(task, Task)
        assert task.id == "task-1"
        assert task.contextId == "ctx-1"
        assert task.status.state == TaskState.SUBMITTED
        assert task.artifacts == []

    def test_row_with_result_creates_artifact(self):
        row = _make_row(state="completed", artifacts={"result": "The answer is 42."})
        task = TasksHandler._row_to_task(row)
        assert len(task.artifacts) == 1
        assert isinstance(task.artifacts[0], Artifact)
        artifact = task.artifacts[0]
        assert artifact.name == "response"
        assert artifact.parts[0].text == "The answer is 42."

    def test_row_with_no_artifacts_field(self):
        row = _make_row(artifacts=None)
        task = TasksHandler._row_to_task(row)
        assert task.artifacts == []

    def test_row_with_empty_result_no_artifact(self):
        row = _make_row(artifacts={"result": ""})
        task = TasksHandler._row_to_task(row)
        assert task.artifacts == []


# ── handle_get ────────────────────────────────────────────────────────────────


class TestHandleGet:
    """Tests for TasksHandler.handle_get()."""

    @pytest.mark.asyncio
    async def test_returns_task_when_found(self, handler):
        row = _make_row(state="completed")

        with patch("aviator.a2a.handler.tasks_handler.task_service") as mock_ts:
            mock_ts.get.return_value = row
            task = await handler.handle_get({"id": "task-1"}, MagicMock(), {"tenantId": "t1"})

        assert task.id == "task-1"
        assert task.status.state == TaskState.COMPLETED

    @pytest.mark.asyncio
    async def test_raises_task_not_found(self, handler):
        with patch("aviator.a2a.handler.tasks_handler.task_service") as mock_ts:
            mock_ts.get.return_value = None
            with pytest.raises(TaskNotFoundError, match="task-missing"):
                await handler.handle_get({"id": "task-missing"}, MagicMock(), {"tenantId": "t1"})

    @pytest.mark.asyncio
    async def test_missing_id_raises_value_error(self, handler):
        with pytest.raises(ValueError, match="Missing required field"):
            await handler.handle_get({}, MagicMock(), {"tenantId": "t1"})


# ── handle_cancel ─────────────────────────────────────────────────────────────


class TestHandleCancel:
    """Tests for TasksHandler.handle_cancel()."""

    @pytest.mark.asyncio
    async def test_cancels_running_task(self, handler):
        row = _make_row(state="working")
        canceled_row = _make_row(state="canceled")

        with (
            patch("aviator.a2a.handler.tasks_handler.task_service") as mock_ts,
            patch("aviator.a2a.handler.tasks_handler.cancel_task", return_value=True),
        ):
            mock_ts.get.return_value = row
            mock_ts.cancel.return_value = canceled_row
            task = await handler.handle_cancel({"id": "task-1"}, MagicMock(), {"tenantId": "t1"})

        assert task.status.state == TaskState.CANCELED

    @pytest.mark.asyncio
    async def test_cancels_submitted_task(self, handler):
        row = _make_row(state="submitted")
        canceled_row = _make_row(state="canceled")

        with (
            patch("aviator.a2a.handler.tasks_handler.task_service") as mock_ts,
            patch("aviator.a2a.handler.tasks_handler.cancel_task", return_value=False),
        ):
            mock_ts.get.return_value = row
            mock_ts.cancel.return_value = canceled_row
            task = await handler.handle_cancel({"id": "task-1"}, MagicMock(), {"tenantId": "t1"})

        assert task.status.state == TaskState.CANCELED

    @pytest.mark.asyncio
    async def test_raises_not_found_when_missing(self, handler):
        with patch("aviator.a2a.handler.tasks_handler.task_service") as mock_ts:
            mock_ts.get.return_value = None
            with pytest.raises(TaskNotFoundError):
                await handler.handle_cancel({"id": "missing"}, MagicMock(), {"tenantId": "t1"})

    @pytest.mark.asyncio
    async def test_raises_not_cancelable_for_completed_task(self, handler):
        row = _make_row(state="completed")

        with patch("aviator.a2a.handler.tasks_handler.task_service") as mock_ts:
            mock_ts.get.return_value = row
            with pytest.raises(TaskNotCancelableError):
                await handler.handle_cancel({"id": "task-done"}, MagicMock(), {"tenantId": "t1"})

    @pytest.mark.asyncio
    async def test_raises_not_cancelable_for_failed_task(self, handler):
        row = _make_row(state="failed")

        with patch("aviator.a2a.handler.tasks_handler.task_service") as mock_ts:
            mock_ts.get.return_value = row
            with pytest.raises(TaskNotCancelableError):
                await handler.handle_cancel({"id": "task-done"}, MagicMock(), {"tenantId": "t1"})

    @pytest.mark.asyncio
    async def test_falls_back_to_original_row_when_cancel_returns_none(self, handler):
        row = _make_row(state="working")

        with (
            patch("aviator.a2a.handler.tasks_handler.task_service") as mock_ts,
            patch("aviator.a2a.handler.tasks_handler.cancel_task", return_value=False),
        ):
            mock_ts.get.return_value = row
            mock_ts.cancel.return_value = None  # DB cancel returned None
            task = await handler.handle_cancel({"id": "task-1"}, MagicMock(), {"tenantId": "t1"})

        # Falls back to the original row's state
        assert task.id == "task-1"
