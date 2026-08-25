"""Celery configuration module.

This module contains all the configuration settings for Celery.
It should be importable on the Python path for Celery to automatically load it.

For more information, see:
https://docs.celeryq.dev/en/v5.6.0/userguide/configuration.html
"""

import os

from celery.schedules import crontab
from kombu import Exchange, Queue

from aviator.plugins import load_beat_schedules, load_plugin_celery_imports
from aviator.settings import settings

# Custom imports for Celery tasks from plugins
imports = [*load_plugin_celery_imports(), "aviator.tasks.beat_tasks"]

EMBEDDINGS_QUEUE = settings.broker_queue_name if hasattr(settings, "broker_queue_name") else "aviator-embeddings"
SUMMARY_QUEUE = (
    settings.broker_summary_queue_name if hasattr(settings, "broker_summary_queue_name") else "aviator-summaries"
)

WORKER_ROLE = settings.celery_worker_role if hasattr(settings, "celery_worker_role") else "default"
IS_SUMMARY_WORKER = WORKER_ROLE == "summary"

# RabbitMQ 4.x no longer permits transient non-exclusive queues.
# Make the Celery control (pidbox) queue exclusive so it is compatible
# with both RabbitMQ 3.x and 4.x.
control_queue_exclusive = True

# Reliability settings:
# - Ack only after task execution, so worker loss does not silently drop tasks.
# - Requeue late-acked tasks when worker is lost (crash/kill/restart during execution).
# - Keep ack on non-retryable failures so those tasks are terminally failed, not redelivered forever.
# - Prefetch one task per worker process to minimize in-flight unacked tasks on shutdown.
task_acks_late = True
task_reject_on_worker_lost = True
task_acks_on_failure_or_timeout = True
worker_prefetch_multiplier = 1

# Celery beat schedule for periodic tasks
beat_schedule = {}
if settings.usage_tracking_enabled:
    beat_schedule["cleanup-usage-transactions"] = {
        "task": "aviator.tasks.beat_tasks.cleanup_usage_transactions",
        "schedule": crontab(hour=2, minute=0),  # Daily at 02:00 UTC
    }

beat_schedule["cleanup-checkpoints"] = {
    "task": "aviator.tasks.beat_tasks.cleanup_checkpoints",
    "schedule": crontab(hour=3, minute=0),  # Daily at 03:00 UTC
    "options": {"local": True},
}

# Merge plugin-provided beat schedules
beat_schedule.update(load_beat_schedules())

if settings.broker_consistent_hash_enabled and settings.broker_type in ("amqp", None):
    task_default_queue = EMBEDDINGS_QUEUE
    task_default_exchange = "aviator"
    task_default_exchange_type = "direct"

    # Declare a durable x-consistent-hash exchange
    _hash_exchange = Exchange(
        f"{task_default_queue}-hash",
        type="x-consistent-hash",
        durable=True,
    )

    # Create N worker queues, all bound to the hash exchange with equal weight "10"
    _embedding_queues = [
        Queue(
            f"{task_default_queue}-{i}",
            exchange=_hash_exchange,
            routing_key="10",
            durable=True,
            auto_delete=False,
            exclusive=False,
        )
        for i in range(settings.broker_worker_queue_count)
    ]

    _summary_exchange = Exchange(task_default_exchange, type=task_default_exchange_type, durable=True)
    _summary_queue = Queue(
        SUMMARY_QUEUE,
        exchange=_summary_exchange,
        routing_key=SUMMARY_QUEUE,
        durable=True,
    )

    if IS_SUMMARY_WORKER:
        # Summary worker only consumes the summary queue
        task_queues = [_summary_queue]
    else:
        # If WORKER_QUEUE_INDEX is set, only consume the assigned queue
        # (but still declare all queues so bindings are created correctly)
        _worker_index = os.environ.get("WORKER_QUEUE_INDEX")
        if _worker_index is not None:
            # Extract ordinal from pod name (e.g., "aviator-worker-0" -> "0") or use as-is if already a number
            _raw = _worker_index.split("-")[-1] if "-" in _worker_index else _worker_index
            if _raw.isdigit():
                _idx = int(_raw)
                task_queues = [_embedding_queues[_idx]]
            else:
                # Non-numeric index (e.g. Docker Compose container ID) — consume all queues
                task_queues = list(_embedding_queues)
        else:
            task_queues = list(_embedding_queues)

    # When migration is enabled, add extra queues so workers also process
    # migration tasks alongside their normal workload.
    if settings.migration_enabled:
        from migration.settings import settings as migration_settings

        if IS_SUMMARY_WORKER:
            task_queues.append(
                Queue(
                    migration_settings.summary_queue,
                    _summary_exchange,
                    routing_key=migration_settings.summary_queue,
                    durable=True,
                )
            )
        else:
            _migration_exchange = Exchange(migration_settings.migration_queue, type="direct", durable=True)
            task_queues.append(
                Queue(
                    migration_settings.migration_queue,
                    exchange=_migration_exchange,
                    routing_key=migration_settings.migration_queue,
                    durable=True,
                )
            )

    # Route the embedding task through the hash exchange
    task_routes = {
        "aviator.celery.process_embedding_request": {
            "exchange": _hash_exchange,
        },
        "aviator.celery.process_workspace_summary_request": {
            "queue": SUMMARY_QUEUE,
            "exchange": _summary_exchange,
            "routing_key": SUMMARY_QUEUE,
        },
    }

    # Force serial execution per queue (critical for the serialization guarantee)
    worker_concurrency = 1

