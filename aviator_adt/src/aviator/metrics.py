"""Metrics for Content Aviator logs."""

import logging
from collections.abc import Callable

from prometheus_client import Gauge
from prometheus_fastapi_instrumentator.metrics import Info

logger = logging.getLogger(__name__)


class SocketCounterMetric:
    """Metric for open websockets by endpoint."""

    _registry: dict
    _metric: Gauge

    def __init__(self) -> None:
        """Initialize the SocketCounterMetric."""

        self._registry = {}
        self._metric = Gauge(
            "open_sockets",
            "Number of open sockets by endpoint",
            labelnames=("endpoint",),
        )

    def inc(self, endpoint: str) -> None:
        """Increment the socket counter for the given endpoint."""

        if endpoint not in self._registry:
            self._registry[endpoint] = 0

        self._registry[endpoint] += 1

        self._metric.labels(endpoint).set(
            self._registry[endpoint],
        )
        logger.debug("Incremented socket counter for endpoint %s: %s", endpoint, self._registry[endpoint])

    def dec(self, endpoint: str) -> None:
        """Decrement the socket counter for the given endpoint."""

        if endpoint not in self._registry:
            self._registry[endpoint] = 0

        self._registry[endpoint] -= 1

        self._metric.labels(endpoint).set(
            self._registry[endpoint],
        )
        logger.debug("Decremented socket counter for endpoint %s: %s", endpoint, self._registry[endpoint])

    # By Payload
    def metrics(self) -> Callable[[Info], None]:
        """Metric for open sockets by endpoint."""

        def instrumentation(info: Info) -> None:  # noqa: ARG001
            for endpoint, counter in self._registry.items():
                self._metric.labels(endpoint).set(
                    counter,
                )

        return instrumentation


socket_counter = SocketCounterMetric()
