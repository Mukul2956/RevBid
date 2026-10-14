from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select, update

from app.models.user import RefreshToken, User
from tests.factories import PASSWORD, make_user

REGISTER = {"email": "Asha@Example.com", "password": "Sup3rSecret", "display_name": "Asha"}


async def test_register_returns_token_user_and_httponly_cookie(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/v1/auth/register", json=REGISTER)

    assert r.status_code == 201
    body = r.json()
    assert body["token_type"] == "bearer" and body["expires_in"] == 15 * 60
    assert body["user"] == {**body["user"], "email": "asha@example.com", "role": "BIDDER"}
    cookie = r.headers["set-cookie"].lower()
    assert "refresh_token=" in cookie and "httponly" in cookie
    assert "path=/api/v1/auth" in cookie and "samesite=lax" in cookie
    assert "password" not in r.text and "hash" not in r.text


async def test_registration_cannot_create_admins_or_override_role(client, sessionmaker) -> None:
    r = await client.post("/api/v1/auth/register", json={**REGISTER, "role": "ADMIN"})
    assert r.status_code == 201 and r.json()["user"]["role"] == "BIDDER"


async def test_duplicate_email_is_rejected_case_insensitively(client) -> None:
    await client.post("/api/v1/auth/register", json=REGISTER)
    r = await client.post("/api/v1/auth/register", json={**REGISTER, "email": "asha@example.com"})
    assert r.status_code == 409 and r.json()["code"] == "EMAIL_TAKEN"


async def test_register_validation(client) -> None:
    cases = [
        {**REGISTER, "email": "not-an-email"},
        {**REGISTER, "password": "short1"},
        {**REGISTER, "password": "onlyletters"},
        {**REGISTER, "password": "12345678"},
        {**REGISTER, "display_name": "A"},
    ]
    for payload in cases:
        r = await client.post("/api/v1/auth/register", json=payload)
        assert r.status_code == 422, payload
        assert r.json()["errors"], payload


async def test_login_success_and_me(client, sessionmaker) -> None:
    user = await make_user(sessionmaker, email="login@example.com", name="Login User")
    r = await client.post(
        "/api/v1/auth/login", json={"email": "LOGIN@example.com", "password": PASSWORD}
    )
    assert r.status_code == 200
    me = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"}
    )
    assert me.status_code == 200 and me.json()["id"] == str(user.id)


async def test_wrong_password_and_unknown_user_look_identical(client, sessionmaker) -> None:
    await make_user(sessionmaker, email="real@example.com")
    wrong = await client.post(
        "/api/v1/auth/login", json={"email": "real@example.com", "password": "nope"}
    )
    unknown = await client.post(
        "/api/v1/auth/login", json={"email": "ghost@example.com", "password": "nope"}
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["code"] == unknown.json()["code"] == "INVALID_CREDENTIALS"
    assert wrong.json()["detail"] == unknown.json()["detail"]


async def test_deactivated_user_cannot_log_in_or_refresh(client, sessionmaker, engine) -> None:
    await make_user(sessionmaker, email="gone@example.com", active=True)
    login = await client.post(
        "/api/v1/auth/login", json={"email": "gone@example.com", "password": PASSWORD}
    )
    assert login.status_code == 200
    async with sessionmaker() as s, s.begin():
        await s.execute(
            update(User).where(User.email == "gone@example.com").values(is_active=False)
        )

    again = await client.post(
        "/api/v1/auth/login", json={"email": "gone@example.com", "password": PASSWORD}
    )
    refresh = await client.post("/api/v1/auth/refresh")
    assert again.status_code == 401
    assert refresh.status_code == 401


async def test_protected_routes_reject_missing_and_bad_tokens(client) -> None:
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    r = await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer garbage"})
    assert r.status_code == 401 and r.json()["code"] == "INVALID_TOKEN"
    assert r.headers["www-authenticate"] == "Bearer"


# --------------------------------------------------------------------------- refresh tokens
async def _login(client, email: str) -> httpx.Response:
    return await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})


async def test_refresh_rotates_the_token_and_issues_a_new_access_token(
    client, sessionmaker
) -> None:
    await make_user(sessionmaker, email="rot@example.com")
    await _login(client, "rot@example.com")
    old_cookie = client.cookies.get("refresh_token")

    second = await client.post("/api/v1/auth/refresh")

    assert second.status_code == 200
    assert second.json()["access_token"]
    assert client.cookies.get("refresh_token") != old_cookie


