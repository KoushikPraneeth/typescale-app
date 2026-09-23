"""Prometheus metrics for TypeScale's HTTP and multiplayer paths."""

from prometheus_client import Counter, Gauge, Histogram

HTTP_REQUESTS = Counter(
    "typescale_http_requests_total",
    "HTTP requests handled by TypeScale.",
    ("method", "route", "status"),
)
HTTP_REQUEST_DURATION = Histogram(
    "typescale_http_request_duration_seconds",
    "HTTP request latency in seconds.",
    ("method", "route"),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)
WEBSOCKET_CONNECTIONS = Gauge(
    "typescale_websocket_connections",
    "Currently accepted WebSocket connections on this process.",
)
WEBSOCKET_MESSAGES = Counter(
    "typescale_websocket_messages_total",
    "WebSocket messages received by type.",
    ("type",),
)
RACES_STARTED = Counter(
    "typescale_races_started_total",
    "Races that transitioned into the running state.",
)
RACES_FINISHED = Counter(
    "typescale_races_finished_total",
    "Races atomically finalized by this process.",
)
PROGRESS_UPDATES = Counter(
    "typescale_progress_updates_total",
    "Accepted player progress updates persisted to Redis.",
)
REDIS_ERRORS = Counter(
    "typescale_redis_errors_total",
    "Redis errors observed by operation.",
    ("operation",),
)
