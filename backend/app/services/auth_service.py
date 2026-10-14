"""Registration, login, and refresh-token rotation.

Token model: a short-lived JWT access token (kept in memory by the SPA) plus an opaque,
rotating refresh token (httpOnly cookie, stored hashed). Each login starts a token *family*;
every refresh revokes the presented token and issues its successor in the same family.
Presenting an already-rotated token after the grace window is treated as theft and revokes the
entire family.
"""

import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.security import (
    Principal,
    create_access_token,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    verify_password,
)
from app.domain.errors import EmailTaken, InvalidCredentials, InvalidToken
from app.models.user import RefreshToken, User
from app.schemas.auth import RegisterIn

log = structlog.stdlib.get_logger("app.auth")

# Two tabs refreshing simultaneously present the same cookie; the loser must not be mistaken
# for a thief. Within this window a rotated token is rejected without revoking the family.
REUSE_GRACE = timedelta(seconds=10)


def normalize_email(email: str) -> str:
    return email.strip().lower()


def principal_for(user: User) -> Principal:
    return Principal(id=user.id, role=user.role, display_name=user.display_name)


async def register(session: AsyncSession, data: RegisterIn) -> User:
    user = User(
        email=normalize_email(data.email),
        password_hash=hash_password(data.password),
        display_name=data.display_name.strip(),
        role="BIDDER",
    )
    try:
        async with session.begin():
            session.add(user)
            await session.flush()
    except IntegrityError as exc:
        raise EmailTaken("An account with this e-mail already exists") from exc
    log.info("user_registered", user_id=str(user.id))
    return user


async def authenticate(session: AsyncSession, email: str, password: str) -> User:
    async with session.begin():
        user = (
            await session.execute(select(User).where(User.email == normalize_email(email)))
        ).scalar_one_or_none()
    # Always run a verification (against a dummy hash when the user is unknown) so timing does
    # not reveal which e-mails are registered.
    ok = verify_password(password, user.password_hash if user else None)
    if not (user and ok and user.is_active):
        log.info("login_failed")
        raise InvalidCredentials("Incorrect e-mail or password")
    return user


async def issue_tokens(
    session: AsyncSession, settings: Settings, user: User, family_id: uuid.UUID | None = None
) -> tuple[str, str]:
    """Return (access_token, raw_refresh_token) and persist the refresh token's hash."""
    raw, token_hash = new_refresh_token()
    async with session.begin():
        session.add(
            RefreshToken(
                user_id=user.id,
                family_id=family_id or uuid.uuid4(),
                token_hash=token_hash,
                expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_ttl_days),
            )
        )
    return create_access_token(settings, principal_for(user)), raw


async def rotate_refresh(
    session: AsyncSession, settings: Settings, raw_token: str
) -> tuple[User, str, str]:
    """Exchange a refresh token for a new (access, refresh) pair."""
    now = datetime.now(UTC)
    token_hash = hash_refresh_token(raw_token)
    theft_detected = False
    user: User | None = None
    new_raw = ""

    async with session.begin():
        token = (
            await session.execute(
                select(RefreshToken).where(RefreshToken.token_hash == token_hash).with_for_update()
            )
        ).scalar_one_or_none()
        if token is None or token.expires_at <= now:
            raise InvalidToken("Invalid or expired refresh token")

        if token.revoked_at is not None:
            if now - token.revoked_at > REUSE_GRACE:
                theft_detected = True
                await session.execute(
                    update(RefreshToken)
                    .where(
                        RefreshToken.family_id == token.family_id, RefreshToken.revoked_at.is_(None)
                    )
                    .values(revoked_at=now)
                )
        else:
            user = await session.get(User, token.user_id)
            if user is None or not user.is_active:
                raise InvalidToken("Invalid or expired refresh token")
            token.revoked_at = now
            new_raw, new_hash = new_refresh_token()
            session.add(
                RefreshToken(
                    user_id=user.id,
                    family_id=token.family_id,
                    token_hash=new_hash,
                    expires_at=now + timedelta(days=settings.refresh_token_ttl_days),
                )
            )

    # Raised after the transaction commits so the family revocation above is not rolled back.
    if theft_detected:
        log.warning("refresh_token_reuse_detected", family_id=str(token.family_id))
        raise InvalidToken("Invalid or expired refresh token")
    if user is None:
        raise InvalidToken("Invalid or expired refresh token")
    return user, create_access_token(settings, principal_for(user)), new_raw


async def revoke_refresh(session: AsyncSession, raw_token: str) -> None:
    """Logout: revoke the whole family so the session can't be resurrected from another tab."""
    token_hash = hash_refresh_token(raw_token)
    async with session.begin():
        token = (
            await session.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
        ).scalar_one_or_none()
        if token is None:
            return
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == token.family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
