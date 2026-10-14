"""Test data builders. Rows are inserted directly for speed; flows under test go through the API."""

import uuid
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.security import Principal, create_access_token, hash_password
from app.models.auction import Auction
from app.models.bike import Bike
from app.models.user import User

PASSWORD = "Passw0rd!"
# argon2 is deliberately slow; hash once and share it across test users.
_PASSWORD_HASH = hash_password(PASSWORD)
RUPEE = 100  # paise


async def make_user(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    email: str | None = None,
    role: str = "BIDDER",
    name: str = "Test User",
    active: bool = True,
) -> User:
    user = User(
        email=email or f"user-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=_PASSWORD_HASH,
        display_name=name,
        role=role,
        is_active=active,
    )
    async with sessionmaker() as session, session.begin():
        session.add(user)
    return user


async def make_bike(sessionmaker: async_sessionmaker[AsyncSession], **overrides: object) -> Bike:
    fields: dict[str, object] = {
        "make": "Royal Enfield",
        "model": "Classic 350",
        "year": 2021,
        "km_driven": 12_000,
        "owner_number": 1,
        "condition": "GOOD",
        "description": "Test bike",
        "images": [],
    }
    fields.update(overrides)
    bike = Bike(**fields)
    async with sessionmaker() as session, session.begin():
        session.add(bike)
    return bike


async def make_auction(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    now: datetime,
    status: str = "LIVE",
    start_in: timedelta = timedelta(minutes=-10),
    duration: timedelta = timedelta(minutes=30),
    starting_price: int = 50_000 * RUPEE,
    reserve_price: int | None = None,
    min_increment: int = 500 * RUPEE,
    snipe_window_s: int = 120,
    snipe_extension_s: int = 120,
    max_extension_s: int = 1800,
    bike: Bike | None = None,
) -> Auction:
    bike = bike or await make_bike(sessionmaker)
    start = now + start_in
    auction = Auction(
        bike_id=bike.id,
        status=status,
        start_at=start,
        end_at=start + duration,
        original_end_at=start + duration,
        starting_price=starting_price,
        reserve_price=reserve_price,
        min_increment=min_increment,
        snipe_window_s=snipe_window_s,
        snipe_extension_s=snipe_extension_s,
        max_extension_s=max_extension_s,
        current_price=starting_price,
    )
    async with sessionmaker() as session, session.begin():
        session.add(auction)
    auction.bike = bike
    return auction


def auth(settings: Settings, user: User) -> dict[str, str]:
    principal = Principal(id=user.id, role=user.role, display_name=user.display_name)
    return {"Authorization": f"Bearer {create_access_token(settings, principal)}"}
