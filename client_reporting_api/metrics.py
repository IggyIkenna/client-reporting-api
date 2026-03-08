"""Prometheus metrics for client-reporting-api."""

from prometheus_client import Counter, Histogram

RECORDS_PROCESSED: Counter = Counter(
    "client_reporting_api_records_processed_total",
    "Total number of client report records processed",
    ["status"],  # labels: success / error
)

PROCESSING_LATENCY: Histogram = Histogram(
    "client_reporting_api_processing_latency_seconds",
    "Client reporting API request latency in seconds",
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5],
)
