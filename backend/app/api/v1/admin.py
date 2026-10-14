"""Administrator endpoints. Every route in this router requires the ADMIN role."""

import time
import uuid
from datetime import datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import func, select, text

from app.api.deps import (
    AdminUser,
    ClockDep,
    HubDep,
    PublisherDep,
    RedisDep,
    SessionDep,
    SettingsDep,
    StatsDep,
    require_admin,
)
from app.domain.auction_state import AuctionStatus
from app.models.auction import Auction, AuctionEvent, Bid
from app.models.bike import Bike
from app.models.user import User
from app.schemas.auction import (
    AdminAuctionOut,
    AuctionCreate,
    AuctionUpdate,
    CancelIn,
)
from app.schemas.bike import BikeIn, BikeOut, BikeUpdate
from app.schemas.common import ApiModel, Page
from app.services import auction_queries, auction_service, bike_service, presenters

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


# --------------------------------------------------------------------------- bikes
@router.get("/bikes", response_model=Page[BikeOut])
async def list_bikes(
    session: SessionDep,
    q: Annotated[str | None, Query(max_length=60)] = None,
    cursor: Annotated[str | None, Query(max_length=300)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> Page[BikeOut]:
    return await bike_service.list_bikes(session, q=q, cursor=cursor, limit=limit)


@router.post("/bikes", response_model=BikeOut, status_code=status.HTTP_201_CREATED)
async def create_bike(body: BikeIn, session: SessionDep, admin: AdminUser) -> Bike:
    return await bike_service.create_bike(session, admin, body)


@router.get("/bikes/{bike_id}", response_model=BikeOut)
async def get_bike(bike_id: uuid.UUID, session: SessionDep) -> Bike:
    return await bike_service.get_bike(session, bike_id)


@router.patch("/bikes/{bike_id}", response_model=BikeOut)
async def update_bike(bike_id: uuid.UUID, body: BikeUpdate, session: SessionDep) -> Bike:
    return await bike_service.update_bike(session, bike_id, body)


@router.delete("/bikes/{bike_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_bike(bike_id: uuid.UUID, session: SessionDep) -> Response:
    await bike_service.delete_bike(session, bike_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- auctions
@router.get("/auctions", response_model=Page[AdminAuctionOut])
async def list_auctions(
    session: SessionDep,
    clock: ClockDep,
    status_: Annotated[list[AuctionStatus] | None, Query(alias="status")] = None,
    q: Annotated[str | None, Query(max_length=60)] = None,
    sort: auction_queries.SortKey = "newest",
    cursor: Annotated[str | None, Query(max_length=300)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> Page[AdminAuctionOut]:
    return await auction_queries.list_admin_auctions(
        session,
        clock,
        statuses=[s.value for s in status_ or []],
        q=q,
        sort=sort,
        cursor=cursor,
        limit=limit,
    )


@router.post("/auctions", response_model=AdminAuctionOut, status_code=status.HTTP_201_CREATED)
async def create_auction(
    body: AuctionCreate,
    session: SessionDep,
    clock: ClockDep,
    settings: SettingsDep,
    publisher: PublisherDep,
    admin: AdminUser,
) -> AdminAuctionOut:
    auction = await auction_service.create_auction(
        session, clock=clock, settings=settings, publisher=publisher, admin=admin, data=body
    )
    return presenters.to_admin_auction_out(auction, auction.created_at)


@router.get("/auctions/{auction_id}", response_model=AdminAuctionOut)
async def get_auction(
    auction_id: uuid.UUID, session: SessionDep, clock: ClockDep
) -> AdminAuctionOut:
    return await auction_queries.get_admin_auction(session, clock, auction_id)


@router.patch("/auctions/{auction_id}", response_model=AdminAuctionOut)
async def update_auction(
    auction_id: uuid.UUID,
    body: AuctionUpdate,
    session: SessionDep,
    clock: ClockDep,
    publisher: PublisherDep,
    admin: AdminUser,
) -> AdminAuctionOut:
    await auction_service.update_auction(
        session, clock=clock, publisher=publisher, admin=admin, auction_id=auction_id, data=body
    )
    return await auction_queries.get_admin_auction(session, clock, auction_id)


@router.post("/auctions/{auction_id}/schedule", response_model=AdminAuctionOut)
async def schedule_auction(
    auction_id: uuid.UUID,
    session: SessionDep,
    clock: ClockDep,
    publisher: PublisherDep,
    admin: AdminUser,
) -> AdminAuctionOut:
    await auction_service.schedule_auction(
        session, clock=clock, publisher=publisher, admin=admin, auction_id=auction_id
    )
    return await auction_queries.get_admin_auction(session, clock, auction_id)


@router.post("/auctions/{auction_id}/cancel", response_model=AdminAuctionOut)
async def cancel_auction(
    auction_id: uuid.UUID,
    body: CancelIn,
    session: SessionDep,
    clock: ClockDep,
    publisher: PublisherDep,
    admin: AdminUser,
) -> AdminAuctionOut:
    await auction_service.cancel_auction(
        session,
        clock=clock,
        publisher=publisher,
        admin=admin,
        auction_id=auction_id,
        reason=body.reason,
    )
    return await auction_queries.get_admin_auction(session, clock, auction_id)


class AuctionEventOut(ApiModel):
    id: int
    type: str
    actor_id: uuid.UUID | None
    payload: dict[str, Any]
    created_at: datetime


@router.get("/auctions/{auction_id}/events", response_model=list[AuctionEventOut])
async def auction_events(
    auction_id: uuid.UUID, session: SessionDep, limit: Annotated[int, Query(ge=1, le=500)] = 200
) -> list[AuctionEvent]:
    """The append-only audit trail (created / scheduled / bids / extensions / ended / cancelled)."""
    rows = await session.execute(
        select(AuctionEvent)
        .where(AuctionEvent.auction_id == auction_id)
        .order_by(AuctionEvent.id.desc())
        .limit(limit)
    )
    return list(rows.scalars())


# --------------------------------------------------------------------------- insight
class AdminStats(ApiModel):
    users: int
    auctions_by_status: dict[str, int]
    total_bids: int
    bids_last_hour: int
    sold_count: int
    unsold_count: int
    gross_sold_value: int
    server_time: datetime


@router.get("/stats", response_model=AdminStats)
async def stats(session: SessionDep, clock: ClockDep) -> AdminStats:
    now = await clock.now(session)
    by_status = dict(
        (await session.execute(select(Auction.status, func.count()).group_by(Auction.status))).all()
    )
    outcome_rows = (
        await session.execute(
            select(Auction.outcome, func.count(), func.coalesce(func.sum(Auction.current_price), 0))
            .where(Auction.outcome.is_not(None))
            .group_by(Auction.outcome)
        )
    ).all()
    by_outcome = {outcome: (n, int(total)) for outcome, n, total in outcome_rows}
    sold_count, gross = by_outcome.get("SOLD", (0, 0))
    unsold_count, _ = by_outcome.get("UNSOLD", (0, 0))
    hour_ago = now - timedelta(hours=1)
    return AdminStats(
        users=(await session.execute(select(func.count()).select_from(User))).scalar_one(),
        auctions_by_status={s.value: by_status.get(s.value, 0) for s in AuctionStatus},
        total_bids=(await session.execute(select(func.count()).select_from(Bid))).scalar_one(),
        bids_last_hour=(
            await session.execute(
                select(func.count()).select_from(Bid).where(Bid.created_at >= hour_ago)
            )
        ).scalar_one(),
        sold_count=sold_count,
        unsold_count=unsold_count,
        gross_sold_value=gross,
        server_time=now,
    )


class ComponentHealth(ApiModel):
    ok: bool
    latency_ms: float | None = None
    error: str | None = None


class SystemInfo(ApiModel):
    version: str
    env: str
    uptime_s: float
    database: ComponentHealth
    redis: ComponentHealth
    websocket_connections: int
    websocket_rooms: int
    scheduler_enabled: bool
    scheduler_last_tick_age_s: float | None
    scheduler_last_error: str | None
    bids_last_minute: int
    demo_mode: bool


@router.get("/system", response_model=SystemInfo)
async def system(
    session: SessionDep,
    settings: SettingsDep,
    runtime: StatsDep,
    hub: HubDep,
    redis: RedisDep,
) -> SystemInfo:
    """Operational snapshot for the admin console: health of dependencies and key runtime gauges."""
    db = await _probe(lambda: session.execute(text("SELECT 1")))
    rd = await _probe(redis.ping)
    last_tick = runtime.scheduler_last_tick
    return SystemInfo(
        version=settings.app_version,
        env=settings.env,
        uptime_s=round(time.time() - runtime.started_at, 1),
        database=db,
        redis=rd,
        websocket_connections=hub.connection_count,
        websocket_rooms=hub.room_count,
        scheduler_enabled=settings.run_scheduler,
        scheduler_last_tick_age_s=round(time.time() - last_tick, 2) if last_tick else None,
        scheduler_last_error=runtime.scheduler_last_error,
        bids_last_minute=runtime.bids_last_minute(),
        demo_mode=settings.demo_mode,
    )


async def _probe(fn: Any) -> ComponentHealth:
    started = time.perf_counter()
    try:
        await fn()
    except Exception as exc:  # report, never raise: this *is* the health report
        return ComponentHealth(ok=False, error=type(exc).__name__)
    return ComponentHealth(ok=True, latency_ms=round((time.perf_counter() - started) * 1000, 2))
