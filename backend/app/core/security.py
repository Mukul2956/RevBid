"""Password hashing (argon2id), access-token JWTs, and opaque refresh tokens."""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import Settings
from app.domain.errors import InvalidToken

_hasher = PasswordHasher()  # argon2id with the library's current recommended parameters

# Verified against when the e-mail is unknown so login timing doesn't reveal which e-mails exist.
_DUMMY_HASH = _hasher.hash("not-a-real-password")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


@dataclass(frozen=True)
class Principal:
    """The authenticated caller, reconstructed from JWT claims (no DB hit per request)."""

    id: uuid.UUID
    role: str
    display_name: str

    @property
    def is_admin(self) -> bool:
        return self.role == "ADMIN"


def create_access_token(
    settings: Settings, principal: Principal, now: datetime | None = None
) -> str:
    now = now or datetime.now(UTC)
    payload = {
        "sub": str(principal.id),
        "role": principal.role,
        "name": principal.display_name,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.access_token_ttl_minutes)).timestamp()),
        "typ": "access",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(settings: Settings, token: str) -> Principal:
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],  # pinned: never trust the token's own `alg`
            options={"require": ["exp", "sub", "typ"]},
        )
        if claims["typ"] != "access":
            raise InvalidToken("Wrong token type")
        return Principal(
            id=uuid.UUID(claims["sub"]), role=claims["role"], display_name=claims.get("name", "")
        )
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise InvalidToken("Invalid or expired token") from exc


def new_refresh_token() -> tuple[str, str]:
    """Return (raw token for the cookie, sha256 hash for the database)."""
    raw = secrets.token_urlsafe(48)
    return raw, hash_refresh_token(raw)


def hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()
