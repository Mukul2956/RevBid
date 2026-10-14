from fastapi import APIRouter, Cookie, Request, Response, status

from app.api.deps import CurrentUser, LimiterDep, SessionDep, SettingsDep, client_ip
from app.core.config import Settings
from app.domain.errors import InvalidToken, NotFound
from app.models.user import User
from app.schemas.auth import LoginIn, RegisterIn, TokenOut, UserOut
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])

_COOKIE_PATH = "/api/v1/auth"  # the refresh cookie is only ever sent to the auth endpoints


def _set_refresh_cookie(response: Response, settings: Settings, raw: str) -> None:
    response.set_cookie(
        settings.refresh_cookie_name,
        raw,
        max_age=settings.refresh_token_ttl_days * 86_400,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        path=_COOKIE_PATH,
    )


def _token_out(settings: Settings, user: User, access: str) -> TokenOut:
    return TokenOut(
        access_token=access,
        expires_in=settings.access_token_ttl_minutes * 60,
        user=UserOut.model_validate(user),
    )


@router.post("/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterIn,
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    limiter: LimiterDep,
) -> TokenOut:
    await limiter.hit(
        "register", client_ip(request), limit=settings.rate_limit_register_per_min, window_s=60
    )
    user = await auth_service.register(session, body)
    access, refresh = await auth_service.issue_tokens(session, settings, user)
    _set_refresh_cookie(response, settings, refresh)
    return _token_out(settings, user, access)


@router.post("/login", response_model=TokenOut)
async def login(
    body: LoginIn,
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    limiter: LimiterDep,
) -> TokenOut:
    # Two buckets: per source address (spraying) and per target account (credential stuffing).
    await limiter.hit(
        "login_ip", client_ip(request), limit=settings.rate_limit_login_per_min, window_s=60
    )
    await limiter.hit(
        "login_account",
        auth_service.normalize_email(body.email),
        limit=settings.rate_limit_login_per_min,
        window_s=60,
    )
    user = await auth_service.authenticate(session, body.email, body.password)
    access, refresh = await auth_service.issue_tokens(session, settings, user)
    _set_refresh_cookie(response, settings, refresh)
    return _token_out(settings, user, access)


@router.post("/refresh", response_model=TokenOut)
async def refresh(
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    limiter: LimiterDep,
    refresh_token: str | None = Cookie(default=None, alias="refresh_token"),
) -> TokenOut:
    await limiter.hit(
        "refresh", client_ip(request), limit=settings.rate_limit_refresh_per_min, window_s=60
    )
    if not refresh_token:
        raise InvalidToken("Missing refresh token")
    user, access, new_refresh = await auth_service.rotate_refresh(session, settings, refresh_token)
    _set_refresh_cookie(response, settings, new_refresh)
    return _token_out(settings, user, access)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    refresh_token: str | None = Cookie(default=None, alias="refresh_token"),
) -> Response:
    if refresh_token:
        await auth_service.revoke_refresh(session, refresh_token)
    response.delete_cookie(settings.refresh_cookie_name, path=_COOKIE_PATH)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=UserOut)
async def me(principal: CurrentUser, session: SessionDep) -> User:
    user = await session.get(User, principal.id)
    if user is None or not user.is_active:
        raise NotFound("User not found")
    return user