async def test_reusing_a_rotated_token_revokes_the_whole_family(client, sessionmaker) -> None:
    await make_user(sessionmaker, email="thief@example.com")
    await _login(client, "thief@example.com")
    stolen = client.cookies.get("refresh_token")
    await client.post("/api/v1/auth/refresh")  # legitimate rotation: `stolen` is now spent
    legit_new = client.cookies.get("refresh_token")

    # Make the replay fall outside the double-tab grace window.
    async with sessionmaker() as s, s.begin():
        await s.execute(
            update(RefreshToken)
            .where(RefreshToken.revoked_at.is_not(None))
            .values(revoked_at=datetime.now(UTC) - timedelta(minutes=5))
        )

    client.cookies.clear()
    client.cookies.set("refresh_token", stolen, path="/api/v1/auth")
    replay = await client.post("/api/v1/auth/refresh")
    assert replay.status_code == 401

    client.cookies.clear()
    client.cookies.set("refresh_token", legit_new, path="/api/v1/auth")
    victim = await client.post("/api/v1/auth/refresh")
    assert victim.status_code == 401, "the thief's replay must also kill the legitimate session"
    async with sessionmaker() as s:
        live = (
            (await s.execute(select(RefreshToken).where(RefreshToken.revoked_at.is_(None))))
            .scalars()
            .all()
        )
        assert live == []


async def test_near_simultaneous_double_refresh_is_not_treated_as_theft(
    client, sessionmaker
) -> None:
    """Two tabs refreshing together present the same cookie; the loser is refused, not punished."""
    await make_user(sessionmaker, email="tabs@example.com")
    await _login(client, "tabs@example.com")
    cookie = client.cookies.get("refresh_token")

    first = await client.post("/api/v1/auth/refresh")
    winner_cookie = client.cookies.get("refresh_token")
    client.cookies.set("refresh_token", cookie, path="/api/v1/auth")
    second = await client.post("/api/v1/auth/refresh")  # replay within the grace window

    assert first.status_code == 200 and second.status_code == 401
    client.cookies.set("refresh_token", winner_cookie, path="/api/v1/auth")
    assert (await client.post("/api/v1/auth/refresh")).status_code == 200, "family must survive"


async def test_logout_revokes_the_session(client, sessionmaker) -> None:
    await make_user(sessionmaker, email="bye@example.com")
    await _login(client, "bye@example.com")
    cookie = client.cookies.get("refresh_token")

    out = await client.post("/api/v1/auth/logout")
    assert out.status_code == 204

    client.cookies.set("refresh_token", cookie, path="/api/v1/auth")
    assert (await client.post("/api/v1/auth/refresh")).status_code == 401


async def test_refresh_without_cookie_is_unauthorised(client) -> None:
    r = await client.post("/api/v1/auth/refresh")
    assert r.status_code == 401 and r.json()["code"] == "INVALID_TOKEN"


async def test_expired_refresh_token_is_rejected(client, sessionmaker) -> None:
    await make_user(sessionmaker, email="old@example.com")
    await _login(client, "old@example.com")
    async with sessionmaker() as s, s.begin():
        await s.execute(
            update(RefreshToken).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    assert (await client.post("/api/v1/auth/refresh")).status_code == 401


# --------------------------------------------------------------------------- rate limiting
async def test_login_is_rate_limited_per_account(settings, clock, publisher, sessionmaker) -> None:
    from asgi_lifespan import LifespanManager

    from app.core.config import Settings
    from app.main import create_app

    limited = Settings(
        **{**settings.model_dump(), "rate_limit_enabled": True, "rate_limit_login_per_min": 3}
    )
    app = create_app(limited, clock=clock, publisher=publisher)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            codes = []
            for _ in range(5):
                r = await c.post(
                    "/api/v1/auth/login", json={"email": "victim@example.com", "password": "x"}
                )
                codes.append(r.status_code)
            assert codes == [401, 401, 401, 429, 429]
            assert r.json()["code"] == "RATE_LIMITED" and int(r.headers["retry-after"]) >= 1
