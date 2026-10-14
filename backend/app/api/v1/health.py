"""Liveness, readiness, time sync and Prometheus scrape endpoints (outside the /api/v1 prefix)."""

import hmac
from datetime import datetime

from fastapi import APIRouter, Header, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from app.api.deps import ClockDep, ReadSessionDep, RedisDep, SettingsDep
from app.domain.errors import Unauthenticated
from app.schemas.common import ApiModel

router = APIRouter(tags=["ops"])


class TimeOut(ApiModel):
    server_time: datetime


@router.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    """Liveness: the process is up and the event loop is responsive. Touches no dependency."""
    return {"status": "ok"}


@router.get("/readyz", include_in_schema=False)
async def readyz(response: Response, session: ReadSessionDep, redis: RedisDep) -> dict[str, object]:
    """Readiness: can this instance actually serve traffic (database + Redis reachable)?"""
    checks: dict[str, str] = {}
    try:
        await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # report every failing dependency, not just the first
        checks["database"] = f"error: {type(exc).__name__}"
    try:
        await redis.ping()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = f"error: {type(exc).__name__}"
    ready = all(v == "ok" for v in checks.values())
    if not ready:
        response.status_code = 503
    return {"status": "ready" if ready else "unavailable", "checks": checks}


@router.get("/api/v1/time", response_model=TimeOut, tags=["time"])
async def server_time(session: ReadSessionDep, clock: ClockDep) -> TimeOut:
    """Authoritative clock for client countdowns (clients compute their offset from this)."""
    return TimeOut(server_time=await clock.now(session))


@router.get("/metrics", include_in_schema=False)
async def metrics_endpoint(
    request: Request,
    settings: SettingsDep,
    authorization: str | None = Header(default=None),
) -> Response:
    if settings.metrics_token:
        expected = f"Bearer {settings.metrics_token}"
        if not authorization or not hmac.compare_digest(authorization, expected):
            raise Unauthenticated("Metrics token required")
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
