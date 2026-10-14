import httpx
import pytest
from asgi_lifespan import LifespanManager

from app.core.config import Settings
from app.main import create_app


async def test_liveness_and_readiness(client) -> None:
    assert (await client.get("/healthz")).json() == {"status": "ok"}
    ready = (await client.get("/readyz")).json()
    assert ready == {"status": "ready", "checks": {"database": "ok", "redis": "ok"}}


async def test_readiness_reports_a_dead_dependency_with_503(client, app) -> None:
    class DeadRedis:
        async def ping(self) -> None:
            raise ConnectionError("down")

    real = app.state.redis
    app.state.redis = DeadRedis()
    try:
        r = await client.get("/readyz")
    finally:
        app.state.redis = real
    assert r.status_code == 503
    assert r.json()["checks"] == {"database": "ok", "redis": "error: ConnectionError"}
    assert (
        await client.get("/healthz")
    ).status_code == 200  # liveness is independent of dependencies


async def test_server_time_endpoint(client, clock) -> None:
    r = await client.get("/api/v1/time")
    assert r.status_code == 200 and r.json()["server_time"].startswith("2030-01-01T12:00:00")


async def test_metrics_exposes_prometheus_text(client) -> None:
    await client.get("/api/v1/auctions")
    r = await client.get("/metrics")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    for name in (
        "http_requests_total",
        "http_request_duration_seconds_bucket",
        "bids_total",
        "ws_connections",
        "scheduler_lag_seconds",
        "auctions_closed_total",
    ):
        assert name in r.text, name
    assert 'route="/api/v1/auctions"' in r.text  # route *template*, bounded label cardinality


async def test_metrics_route_label_is_the_template_not_the_raw_path(client) -> None:
    await client.get("/api/v1/auctions/00000000-0000-0000-0000-00000000aaaa")
    text = (await client.get("/metrics")).text
    assert 'route="/api/v1/auctions/{auction_id}"' in text
    assert "00000000-0000-0000-0000-00000000aaaa" not in text


async def test_metrics_token_protects_the_endpoint(settings, clock, publisher) -> None:
    protected = Settings(**{**settings.model_dump(), "metrics_token": "s3cret-token"})
    app = create_app(protected, clock=clock, publisher=publisher)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c,
    ):
        assert (await c.get("/metrics")).status_code == 401
        assert (
            await c.get("/metrics", headers={"Authorization": "Bearer wrong"})
        ).status_code == 401
        ok = await c.get("/metrics", headers={"Authorization": "Bearer s3cret-token"})
        assert ok.status_code == 200


async def test_security_headers_on_every_response(client) -> None:
    for path in ("/healthz", "/api/v1/auctions", "/no/such/route"):
        h = (await client.get(path)).headers
        assert h["x-content-type-options"] == "nosniff"
        assert h["x-frame-options"] == "DENY"
        assert h["referrer-policy"] == "no-referrer"


async def test_request_id_is_generated_echoed_and_sanitised(client) -> None:
    generated = (await client.get("/healthz")).headers["x-request-id"]
    assert len(generated) == 32
    echoed = await client.get("/healthz", headers={"X-Request-ID": "trace-abc-123"})
    assert echoed.headers["x-request-id"] == "trace-abc-123"
    too_long = await client.get("/healthz", headers={"X-Request-ID": "x" * 200})
    assert too_long.headers["x-request-id"] != "x" * 200


async def test_errors_are_problem_json_with_request_id(client) -> None:
    missing = await client.get("/api/v1/nope")
    assert missing.status_code == 404
    assert missing.headers["content-type"].startswith("application/problem+json")
    body = missing.json()
    assert (
        body["code"] == "NOT_FOUND"
        and body["status"] == 404
        and body["request_id"] == missing.headers["x-request-id"]
    )

    wrong_method = await client.delete("/api/v1/auctions")
    assert wrong_method.status_code == 405 and wrong_method.json()["code"] == "METHOD_NOT_ALLOWED"

    invalid = await client.post("/api/v1/auth/login", json={"email": "bad"})
    assert invalid.status_code == 422
    fields = {e["field"] for e in invalid.json()["errors"]}
    assert fields == {"email", "password"}


