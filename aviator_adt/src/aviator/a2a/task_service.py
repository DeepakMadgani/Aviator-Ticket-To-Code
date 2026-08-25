"""A2A task service — single entry point for all A2ATask DB operations.

This consolidates the repeated ``with database_manager.session(...) as db:``
pattern and ensures consistent session handling across the A2A layer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from aviator.database import database_manager
from aviator.database.a2a_task_repository import A2ATaskRepository

if TYPE_CHECKING:
    from aviator.a2a.models import TaskState
    from aviator.database.models import A2ATask


class A2ATaskService:
    """Owns all DB lifecycle operations for ``A2ATask`` rows.

    Each method opens its own session via ``database_manager.session()``.
    No session object is ever exposed to callers.
    """

    def create(self, task_id: str, context_id: str | None, schema_name: str | None) -> A2ATask:
        """Insert a new task row in ``submitted`` state and return it."""
        with database_manager.session(schema_name) as db:
            return A2ATaskRepository(db).create(task_id, context_id)

    def get(self, task_id: str, schema_name: str | None) -> A2ATask | None:
        """Return the task row for *task_id*, or ``None`` if not found."""
        with database_manager.session(schema_name) as db:
            return A2ATaskRepository(db).get(task_id)

    def update_state(
        self,
        task_id: str,
        schema_name: str | None,
        state: TaskState,
        *,
        artifacts: dict | None = None,
        error: str | None = None,
    ) -> None:
        """Transition a task to *state*, optionally persisting artifacts or an error message.

        Args:
            task_id:     The task to update.
            schema_name: Tenant schema (``None`` → default schema).
            state:       Target state — ``"working"``, ``"completed"``,
                         ``"input-required"``, ``"failed"``, or ``"canceled"``.
            artifacts:   Optional artifact payload to persist alongside the state.
            error:       Optional error message (meaningful for ``"failed"`` state).

        """
        with database_manager.session(schema_name) as db:
            A2ATaskRepository(db).update_state(task_id, state, artifacts=artifacts, error=error)

    def cancel(self, task_id: str, schema_name: str | None) -> A2ATask | None:
        """DB-level cancel: sets state to ``canceled`` only if still in progress.

        Returns the (possibly updated) task row, or ``None`` if not found.
        """
        with database_manager.session(schema_name) as db:
            return A2ATaskRepository(db).cancel(task_id)


# Module-level singleton — import this everywhere instead of the class.
task_service = A2ATaskService()
