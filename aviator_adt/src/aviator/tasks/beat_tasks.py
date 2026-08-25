"""Periodic Celery beat task entry points.

These tasks are intentionally thin wrappers that delegate retention work to
service-layer cleanup modules.
"""

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True)
def cleanup_usage_transactions(self) -> dict:  # noqa: ANN001, ARG001
    """Delete usage transactions and daily tallies older than configured retention."""
    from aviator.services.usage_tracking.cleanup import cleanup_old_tallies, cleanup_old_transactions

    deleted = cleanup_old_transactions()
    logger.info("Usage tracking cleanup: deleted %d old transaction rows", deleted)
    deleted_tallies = cleanup_old_tallies()
    logger.info("Usage tracking cleanup: deleted %d old tally rows", deleted_tallies)
    return {"deleted_transactions": deleted, "deleted_tallies": deleted_tallies}


@shared_task(bind=True)
async def cleanup_checkpoints(self) -> dict:  # noqa: ANN001, ARG001
    """Delete LangGraph checkpoint rows older than configured retention."""
    from aviator.services.checkpoint_cleanup import cleanup_old_checkpoints

    deleted = await cleanup_old_checkpoints()
    logger.info("Checkpointer cleanup: deleted %d rows across checkpoint tables", deleted)
    return {"deleted_checkpoints": deleted}
