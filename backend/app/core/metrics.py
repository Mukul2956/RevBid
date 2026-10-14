"""Prometheus metrics. One module owns every metric so names/labels stay consistent."""

import time
from collections import deque
from dataclasses import dataclass, field

from prometheus_client import Counter, Gauge, Histogram

# --- HTTP -------------------------------------------------------------------------
HTTP_REQUESTS = Counter("http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)

# --- bidding ----------------------------------------------------------------------
BIDS = Counter("bids_total", "Bid attempts by outcome", ["result"])  # accepted | <ERROR_CODE>
BID_DURATION = Histogram(
    "bid_duration_seconds",
    "End-to-end bid transaction time (lock wait + validation + write)",
    buckets=(0.002, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5),
)
BID_LOCK_WAIT = Histogram(
    "bid_lock_wait_seconds",
    "Time spent waiting for the per-auction row lock",
    buckets=(0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 1),
)
SNIPE_EXTENSIONS = Counter("auction_snipe_extensions_total", "Anti-sniping end-time extensions")

# --- lifecycle --------------------------------------------------------------------
AUCTIONS_BY_STATUS = Gauge("auctions", "Auctions by status", ["status"])
AUCTIONS_CLOSED = Counter("auctions_closed_total", "Auctions closed by outcome", ["outcome"])
AUCTIONS_STARTED = Counter("auctions_started_total", "Auctions moved to LIVE")
SCHEDULER_LAG = Histogram(
    "scheduler_lag_seconds",
    "How late an auction was closed relative to its end_at",
    buckets=(0.1, 0.5, 1, 2, 5, 10, 30, 60, 300, 3600),
)
SCHEDULER_LAST_TICK = Gauge(
    "scheduler_last_tick_timestamp_seconds", "Unix time of the last completed scheduler tick"
)
SCHEDULER_ERRORS = Counter("scheduler_errors_total", "Scheduler ticks that raised")

# --- realtime ---------------------------------------------------------------------
WS_CONNECTIONS = Gauge("ws_connections", "Open WebSocket connections")
WS_MESSAGES_SENT = Counter("ws_messages_sent_total", "WebSocket messages sent", ["type"])
WS_DROPPED = Counter(
    "ws_dropped_connections_total", "Connections dropped by the server", ["reason"]
)
REDIS_LISTENER_RECONNECTS = Counter(
    "redis_listener_reconnects_total", "Redis pub/sub listener reconnects"
)
PUBLISH_FAILURES = Counter("realtime_publish_failures_total", "Failed realtime publishes")

# --- rate limiting ----------------------------------------------------------------
RATE_LIMITED = Counter("rate_limited_total", "Requests rejected by rate limiting", ["rule"])
RATE_LIMIT_ERRORS = Counter(
    "rate_limiter_errors_total", "Rate limiter backend failures (fail-open)"
)


@dataclass
class RuntimeStats:
    """In-process counters surfaced on the admin System page (works without Prometheus)."""

    started_at: float = field(default_factory=time.time)
    scheduler_last_tick: float | None = None
    scheduler_last_error: str | None = None
    _bid_times: deque[float] = field(default_factory=lambda: deque(maxlen=5000))

    def record_bid(self) -> None:
        self._bid_times.append(time.time())

    def bids_last_minute(self) -> int:
        cutoff = time.time() - 60
        return sum(1 for t in self._bid_times if t >= cutoff)
