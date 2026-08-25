"""Celery queue status reporting service."""

import logging

from celery.app.control import Inspect

from aviator.celery import celery
from aviator.models import QueueStatusModel, WorkerAutoscalerModel, WorkerInfoModel
from aviator.settings import settings

logger = logging.getLogger(__name__)


def _get_queue_depths() -> dict[str, int]:
    """Get message counts for each queue from the broker efficiently.

    Uses transport-specific queue declarations to get queue depths without
    consuming messages.
    Returns a dict of queue_name -> message_count.

    """
    queue_depths: dict[str, int] = {}

    # Supported transports for queue depth probing.
    if settings.broker_type not in ("amqp", "rabbitmq", "redis", "pubsub"):
        return queue_depths

    try:
        from kombu import Connection

        with Connection(celery.conf.broker_url) as conn, conn.channel() as channel:
            for queue_name in [settings.broker_queue_name]:
                try:
                    declare_kwargs = {"queue": queue_name}

                    # For AMQP/RabbitMQ, passive declaration avoids creating queues.
                    if settings.broker_type in ("amqp", "rabbitmq"):
                        declare_kwargs["passive"] = True

                    _name, message_count, _consumers = channel.queue_declare(**declare_kwargs)
                    queue_depths[queue_name] = message_count
                    logger.debug("Queue %s: %d messages", queue_name, message_count)
                except Exception as e:
                    logger.warning("Failed to query queue %s: %s", queue_name, e)
    except Exception as e:
        logger.warning("Failed to get queue depths from broker: %s", e)

    return queue_depths


def get_celery_queue_status() -> QueueStatusModel:
    """Get the current status of Celery workers and queues.

    Returns a QueueStatusModel containing:
    - workers: dict of worker names to their status (online/offline)
    - queues: dict of queue names to message counts
    - registered_tasks: list of registered task names
    - broker_connection: status of broker connection

    """
    try:
        # Get broker connection status
        conn = celery.connection_for_read()
        conn.ensure_connection(timeout=2)
        broker_status = True
    except Exception as e:
        logger.warning("Failed to check broker connection: %s", e)
        broker_status = False

    status_data = QueueStatusModel(
        broker_type=settings.broker_type,
        broker_host=settings.broker_url.host,
        broker_connection="connected" if broker_status else "disconnected",
        registered_tasks=[task for task in celery.tasks if not task.startswith("celery.")],
    )

    if not broker_status:
        status_data.error = "Cannot connect to broker - worker information unavailable"
        return status_data

    # Get queue depths from broker
    try:
        status_data.queues = _get_queue_depths()
    except Exception as e:
        logger.debug("Failed to get queue depths: %s", e)

    # Remote control is disabled for pub/sub brokers
    if settings.broker_type == "pubsub":
        status_data.warning = "Remote control is disabled for Pub/Sub brokers - worker information unavailable"
        return status_data

    # Try to get worker and task information
    try:
        # Attempt to use remote control if available with a timeout
        inspector = Inspect(app=celery, timeout=2)

        # Get stats to determine worker status
        try:
            stats = inspector.stats() or {}
        except Exception as e:
            logger.warning("Failed to get worker stats: %s", e)
            stats = {}

        # Get active tasks per worker
        try:
            active = inspector.active() or {}
        except Exception as e:
            logger.warning("Failed to get active tasks: %s", e)
            active = {}

        for worker_name, worker_stats in stats.items():
            autoscaler_stats = worker_stats.get("autoscaler") or {}
            status_data.workers[worker_name] = WorkerInfoModel(
                status="online",
                uptime_seconds=worker_stats.get("uptime"),
                autoscaler=(
                    WorkerAutoscalerModel(
                        current=autoscaler_stats.get("current"),
                        min=autoscaler_stats.get("min"),
                        max=autoscaler_stats.get("max"),
                    )
                    if autoscaler_stats
                    else None
                ),
                active_tasks=len(active.get(worker_name, [])),
                tasks_executed=worker_stats.get("total") or None,
            )

        # If no workers responded, set a warning
        if not status_data.workers:
            status_data.warning = "No workers responded to control commands (workers may not be running)"

    except Exception as e:
        logger.warning("Failed to initialize Celery inspector: %s", e)
        status_data.error = f"Failed to get worker information: {e!s}"

    return status_data
