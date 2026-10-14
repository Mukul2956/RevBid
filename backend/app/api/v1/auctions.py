import re
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Header, Query, status

from app.api.deps import (
    ClockDep,
    CurrentUser,
    LimiterDep,
    OptionalPrincipal,
    PublisherDep,
    ReadSessionDep,
    SessionDep,
    SettingsDep,
    StatsDep,
)
from app.domain.errors import DomainError
from app.schemas.auction import AuctionOut, BidAccepted, BidIn, BidOut, MyBidOut
from app.schemas.common import Page
from app.services import auction_queries, bid_service

router = APIRouter(tags=["auctions"])

_IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9_\-]{8,64}$")


class InvalidIdempotencyKey(DomainError):
    code = "VALIDATION_ERROR"


@router.get("/auctions", response_model=Page[AuctionOut])
async def list_auctions(
    session: ReadSessionDep,
    clock: ClockDep,
    viewer: OptionalPrincipal,
    status_: Annotated[
        list[Literal["LIVE", "SCHEDULED", "ENDED"]] | None, Query(alias="status")
    ] = None,
    q: Annotated[str | None, Query(max_length=60)] = None,
    make: Annotated[str | None, Query(max_length=60)] = None,
    year_min: Annotated[int | None, Query(ge=1980, le=2100)] = None,
    year_max: Annotated[int | None, Query(ge=1980, le=2100)] = None,
    price_min: Annotated[int | None, Query(ge=0, description="Paise")] = None,
    price_max: Annotated[int | None, Query(ge=0, description="Paise")] = None,
    sort: auction_queries.SortKey = "ending_soon",
    cursor: Annotated[str | None, Query(max_length=300)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> Page[AuctionOut]:
    return await auction_queries.list_public_auctions(
        session,
        clock,
        viewer_id=viewer.id if viewer else None,
        statuses=list(status_ or ["LIVE"]),
        q=q,
        make=make,
        year_min=year_min,
        year_max=year_max,
        price_min=price_min,
        price_max=price_max,
        sort=sort,
        cursor=cursor,
        limit=limit,
    )


@router.get("/auctions/makes", response_model=list[str])
async def list_makes(session: ReadSessionDep) -> list[str]:
    return await auction_queries.list_makes(session)


@router.get("/auctions/{auction_id}", response_model=AuctionOut)
async def get_auction(
    auction_id: uuid.UUID, session: ReadSessionDep, clock: ClockDep, viewer: OptionalPrincipal
) -> AuctionOut:
    return await auction_queries.get_public_auction(
        session, clock, auction_id, viewer.id if viewer else None
    )


@router.get("/auctions/{auction_id}/bids", response_model=Page[BidOut])
async def list_bids(
    auction_id: uuid.UUID,
    session: ReadSessionDep,
    cursor: Annotated[str | None, Query(max_length=300)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> Page[BidOut]:
    return await auction_queries.list_bids(session, auction_id, cursor=cursor, limit=limit)


@router.post(
    "/auctions/{auction_id}/bids", response_model=BidAccepted, status_code=status.HTTP_201_CREATED
)
async def place_bid(
    auction_id: uuid.UUID,
    body: BidIn,
    session: SessionDep,
    clock: ClockDep,
    publisher: PublisherDep,
    settings: SettingsDep,
    stats: StatsDep,
    limiter: LimiterDep,
    user: CurrentUser,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> BidAccepted:
    if idempotency_key is not None and not _IDEMPOTENCY_RE.match(idempotency_key):
        raise InvalidIdempotencyKey("Idempotency-Key must be 8-64 chars of [A-Za-z0-9_-]")
    await limiter.hit("bid", str(user.id), limit=settings.rate_limit_bids_per_10s, window_s=10)
    return await bid_service.place_bid(
        session,
        clock=clock,
        publisher=publisher,
        settings=settings,
        stats=stats,
        auction_id=auction_id,
        bidder=user,
        amount=body.amount,
        idempotency_key=idempotency_key,
    )


@router.get("/me/bids", response_model=list[MyBidOut], tags=["me"])
async def my_bids(session: ReadSessionDep, clock: ClockDep, user: CurrentUser) -> list[MyBidOut]:
    return await auction_queries.my_bids(session, clock, user.id)
