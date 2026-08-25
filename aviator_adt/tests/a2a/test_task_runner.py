"""Tests for A2A task runner (start_task / cancel_task)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import aviator.a2a.task_runner as task_runner_module
from aviator.a2a.task_runner import cancel_task, start_task

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_handler(response=None, raise_exc=None):
    """Return a mock handler whose process() returns *response* or raises *raise_exc*."""
    handler = MagicMock()
    if raise_exc:
        handler.process = AsyncMock(side_effect=raise_exc)
    else:
        handler.process = AsyncMock(return_value=response)
    return handler


def _make_response(result="Answer", context='{"thread_id": "t1"}', interrupt=False):
    """Return a fake A2AAgentInvokeResponse-like object."""
    import json

    ctx = json.dumps({"thread_id": "t1", "interrupt": interrupt})
    resp = MagicMock()
    resp.result = result
    resp.context = ctx
    resp.references = []
    resp.where = []
    return resp


# ── start_task ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_task_registers_running_task():
    task_runner_module._running.clear()
    handler = _make_handler(response=_make_response())

    with (
        patch.object(task_runner_module.task_service, "create"),
        patch.object(task_runner_module.task_service, "update_state"),
    ):
        t = start_task("task-1", handler, {}, MagicMock(), {}, None)
        assert "task-1" in task_runner_module._running
        await t  # allow background task to complete
        # After completion, task is removed from _running
        assert "task-1" not in task_runner_module._running


@pytest.mark.asyncio
async def test_start_task_marks_completed_on_success():
    task_runner_module._running.clear()
    handler = _make_handler(response=_make_response())
    states = []

    def capture_state(task_id, schema, state, **_kwargs):
        states.append(state)

    with patch.object(task_runner_module.task_service, "update_state", side_effect=capture_state):
        t = start_task("task-2", handler, {}, MagicMock(), {}, "schema_test")
        await t

    assert "working" in states
    assert "completed" in states


@pytest.mark.asyncio
async def test_start_task_marks_input_required_on_interrupt():
    task_runner_module._running.clear()
    handler = _make_handler(response=_make_response(interrupt=True))
    states = []

    def capture_state(task_id, schema, state, **_kwargs):
        states.append(state)

    with patch.object(task_runner_module.task_service, "update_state", side_effect=capture_state):
        t = start_task("task-3", handler, {}, MagicMock(), {}, None)
        await t

    assert "input-required" in states


@pytest.mark.asyncio
async def test_start_task_marks_failed_on_exception():
    task_runner_module._running.clear()
    handler = _make_handler(raise_exc=RuntimeError("boom"))
    states = []

    def capture_state(task_id, schema, state, **_kwargs):
        states.append(state)

    with patch.object(task_runner_module.task_service, "update_state", side_effect=capture_state):
        t = start_task("task-4", handler, {}, MagicMock(), {}, None)
        await t

    assert "failed" in states


@pytest.mark.asyncio
async def test_start_task_marks_canceled_on_asyncio_cancel():
    """When the asyncio task is cancelled, state should be set to 'canceled'."""
    task_runner_module._running.clear()
    barrier = asyncio.Event()

    async def blocking_process(*_args, **_kwargs):
        barrier.set()
        await asyncio.sleep(60)  # block until cancelled

    handler = MagicMock()
    handler.process = blocking_process

    states = []

    def capture_state(task_id, schema, state, **_kwargs):
        states.append(state)

    with patch.object(task_runner_module.task_service, "update_state", side_effect=capture_state):
        t = start_task("task-5", handler, {}, MagicMock(), {}, None)
        await barrier.wait()
        t.cancel()
        # _run catches CancelledError and updates state — the asyncio.Task itself
        # completes cleanly without re-raising.
        await asyncio.gather(t, return_exceptions=True)

    assert "canceled" in states


# ── cancel_task ───────────────────────────────────────────────────────────────


def test_cancel_task_returns_true_for_running_task():
    task_runner_module._running.clear()
    mock_asyncio_task = MagicMock(spec=asyncio.Task)
    mock_asyncio_task.done.return_value = False
    task_runner_module._running["running-task-1"] = mock_asyncio_task

    result = cancel_task("running-task-1")

    assert result is True
    mock_asyncio_task.cancel.assert_called_once()


def test_cancel_task_returns_false_for_unknown_task():
    task_runner_module._running.clear()
    result = cancel_task("non-existent-task")
    assert result is False


def test_cancel_task_returns_false_for_done_task():
    task_runner_module._running.clear()
    mock_asyncio_task = MagicMock(spec=asyncio.Task)
    mock_asyncio_task.done.return_value = True
    task_runner_module._running["done-task"] = mock_asyncio_task

    result = cancel_task("done-task")

    assert result is False
    mock_asyncio_task.cancel.assert_not_called()
