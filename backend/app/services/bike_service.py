"""Bike catalogue management (admin)."""

import uuid

from sqlalchemy import func, select, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import Principal
from app.domain.errors import Conflict, NotFound
from app.models.bike import Bike
from app.schemas.bike import BikeIn, BikeOut, BikeUpdate
from app.schemas.common import Page
from app.services.pagination import decode_cursor, encode_cursor
from app.services.sqlutil import escape_like

_NULLABLE_FIELDS = {"variant", "engine_cc", "color", "registration_state", "inspection_score"}


async def create_bike(session: AsyncSession, admin: Principal, data: BikeIn) -> Bike:
    bike = Bike(**data.model_dump(), created_by=admin.id)
    async with session.begin():
        session.add(bike)
        await session.flush()
    return bike


async def get_bike(session: AsyncSession, bike_id: uuid.UUID) -> Bike:
    bike = await session.get(Bike, bike_id)
    if bike is None:
        raise NotFound("Bike not found")
    return bike


async def update_bike(session: AsyncSession, bike_id: uuid.UUID, data: BikeUpdate) -> Bike:
    async with session.begin():
        bike = await get_bike(session, bike_id)
        for field, value in data.model_dump(exclude_unset=True).items():
            if value is not None or field in _NULLABLE_FIELDS:
                setattr(bike, field, value)
    return bike


async def delete_bike(session: AsyncSession, bike_id: uuid.UUID) -> None:
    try:
        async with session.begin():
            bike = await get_bike(session, bike_id)
            await session.delete(bike)
    except IntegrityError as exc:  # FK RESTRICT from auctions
        raise Conflict("Bike is referenced by an auction and cannot be deleted") from exc


async def list_bikes(
    session: AsyncSession, *, q: str | None, cursor: str | None, limit: int
) -> Page[BikeOut]:
    stmt = select(Bike).order_by(Bike.created_at.desc(), Bike.id.desc()).limit(limit + 1)
    if q:
        like = f"%{escape_like(q.strip())}%"
        stmt = stmt.where(func.concat(Bike.make, " ", Bike.model).ilike(like, escape="\\"))
    if cursor:
        value, last_id = decode_cursor(cursor, as_datetime=True)
        stmt = stmt.where(tuple_(Bike.created_at, Bike.id) < tuple_(value, last_id))
    rows = list((await session.execute(stmt)).scalars())
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = encode_cursor(rows[-1].created_at, rows[-1].id)
    return Page[BikeOut](items=[BikeOut.model_validate(b) for b in rows], next_cursor=next_cursor)