async def test_unhandled_errors_become_clean_500s_without_leaking_details(app, client) -> None:
    async def boom() -> None:
        raise RuntimeError("secret internal detail: db password=hunter2")

    app.add_api_route("/_boom", boom)
    r = await client.get("/_boom")
    assert r.status_code == 500
    assert r.json()["code"] == "INTERNAL_ERROR"
    assert "hunter2" not in r.text and "Traceback" not in r.text
    assert r.json()["request_id"] == r.headers["x-request-id"]


@pytest.mark.parametrize(
    ("origin", "allowed"), [("http://localhost:5173", True), ("https://evil.example", False)]
)
async def test_cors_allowlist(client, origin, allowed) -> None:
    r = await client.options(
        "/api/v1/auctions",
        headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
    )
    assert (r.headers.get("access-control-allow-origin") == origin) is allowed
    if allowed:
        assert r.headers["access-control-allow-credentials"] == "true"


async def test_openapi_document_and_docs_are_served(client) -> None:
    spec = (await client.get("/api/v1/openapi.json")).json()
    assert spec["info"]["title"] == "Bike Auction API"
    assert "/api/v1/auctions/{auction_id}/bids" in spec["paths"]
    assert (await client.get("/docs")).status_code == 200


async def test_oversized_bodies_are_rejected_before_parsing(client) -> None:
    huge = {"email": "a@b.co", "password": "x" * 400_000}
    r = await client.post("/api/v1/auth/login", json=huge)
    assert r.status_code == 413 and r.json()["code"] == "PAYLOAD_TOO_LARGE"
    assert r.headers["content-type"].startswith("application/problem+json")
    # ordinary requests are unaffected
    ok = await client.post("/api/v1/auth/login", json={"email": "a@b.co", "password": "pw"})
    assert ok.status_code == 401


async def test_oversized_chunked_bodies_without_content_length_are_rejected(client) -> None:
    async def chunks():  # type: ignore[no-untyped-def]
        for _ in range(40):
            yield b"x" * 10_000  # 400 KB streamed, no Content-Length header

    r = await client.post(
        "/api/v1/auth/login", content=chunks(), headers={"Content-Type": "application/json"}
    )
    assert r.status_code == 413


async def test_refresh_endpoint_is_rate_limited(settings, clock, publisher) -> None:
    limited = Settings(
        **{**settings.model_dump(), "rate_limit_enabled": True, "rate_limit_refresh_per_min": 3}
    )
    app = create_app(limited, clock=clock, publisher=publisher)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c,
    ):
        codes = [(await c.post("/api/v1/auth/refresh")).status_code for _ in range(5)]
    assert codes == [401, 401, 401, 429, 429]


async def test_read_endpoints_use_no_transaction_on_the_wire(
    app, client, clock, sessionmaker
) -> None:
    """Throughput is bound by DB round trips, so reads must not send BEGIN/ROLLBACK (or a ping).

    This property silently regressed once: with pool_pre_ping enabled the driver sends
    BEGIN/ROLLBACK even in AUTOCOMMIT mode. Asserting at the asyncpg level catches that.
    """
    import asyncpg

    from tests.factories import make_auction

    auction = await make_auction(sessionmaker, now=clock.current)
    await client.get(f"/api/v1/auctions/{auction.id}")  # warm the pool: measure a reused connection

    sent: list[str] = []
    original = asyncpg.Connection.execute

    async def spy(self, query, *args, **kwargs):  # type: ignore[no-untyped-def]
        sent.append(" ".join(str(query).split()).upper())
        return await original(self, query, *args, **kwargs)

    asyncpg.Connection.execute = spy  # type: ignore[method-assign]
    try:
        for path in (
            f"/api/v1/auctions/{auction.id}",
            "/api/v1/auctions",
            "/api/v1/auctions/makes",
        ):
            sent.clear()
            assert (await client.get(path)).status_code == 200
            assert not any(
                q.startswith(("BEGIN", "ROLLBACK", "COMMIT")) or q == ";" for q in sent
            ), (path, sent)
    finally:
        asyncpg.Connection.execute = original  # type: ignore[method-assign]
