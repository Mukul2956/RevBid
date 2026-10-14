"""FastAPI dependencies: app singletons (from ``app.state``) and authentication."""

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.config import Settings
from app.core.metrics import RuntimeStats
from app.core.rate_limit import RateLimiter
from app.core.security import Principal, decode_access_token
from app.db.session import get_read_session, get_session
from app.domain.errors import Forbidden, Unauthenticated
from app.realtime.hub import Hub
from app.realtime.publisher import Publisher

_bearer = HTTPBearer(auto_error=False)


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


def get_clock(request: Request) -> Clock:
    return request.app.state.clock  # type: ignore[no-any-return]


def get_publisher(request: Request) -> Publisher:
    return request.app.state.publisher  # type: ignore[no-any-return]


def get_stats(request: Request) -> RuntimeStats:
    return request.app.state.stats  # type: ignore[no-any-return]


def get_limiter(request: Request) -> RateLimiter:
    return request.app.state.limiter  # type: ignore[no-any-return]


def get_hub(request: Request) -> Hub:
    return request.app.state.hub  # type: ignore[no-any-return]


def get_redis(request: Request) -> Redis:
    return request.app.state.redis  # type: ignore[no-any-return]


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
ClockDep = Annotated[Clock, Depends(get_clock)]
PublisherDep = Annotated[Publisher, Depends(get_publisher)]
StatsDep = Annotated[RuntimeStats, Depends(get_stats)]
LimiterDep = Annotated[RateLimiter, Depends(get_limiter)]
HubDep = Annotated[Hub, Depends(get_hub)]
RedisDep = Annotated[Redis, Depends(get_redis)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ReadSessionDep = Annotated[AsyncSession, Depends(get_read_session)]
Credentials = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]


async def get_optional_principal(creds: Credentials, settings: SettingsDep) -> Principal | None:
    if creds is None:
        return None
    return decode_access_token(settings, creds.credentials)


async def get_principal(creds: Credentials, settings: SettingsDep) -> Principal:
    if creds is None:
        raise Unauthenticated("Authentication required")
    return decode_access_token(settings, creds.credentials)


async def require_admin(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
    if not principal.is_admin:
        raise Forbidden("Administrator access required")
    return principal


OptionalPrincipal = Annotated[Principal | None, Depends(get_optional_principal)]
CurrentUser = Annotated[Principal, Depends(get_principal)]
AdminUser = Annotated[Principal, Depends(require_admin)]
