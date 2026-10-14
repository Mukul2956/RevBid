"""Generate dashboards/auction-overview.json.

The dashboard is *code* (this script) so panels stay consistent and reviewable; the generated JSON
is committed because Grafana provisions from it. Re-run after editing:

    python deploy/grafana/build_dashboard.py
"""

import json
from pathlib import Path

DS = {"type": "prometheus", "uid": "prometheus"}
_id = 0


def _next() -> int:
    global _id
    _id += 1
    return _id


def target(expr: str, legend: str = "", ref: str = "A") -> dict:
    return {"datasource": DS, "expr": expr, "legendFormat": legend, "refId": ref}


def timeseries(title: str, x: int, y: int, w: int, h: int, targets: list[dict], unit: str = "short",
               desc: str = "", stack: bool = False) -> dict:
    return {
        "id": _next(), "type": "timeseries", "title": title, "description": desc, "datasource": DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h}, "targets": targets,
        "fieldConfig": {
            "defaults": {"unit": unit, "custom": {"lineWidth": 2, "fillOpacity": 12 if stack else 8,
                                                  "stacking": {"mode": "normal" if stack else "none"}}},
            "overrides": [],
        },
        "options": {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi"}},
    }


def stat(title: str, x: int, y: int, w: int, h: int, expr: str, unit: str = "short", legend: str = "",
         thresholds: list[tuple[str, float | None]] | None = None) -> dict:
    steps = [{"color": c, "value": v} for c, v in (thresholds or [("green", None)])]
    return {
        "id": _next(), "type": "stat", "title": title, "datasource": DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h}, "targets": [target(expr, legend)],
        "fieldConfig": {"defaults": {"unit": unit, "thresholds": {"mode": "absolute", "steps": steps}}, "overrides": []},
        "options": {"reduceOptions": {"calcs": ["lastNotNull"]}, "colorMode": "value", "graphMode": "area"},
    }


def quantiles(metric: str, by: str = "") -> list[dict]:
    group = f"le{', ' + by if by else ''}"
    return [
        target(f"histogram_quantile({q}, sum(rate({metric}_bucket[1m])) by ({group}))", f"p{int(q * 100)}", ref)
        for q, ref in ((0.5, "A"), (0.95, "B"), (0.99, "C"))
    ]


panels = [
    {"id": _next(), "type": "row", "title": "At a glance", "gridPos": {"x": 0, "y": 0, "w": 24, "h": 1}, "collapsed": False, "panels": []},
    stat("Live auctions", 0, 1, 4, 4, 'auctions{status="LIVE"}'),
    stat("Scheduled", 4, 1, 4, 4, 'auctions{status="SCHEDULED"}'),
    stat("WebSocket connections", 8, 1, 4, 4, "sum(ws_connections)"),
    stat("Bids / min", 12, 1, 4, 4, 'sum(rate(bids_total{result="accepted"}[1m])) * 60'),
    stat("Scheduler last tick", 16, 1, 4, 4, "time() - scheduler_last_tick_timestamp_seconds", "s",
         thresholds=[("green", None), ("orange", 5), ("red", 15)]),
    stat("5xx ratio", 20, 1, 4, 4,
         'sum(rate(http_requests_total{status=~"5.."}[5m])) / clamp_min(sum(rate(http_requests_total[5m])), 1)',
         "percentunit", thresholds=[("green", None), ("orange", 0.01), ("red", 0.05)]),

    {"id": _next(), "type": "row", "title": "Bidding (the hot path)", "gridPos": {"x": 0, "y": 5, "w": 24, "h": 1}, "collapsed": False, "panels": []},
    timeseries("Bids per second by result", 0, 6, 8, 8,
               [target("sum(rate(bids_total[1m])) by (result)", "{{result}}")], "ops",
               "accepted vs the stable rejection codes (BID_TOO_LOW, ALREADY_HIGHEST, ...). A rejection spike = contention.", stack=True),
    timeseries("Bid latency (lock wait + validate + write)", 8, 6, 8, 8, quantiles("bid_duration_seconds"), "s",
               "End-to-end transaction time as measured in the API."),
    timeseries("Row-lock wait", 16, 6, 8, 8, quantiles("bid_lock_wait_seconds"), "s",
               "Time bids spend queued behind other bids on the same auction. Rising = a hot auction."),

    {"id": _next(), "type": "row", "title": "Lifecycle", "gridPos": {"x": 0, "y": 14, "w": 24, "h": 1}, "collapsed": False, "panels": []},
    timeseries("Auctions by status", 0, 15, 8, 8, [target("auctions", "{{status}}")], "short", stack=True),
    timeseries("Close lag (end_at -> actually closed)", 8, 15, 8, 8, quantiles("scheduler_lag_seconds"), "s",
               "How late the scheduler settled auctions. Bids are rejected on time regardless; this is announcement delay."),
    timeseries("Closed auctions & anti-snipe extensions", 16, 15, 8, 8,
               [target("sum(increase(auctions_closed_total[5m])) by (outcome)", "closed {{outcome}}"),
                target("sum(increase(auction_snipe_extensions_total[5m]))", "snipe extensions", "B")], "short"),

    {"id": _next(), "type": "row", "title": "Realtime", "gridPos": {"x": 0, "y": 23, "w": 24, "h": 1}, "collapsed": False, "panels": []},
    timeseries("Open WebSockets", 0, 24, 8, 8, [target("sum(ws_connections)", "connections")], "short"),
    timeseries("Messages sent by type", 8, 24, 8, 8,
               [target("sum(rate(ws_messages_sent_total[1m])) by (type)", "{{type}}")], "ops", stack=True),
    timeseries("Drops & backplane health", 16, 24, 8, 8,
               [target("sum(increase(ws_dropped_connections_total[5m])) by (reason)", "dropped {{reason}}"),
                target("sum(increase(redis_listener_reconnects_total[5m]))", "redis reconnects", "B"),
                target("sum(increase(realtime_publish_failures_total[5m]))", "publish failures", "C")], "short"),

    {"id": _next(), "type": "row", "title": "HTTP & protection", "gridPos": {"x": 0, "y": 32, "w": 24, "h": 1}, "collapsed": False, "panels": []},
    timeseries("Requests per second by status class", 0, 33, 8, 8,
               [target('sum(rate(http_requests_total{status=~"2.."}[1m]))', "2xx"),
                target('sum(rate(http_requests_total{status=~"4.."}[1m]))', "4xx", "B"),
                target('sum(rate(http_requests_total{status=~"5.."}[1m]))', "5xx", "C")], "reqps", stack=True),
    timeseries("Latency by route (p95)", 8, 33, 8, 8,
               [target("histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket[1m])) by (le, route))", "{{route}}")], "s"),
    timeseries("Rate-limit rejections", 16, 33, 8, 8,
               [target("sum(increase(rate_limited_total[5m])) by (rule)", "{{rule}}"),
                target("sum(increase(rate_limiter_errors_total[5m]))", "limiter errors (fail-open)", "B")], "short"),
]

dashboard = {
    "uid": "auction-overview", "title": "Auction platform: overview", "tags": ["auction"],
    "timezone": "browser", "schemaVersion": 39, "version": 1, "refresh": "5s",
    "time": {"from": "now-30m", "to": "now"}, "panels": panels, "templating": {"list": []},
    "annotations": {"list": []},
}

out = Path(__file__).parent / "dashboards" / "auction-overview.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(dashboard, indent=2) + "\n")
print(f"wrote {out} ({len(panels)} panels)")
