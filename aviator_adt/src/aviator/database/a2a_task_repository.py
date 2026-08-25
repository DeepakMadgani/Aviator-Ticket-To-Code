"""A2A task repository — CRUD operations for the ``a2a_tasks`` table.

Provides lifecycle operations for A2A tasks as a class that receives an
already-scoped ``Session`` from ``DatabaseManager.session()``.

No session lifecycle here — open/close is the caller's responsibility
(typically handled by the ``database_manager.session()`` context manager).

Usage::

    with database_manager.session(schema_name) as db:
        repo = A2ATaskRepository(db)
        task = repo.create(task_id, context_id)
"""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from aviator.a2a.models import TaskState

from .models import A2ATask

logger = logging.getLogger(__name__)


class A2ATaskRepository:
    """Repository for ``A2ATask`` rows.

    Receives a ``Session`` scoped to the correct tenant schema from
    ``DatabaseManager.session(schema_name)``.  All methods operate on that
    session — no session management logic inside.
    """

    def __init__(self, db: Session) -> None:
        """Initialise with a scoped database session."""
        self._db = db

    def create(self, task_id: str, context_id: str | None) -> A2ATask:
        """Insert a new task row in ``submitted`` state and return it."""
        task = A2ATask(id=task_id, context_id=context_id, state=TaskState.SUBMITTED)
        self._db.add(task)
        self._db.commit()
        self._db.refresh(task)
        logger.debug("[A2ATaskRepository] Created task %s", task_id)
        return task

    def get(self, task_id: str) -> A2ATask | None:
        """Return the task row for *task_id*, or ``None`` if not found."""
        return self._db.scalars(select(A2ATask).where(A2ATask.id == task_id)).first()

    def update_state(
        self,
        task_id: str,
        state: TaskState,
        *,
        artifacts: dict | None = None,
        error: str | None = None,
    ) -> A2ATask | None:
        """Update *state* and optionally *artifacts* / *error* for a task.

        Returns the updated row, or ``None`` if the task was not found.
        """
        task = self._db.scalars(select(A2ATask).where(A2ATask.id == task_id)).first()
        if task is None:
            logger.warning("[A2ATaskRepository] Task %s not found for state update", task_id)
            return None
        task.state = state
        if artifacts is not None:
            task.artifacts = artifacts
        if error is not None:
            task.error = error
        self._db.commit()
        self._db.refresh(task)
        logger.debug("[A2ATaskRepository] Task %s → state=%s", task_id, state)
        return task

    def cancel(self, task_id: str) -> A2ATask | None:
        """Set state to ``canceled`` only if the task is still in progress.

        Returns the (possibly updated) task row, or ``None`` if not found.
        """
        task = self._db.scalars(select(A2ATask).where(A2ATask.id == task_id)).first()
        if task is None:
            return None
        if task.state in {TaskState.SUBMITTED, TaskState.WORKING}:
            task.state = TaskState.CANCELED
            self._db.commit()
            self._db.refresh(task)
            logger.debug("[A2ATaskRepository] Task %s canceled", task_id)
        return task
