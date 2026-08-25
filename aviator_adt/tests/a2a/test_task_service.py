"""Tests for A2ATaskService."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from aviator.a2a.task_service import A2ATaskService


def _make_task_row(task_id="task-1", state="submitted", context_id="ctx-1"):
    return SimpleNamespace(id=task_id, state=state, context_id=context_id, artifacts=None)


@pytest.fixture
def svc():
    return A2ATaskService()


class TestA2ATaskServiceCreate:
    """Tests for A2ATaskService.create()."""

    def test_calls_repository_create(self, svc):
        row = _make_task_row()
        mock_repo = MagicMock()
        mock_repo.create.return_value = row

        with patch("aviator.a2a.task_service.database_manager") as mock_dm:
            mock_dm.session.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_dm.session.return_value.__exit__ = MagicMock(return_value=False)
            with patch("aviator.a2a.task_service.A2ATaskRepository", return_value=mock_repo):
                result = svc.create("task-1", "ctx-1", None)

        mock_repo.create.assert_called_once_with("task-1", "ctx-1")
        assert result.id == "task-1"


class TestA2ATaskServiceGet:
    """Tests for A2ATaskService.get()."""

    def test_returns_task_when_found(self, svc):
        row = _make_task_row()
        mock_repo = MagicMock()
        mock_repo.get.return_value = row

        with patch("aviator.a2a.task_service.database_manager") as mock_dm:
            mock_dm.session.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_dm.session.return_value.__exit__ = MagicMock(return_value=False)
            with patch("aviator.a2a.task_service.A2ATaskRepository", return_value=mock_repo):
                result = svc.get("task-1", None)

        assert result is row

    def test_returns_none_when_not_found(self, svc):
        mock_repo = MagicMock()
        mock_repo.get.return_value = None

        with patch("aviator.a2a.task_service.database_manager") as mock_dm:
            mock_dm.session.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_dm.session.return_value.__exit__ = MagicMock(return_value=False)
            with patch("aviator.a2a.task_service.A2ATaskRepository", return_value=mock_repo):
                result = svc.get("missing", None)

        assert result is None


class TestA2ATaskServiceUpdateState:
    """Tests for A2ATaskService.update_state()."""

    def test_calls_repository_update_state(self, svc):
        mock_repo = MagicMock()

        with patch("aviator.a2a.task_service.database_manager") as mock_dm:
            mock_dm.session.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_dm.session.return_value.__exit__ = MagicMock(return_value=False)
            with patch("aviator.a2a.task_service.A2ATaskRepository", return_value=mock_repo):
                svc.update_state("task-1", None, "completed", artifacts={"result": "done"})

        mock_repo.update_state.assert_called_once_with("task-1", "completed", artifacts={"result": "done"}, error=None)

    def test_with_error(self, svc):
        mock_repo = MagicMock()

        with patch("aviator.a2a.task_service.database_manager") as mock_dm:
            mock_dm.session.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_dm.session.return_value.__exit__ = MagicMock(return_value=False)
            with patch("aviator.a2a.task_service.A2ATaskRepository", return_value=mock_repo):
                svc.update_state("task-1", None, "failed", error="Internal error")

        mock_repo.update_state.assert_called_once_with("task-1", "failed", artifacts=None, error="Internal error")


class TestA2ATaskServiceCancel:
    """Tests for A2ATaskService.cancel()."""

    def test_calls_repository_cancel(self, svc):
        row = _make_task_row(state="canceled")
        mock_repo = MagicMock()
        mock_repo.cancel.return_value = row

        with patch("aviator.a2a.task_service.database_manager") as mock_dm:
            mock_dm.session.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_dm.session.return_value.__exit__ = MagicMock(return_value=False)
            with patch("aviator.a2a.task_service.A2ATaskRepository", return_value=mock_repo):
                result = svc.cancel("task-1", None)

        assert result.state == "canceled"

    def test_returns_none_when_task_missing(self, svc):
        mock_repo = MagicMock()
        mock_repo.cancel.return_value = None

        with patch("aviator.a2a.task_service.database_manager") as mock_dm:
            mock_dm.session.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_dm.session.return_value.__exit__ = MagicMock(return_value=False)
            with patch("aviator.a2a.task_service.A2ATaskRepository", return_value=mock_repo):
                result = svc.cancel("missing", None)

        assert result is None