elif settings.broker_type == "pubsub":
    # Google Pub/Sub: use queue name as exchange name so that the Pub/Sub topic
    # matches the queue name. The kombu gcpubsub transport maps exchange → topic,
    # so exchange=queue ensures the producer and worker use the same topic/subscription.
    # This avoids a fallback in gcpubsub Channel._lookup that otherwise binds the
    # exchange name as a queue, creating an unintended subscription.
    task_default_queue = EMBEDDINGS_QUEUE
    task_default_exchange = EMBEDDINGS_QUEUE
    task_default_exchange_type = "direct"

    _embedding_exchange = Exchange(EMBEDDINGS_QUEUE, type="direct", durable=True)
    _summary_exchange = Exchange(SUMMARY_QUEUE, type="direct", durable=True)
    _embedding_queues = Queue(EMBEDDINGS_QUEUE, _embedding_exchange, routing_key=EMBEDDINGS_QUEUE, durable=True)
    _summary_queue = Queue(SUMMARY_QUEUE, _summary_exchange, routing_key=SUMMARY_QUEUE, durable=True)

    task_queues = [_summary_queue] if IS_SUMMARY_WORKER else [_embedding_queues]

    task_routes = {
        "aviator.celery.process_embedding_request": {
            "queue": EMBEDDINGS_QUEUE,
            "exchange": _embedding_exchange,
            "routing_key": EMBEDDINGS_QUEUE,
        },
        "aviator.celery.process_workspace_summary_request": {
            "queue": SUMMARY_QUEUE,
            "exchange": _summary_exchange,
            "routing_key": SUMMARY_QUEUE,
        },
    }

elif settings.migration_enabled:
    from migration.settings import settings as migration_settings

    task_default_queue = EMBEDDINGS_QUEUE
    task_default_exchange = "aviator"
    task_default_exchange_type = "direct"
    _migration_exchange = Exchange(migration_settings.migration_queue, type="direct", durable=True)

    _exchange = Exchange(task_default_exchange, type=task_default_exchange_type, durable=True)
    _embedding_queues = Queue(EMBEDDINGS_QUEUE, _exchange, routing_key=EMBEDDINGS_QUEUE, durable=True)
    _summary_queue = Queue(SUMMARY_QUEUE, _exchange, routing_key=SUMMARY_QUEUE, durable=True)
    _migration_queue = Queue(
        migration_settings.migration_queue,
        exchange=_migration_exchange,
        routing_key=migration_settings.migration_queue,
        durable=True,
    )
    _summary_backfill_queue = Queue(
        migration_settings.summary_queue, _exchange, routing_key=migration_settings.summary_queue, durable=True
    )
    task_queues = (
        [_summary_queue, _summary_backfill_queue] if IS_SUMMARY_WORKER else [_embedding_queues, _migration_queue]
    )

    task_routes = {
        "aviator.celery.process_embedding_request": {
            "queue": EMBEDDINGS_QUEUE,
            "exchange": _exchange,
            "routing_key": EMBEDDINGS_QUEUE,
        },
        "aviator.celery.process_workspace_summary_request": {
            "queue": SUMMARY_QUEUE,
            "exchange": _exchange,
            "routing_key": SUMMARY_QUEUE,
        },
    }
else:
    task_default_queue = EMBEDDINGS_QUEUE
    task_default_exchange = "aviator"
    task_default_exchange_type = "direct"

    _exchange = Exchange("aviator", type="direct", durable=True)
    _embedding_queues = Queue(EMBEDDINGS_QUEUE, _exchange, routing_key=EMBEDDINGS_QUEUE, durable=True)
    _summary_queue = Queue(SUMMARY_QUEUE, _exchange, routing_key=SUMMARY_QUEUE, durable=True)

    task_queues = [_summary_queue] if IS_SUMMARY_WORKER else [_embedding_queues]

    task_routes = {
        "aviator.celery.process_embedding_request": {
            "queue": EMBEDDINGS_QUEUE,
            "exchange": _exchange,
            "routing_key": EMBEDDINGS_QUEUE,
        },
        "aviator.celery.process_workspace_summary_request": {
            "queue": SUMMARY_QUEUE,
            "exchange": _exchange,
            "routing_key": SUMMARY_QUEUE,
        },
    }
