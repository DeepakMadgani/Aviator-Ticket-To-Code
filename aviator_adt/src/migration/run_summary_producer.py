"""CLI entrypoint for the summary backfill producer."""

from __future__ import annotations

import logging
import os
import sys
import time

from celery import Celery

from aviator.settings import settings as aviator_settings
from migration.settings import settings
from migration.summary_producer import run_summary_producer

logger = logging.getLogger(__name__)

_RETRY_BACKOFF_SECS = 30


def _make_producer_celery() -> Celery:
    """Create a lightweight Celery client for task dispatch only.

    The producer only calls ``send_task()`` — it doesn't run or register
    any tasks, so it doesn't need the full aviator Celery app.

    Broker-specific configurations (RabbitMQ vs Pub/Sub) are applied to
    ensure the producer publishes to the same queues/topics that workers
    consume from.
    """
    app = Celery("summary-backfill-producer")
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
            "Summary producer configured for Pub/Sub with queue_name_prefix='%s'",
            aviator_settings.pubsub_subscription_name_prefix,
        )

    return app


def main() -> None:
    """Configure logging and kick off the summary backfill producer."""
    logging.basicConfig(
        level=getattr(logging, settings.log_level),
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        stream=sys.stdout,
    )
    try:
        celery = _make_producer_celery()
        run_summary_producer(celery)
    except Exception:
        logger.exception("Summary backfill producer crashed — container will restart in %ds.", _RETRY_BACKOFF_SECS)
        time.sleep(_RETRY_BACKOFF_SECS)
        sys.exit(1)


if __name__ == "__main__":
    main()
