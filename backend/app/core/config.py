"""Application settings, loaded from environment / .env (pydantic-settings).

Field names map to UPPER_SNAKE environment variables (e.g. ``postgres_url``
reads ``POSTGRES_URL``). Every setting has a development-safe default so the
app can boot for local work without secrets; production must set them via
environment only. See the repo-root ``.env.example`` for the full set.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # .env may carry keys not consumed by the backend yet
        case_sensitive=False,
    )

    # ── Application ──────────────────────────────────────────────
    app_env: str = "development"  # development | staging | production
    log_level: str = "INFO"
    app_base_url: str = "http://localhost:8000"
    frontend_base_url: str = "http://localhost:3000"
    allowed_origins: str = "http://localhost:3000"  # comma-separated CORS origins

    # ── Database ─────────────────────────────────────────────────
    # Standard libpq URL; the async driver prefix is normalized in
    # `async_database_url`. Defaults to a local dev Postgres.
    postgres_url: str = "postgresql://postgres:postgres@localhost:5432/folexo"
    postgres_pool_size: int = 5
    postgres_max_overflow: int = 10

    # ── Supabase (Dashboard → Project Settings → API) ────────────
    supabase_url: str | None = None
    supabase_anon_key: str | None = None
    supabase_service_role_key: str | None = None
    supabase_jwt_secret: str | None = None

    # ── JWT (Supabase-issued tokens, ADR-004) ────────────────────
    jwt_algorithm: str = "HS256"  # HS256 (legacy) or RS256 (Supabase JWT signing keys)
    jwt_public_key: str | None = None  # PEM public key; required for RS256
    jwt_audience: str | None = "authenticated"  # None disables audience checks
    jwt_issuer: str | None = None  # defaults to {SUPABASE_URL}/auth/v1 when unset
    jwt_leeway_seconds: int = 10  # clock-skew tolerance on exp validation
    access_token_expire_minutes: int = 60  # for locally minted (test) tokens

    @property
    def effective_jwt_issuer(self) -> str | None:
        if self.jwt_issuer:
            return self.jwt_issuer
        if self.supabase_url:
            return f"{self.supabase_url.rstrip('/')}/auth/v1"
        return None

    @property
    def async_database_url(self) -> str:
        """POSTGRES_URL normalized for asyncpg (SQLAlchemy requires the driver)."""
        url = self.postgres_url
        if url.startswith("postgresql+asyncpg://"):
            return url
        for scheme in ("postgresql://", "postgres://"):
            if url.startswith(scheme):
                return url.replace(scheme, "postgresql+asyncpg://", 1)
        raise ValueError(
            "POSTGRES_URL must start with postgresql:// (or postgresql+asyncpg://)"
        )

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
