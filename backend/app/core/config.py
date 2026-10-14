"""Application settings, loaded from environment variables (12-factor)."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_INSECURE_DEFAULT_SECRET = "dev-only-insecure-secret-change-me-please-0123456789"  # noqa: S105


def normalize_database_url(url: str) -> str:
    """Accept the URL shapes managed hosts hand out and return an asyncpg SQLAlchemy URL.

    Render/Heroku-style ``postgres://`` and plain ``postgresql://`` are rewritten, and the
    libpq-only ``sslmode`` query parameter is translated to asyncpg's ``ssl``.
    """
    if url.startswith("postgres://"):
        url = "postgresql://" + url.removeprefix("postgres://")
    if url.startswith("postgresql://"):
        url = "postgresql+asyncpg://" + url.removeprefix("postgresql://")
    return url.replace("sslmode=", "ssl=")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # --- runtime -----------------------------------------------------------------
    env: Literal["local", "test", "production"] = "local"
    app_version: str = "1.0.0"
    log_level: str = "INFO"
    log_json: bool | None = None  # default: JSON everywhere except `local`

    # --- infrastructure ----------------------------------------------------------
    database_url: str = "postgresql+asyncpg://auction:auction@127.0.0.1:5433/auction"
    db_pool_size: int = 10
    db_max_overflow: int = 10
    db_read_pool_size: int = 10  # separate pool for read-only endpoints (see db/session.py)
    db_read_max_overflow: int = 10
    # Read pool behaviour (defaults are the fast path; see db/session.py). Operators who prefer
    # connection health checks over fewer round trips can flip these.
    db_read_autocommit: bool = True
    db_read_pre_ping: bool = False
    redis_url: str = "redis://127.0.0.1:6380/0"

    # --- security ----------------------------------------------------------------
    jwt_secret: str = _INSECURE_DEFAULT_SECRET
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 7
    refresh_cookie_name: str = "refresh_token"
    cookie_secure: bool = False
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    ws_allowed_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    max_request_body_bytes: int = 262_144  # 256 KiB: every legitimate body here is tiny
    metrics_token: str | None = None  # if set, /metrics requires `Authorization: Bearer <token>`
    enable_hsts: bool = False

    # --- rate limiting (fixed window, per key) -------------------------------------
    rate_limit_enabled: bool = True
    rate_limit_login_per_min: int = 10
    rate_limit_register_per_min: int = 5
    rate_limit_refresh_per_min: int = 60
    rate_limit_bids_per_10s: int = 20

    # --- auctions ----------------------------------------------------------------
    # Amounts are integer paise (1 INR = 100 paise). 1 crore INR = 10^9 paise.
    max_bid_amount: int = 1_000_000_000
    default_snipe_window_s: int = 120
    default_snipe_extension_s: int = 120
    default_max_extension_s: int = 1800

    # --- scheduler ---------------------------------------------------------------
    run_scheduler: bool = True
    scheduler_interval_s: float = 1.0
    scheduler_batch_size: int = 50

    # --- demo ---------------------------------------------------------------------
    # Keeps a few auctions live/upcoming so reviewers always find something to bid on.
    demo_mode: bool = False
    demo_min_open_auctions: int = 4

    # --- realtime ----------------------------------------------------------------
    ws_max_connections_per_user: int = 10
    ws_max_subscriptions: int = 50
    ws_send_queue_size: int = 100
    ws_messages_per_second: float = 20.0
    ws_burst: int = 40

    # --- observability -----------------------------------------------------------
    sentry_dsn: str | None = None

    @model_validator(mode="after")
    def _finalize(self) -> "Settings":
        self.database_url = normalize_database_url(self.database_url)
        if self.log_json is None:
            self.log_json = self.env != "local"
        if self.env == "production":
            if self.jwt_secret == _INSECURE_DEFAULT_SECRET or len(self.jwt_secret) < 32:
                raise ValueError(
                    "JWT_SECRET must be set to a random value (>=32 chars) in production"
                )
            if not self.cookie_secure:
                raise ValueError("COOKIE_SECURE must be true in production")
        if self.cookie_samesite == "none" and not self.cookie_secure:
            raise ValueError("SameSite=None cookies require COOKIE_SECURE=true")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
