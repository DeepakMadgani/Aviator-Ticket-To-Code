"""Prometheus metrics for usage tracking.

Counters are incremented on every recorded transaction. Gauges are updated
when the ``/v1/usage-stats`` endpoint is queried, providing the latest
semantic-size snapshot to the Prometheus scrape.
"""

from prometheus_client import Counter, Gauge

usage_transactions_total = Counter(
    "aviator_usage_transactions_total",
    "Total number of recorded usage transactions",
    labelnames=("tenant_id", "transaction_type"),
)

semantic_documents_total = Gauge(
    "aviator_semantic_documents_total",
    "Total documents currently embedded in the vector store",
    labelnames=("tenant_id",),
)

semantic_chunks_total = Gauge(
    "aviator_semantic_chunks_total",
    "Total chunks currently embedded in the vector store",
    labelnames=("tenant_id",),
)
