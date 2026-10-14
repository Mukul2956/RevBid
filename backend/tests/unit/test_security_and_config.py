import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from pydantic import ValidationError

from app.core.config import _INSECURE_DEFAULT_SECRET, Settings, normalize_database_url
from app.core.security import (
    Principal,
    create_access_token,
    decode_access_token,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    verify_password,
)
from app.domain.errors import InvalidToken
from app.domain.masking import bidder_alias
from app.services.pagination import InvalidCursor, decode_cursor, encode_cursor

SETTINGS = Settings(jwt_secret="x" * 40, env="test")
USER = Principal(id=uuid.uuid4(), role="BIDDER", display_name="Asha")


class TestPasswords:
    def test_hash_is_argon2id_and_verifies(self) -> None:
        h = hash_password("correct horse 1")
        assert h.startswith("$argon2id$")
        assert verify_password("correct horse 1", h)
        assert not verify_password("wrong", h)

    def test_unknown_user_never_verifies(self) -> None:
        assert verify_password("not-a-real-password", None) is False

    def test_hashes_are_salted(self) -> None:
        assert hash_password("same") != hash_password("same")


class TestAccessTokens:
    def test_roundtrip(self) -> None:
        token = create_access_token(SETTINGS, USER)
        assert decode_access_token(SETTINGS, token) == USER

    def test_expired(self) -> None:
        token = create_access_token(SETTINGS, USER, now=datetime.now(UTC) - timedelta(hours=1))
        with pytest.raises(InvalidToken):
            decode_access_token(SETTINGS, token)

    def test_tampered_signature(self) -> None:
        token = create_access_token(SETTINGS, USER)
        with pytest.raises(InvalidToken):
            decode_access_token(SETTINGS, token[:-3] + "abc")

    def test_wrong_secret(self) -> None:
        other = Settings(jwt_secret="y" * 40, env="test")
        with pytest.raises(InvalidToken):
            decode_access_token(other, create_access_token(SETTINGS, USER))

    def test_alg_none_is_rejected(self) -> None:
        forged = jwt.encode(
            {
                "sub": str(USER.id),
                "role": "ADMIN",
                "typ": "access",
                "exp": datetime.now(UTC) + timedelta(hours=1),
            },
            key="",
            algorithm="none",
        )
        with pytest.raises(InvalidToken):
            decode_access_token(SETTINGS, forged)

    def test_refresh_style_token_is_not_an_access_token(self) -> None:
        token = jwt.encode(
            {
                "sub": str(USER.id),
                "role": "BIDDER",
                "typ": "refresh",
                "exp": datetime.now(UTC) + timedelta(hours=1),
            },
            SETTINGS.jwt_secret,
            algorithm="HS256",
        )
        with pytest.raises(InvalidToken):
            decode_access_token(SETTINGS, token)

    def test_garbage(self) -> None:
        with pytest.raises(InvalidToken):
            decode_access_token(SETTINGS, "not.a.jwt")


def test_refresh_tokens_are_random_and_stored_hashed() -> None:
    raw1, hash1 = new_refresh_token()
    raw2, _ = new_refresh_token()
    assert raw1 != raw2 and len(raw1) >= 60
    assert hash1 == hash_refresh_token(raw1) and hash1 != raw1


class TestMasking:
    def test_stable_within_an_auction(self) -> None:
        a, u = uuid.uuid4(), uuid.uuid4()
        assert bidder_alias(a, u) == bidder_alias(a, u)

    def test_differs_across_auctions_and_users(self) -> None:
        a1, a2, u1, u2 = (uuid.uuid4() for _ in range(4))
        assert bidder_alias(a1, u1) != bidder_alias(a2, u1)
        assert bidder_alias(a1, u1) != bidder_alias(a1, u2)

    def test_does_not_leak_the_user_id(self) -> None:
        a, u = uuid.uuid4(), uuid.uuid4()
        alias = bidder_alias(a, u)
        assert alias.startswith("Bidder-") and str(u)[:8] not in alias.lower()


class TestConfig:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("postgres://u:p@h:5432/d", "postgresql+asyncpg://u:p@h:5432/d"),
            ("postgresql://u:p@h/d", "postgresql+asyncpg://u:p@h/d"),
            ("postgresql+asyncpg://u:p@h/d", "postgresql+asyncpg://u:p@h/d"),
            ("postgresql://u:p@h/d?sslmode=require", "postgresql+asyncpg://u:p@h/d?ssl=require"),
        ],
    )
    def test_database_url_normalisation(self, raw: str, expected: str) -> None:
        assert normalize_database_url(raw) == expected

    def test_production_rejects_short_secret(self) -> None:
        with pytest.raises(ValidationError, match="JWT_SECRET"):
            Settings(env="production", cookie_secure=True, jwt_secret="short")

    def test_production_rejects_the_built_in_dev_secret(self) -> None:
        with pytest.raises(ValidationError, match="JWT_SECRET"):
            Settings(env="production", cookie_secure=True, jwt_secret=_INSECURE_DEFAULT_SECRET)

    def test_production_requires_secure_cookies(self) -> None:
        with pytest.raises(ValidationError, match="COOKIE_SECURE"):
            Settings(env="production", jwt_secret="z" * 40, cookie_secure=False)

    def test_samesite_none_requires_secure(self) -> None:
        with pytest.raises(ValidationError, match="SameSite"):
            Settings(env="local", cookie_samesite="none", cookie_secure=False)

    def test_json_logging_default_follows_env(self) -> None:
        assert Settings(env="local").log_json is False
        assert Settings(env="test").log_json is True


class TestCursors:
    def test_roundtrip_int_and_datetime(self) -> None:
        rid = uuid.uuid4()
        assert decode_cursor(encode_cursor(123, rid), as_datetime=False) == (123, rid)
        ts = datetime(2030, 1, 1, 12, tzinfo=UTC)
        assert decode_cursor(encode_cursor(ts, rid), as_datetime=True) == (ts, rid)

    @pytest.mark.parametrize("bad", ["", "!!!", "e30", "eyJ2IjoxfQ"])
    def test_garbage_is_a_clean_validation_error(self, bad: str) -> None:
        with pytest.raises(InvalidCursor):
            decode_cursor(bad, as_datetime=False)
