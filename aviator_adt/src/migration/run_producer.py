"""CLI entrypoint for the migration producer."""

from __future__ import annotations

import logging
import os
import sys
import time

from celery import Celery

from aviator.settings import settings as aviator_settings
from migration.producer import run_producer
from migration.settings import settings

logger = logging.getLogger(__name__)

# Seconds to wait before exiting on a transient error so Docker's
# ``restart: on-failure`` doesn't spin too aggressively.
_RETRY_BACKOFF_SECS = 30


def _make_producer_celery() -> Celery:
    """Create a lightweight Celery client for task dispatch only.

    The producer only calls ``send_task()`` — it doesn't run or register
    any tasks, so it doesn't need the full aviator Celery app (which pulls
    in heavy dependencies like OpenTelemetry and LangChain).

    Broker-specific configurations (RabbitMQ vs Pub/Sub) are applied to
    ensure the producer publishes to the same queues/topics that workers
    consume from.
    """
    app = Celery("migration-producer")
    app.conf.broker_url = settings.broker_url
    app.conf.task_serializer = "json"
    app.conf.accept_content = ["json"]
    app.conf.result_backend = None
    app.conf.task_ignore_result = True

    # Apply broker-specific transport options to match the worker configuration
    if aviator_settings.broker_type == "pubsub":
        app.conf.broker_transport_options = aviator_settings.pubsub_broker_transport_options

        # Set emulator host if specified (for local development)
        if aviator_settings.pubsub_emulator_host:
            os.environ["PUBSUB_EMULATOR_HOST"] = aviator_settings.pubsub_emulator_host
            logger.info("Set PUBSUB_EMULATOR_HOST to %s", aviator_settings.pubsub_emulator_host)

        logger.info(
            "Producer configured for Pub/Sub with queue_name_prefix='%s'",
            aviator_settings.pubsub_subscription_name_prefix,
        )

    return app


def main() -> None:
    """Configure logging and kick off the producer."""
    logging.basicConfig(
        level=getattr(logging, settings.log_level),
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        stream=sys.stdout,
    )
    try:
        celery = _make_producer_celery()
        run_producer(celery)
    except Exception:
        logger.exception("Producer crashed — container will restart in %ds.", _RETRY_BACKOFF_SECS)
        time.sleep(_RETRY_BACKOFF_SECS)
        sys.exit(1)


if __name__ == "__main__":
    main()
