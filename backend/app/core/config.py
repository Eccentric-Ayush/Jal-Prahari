# backend/app/core/config.py
#
# Responsibility: Centralised, type-safe application configuration.
#
# ─── Why pydantic-settings? ──────────────────────────────────────────────────
# Raw os.getenv() approaches have three problems:
#   1. No type safety  — getenv() always returns str or None.
#      A missing POSTGRES_PORT reads fine but crashes later as a string concat.
#   2. No validation   — invalid values are silently accepted.
#   3. Scattered reads — env var names are duplicated across files,
#      making refactors error-prone.
#
# pydantic-settings solves all three:
#   1. Type coercion:  PORT=5432 in .env → settings.postgres_port: int = 5432
#   2. Validation:     Missing required fields fail at startup (fast-fail).
#   3. Single source:  One Settings class is the contract for all config.
#
# ─── lru_cache singleton ─────────────────────────────────────────────────────
# get_settings() is wrapped with @lru_cache(maxsize=1).
# The .env file is read and validated exactly ONCE per process lifetime.
# FastAPI's dependency injection calls get_settings() on every request
# that injects Depends(get_settings) — lru_cache makes this zero-cost.
#
# In tests: call get_settings.cache_clear() before patching env vars.
#
# ─── Dual database URLs ───────────────────────────────────────────────────────
# The project runs two SQLAlchemy engines:
#
#   sync_database_url  → postgresql+psycopg2://...
#       Used by: init_db.py, dem_parser.py, bulk_insert_service.py
#       Driver:  psycopg2 (sync, thread-safe, libpq-based)
#
#   async_database_url → postgresql+asyncpg://...
#       Used by: database/session.py → CRUD API routes
#       Driver:  asyncpg (async, no libpq, binary protocol, 3–5x faster reads)

from functools import lru_cache
from typing import Optional

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application-wide settings.

    Pydantic-settings reads fields from (in priority order):
        1. Environment variables (highest priority)
        2. .env file in the working directory
        3. Field default values (lowest priority)

    Field names map to env var names (case-insensitive):
        postgres_user  ←→  POSTGRES_USER
        log_level      ←→  LOG_LEVEL
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",          # silently ignore unknown env vars
        populate_by_name=True,
    )

    # ── PostgreSQL connection ─────────────────────────────────────────────────
    postgres_user:     str = Field("postgres",    description="PostgreSQL username.")
    postgres_password: str = Field("postgres",    description="PostgreSQL password.")
    postgres_host:     str = Field("localhost",   description="PostgreSQL host/IP.")
    postgres_port:     int = Field(5432,          description="PostgreSQL TCP port.")
    postgres_db:       str = Field("jal_prahari", description="Target database name.")

    # ── Application behaviour ─────────────────────────────────────────────────
    log_level:     str = Field("INFO",        description="Minimum logging level.")
    app_env:       str = Field("development", description="Runtime environment: development | production.")
    api_version:   str = Field("v1",          description="API version prefix for versioned routes.")
    batch_size:    int = Field(100,            description="Default bulk-insert batch size (rows per executemany).")

    # ── Pagination defaults ───────────────────────────────────────────────────
    default_page_size: int = Field(50,  description="Default records per page for history endpoints.")
    max_page_size:     int = Field(200, description="Hard cap on page_size to prevent runaway queries.")

    # ── Predictive Engine Heuristics (Provisional) ────────────────────────────
    # These thresholds and weights are unvalidated starting assumptions for the v1
    # rule-based prediction engine. They allow recalibration without redeploying.
    max_water_level_meters: float = Field(5.0, description="Theoretical maximum expected water level for normalization.")
    weight_water_level:     float = Field(0.4, description="Weight given to current water level (0.0 to 1.0).")
    weight_trend:           float = Field(0.3, description="Weight given to water-level growth trend (0.0 to 1.0).")
    weight_elevation:       float = Field(0.3, description="Weight given to elevation vulnerability (0.0 to 1.0).")
    min_elevation_meters:   float = Field(0.0, description="Dataset minimum elevation for normalization.")
    max_elevation_meters:   float = Field(100.0, description="Dataset maximum elevation for normalization.")
    dem_file_path:          str   = Field("data/mumbai_dem.tif", description="Path to the GeoTIFF DEM file for elevation lookups.")

    # ── Computed properties (derived, not read from env) ──────────────────────

    @computed_field
    @property
    def sync_database_url(self) -> str:
        """
        Synchronous psycopg2 connection URL.

        Used exclusively by legacy/blocking code:
            • database/connection.py  — existing sync engine
            • database/init_db.py     — one-time table creation
            • core/dem_parser.py      — DEM elevation lookup
            • services/bulk_insert_service.py — high-throughput ingestion
        """
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field
    @property
    def async_database_url(self) -> str:
        """
        Asynchronous asyncpg connection URL.

        Used exclusively by the new CRUD API layer:
            • database/session.py     — create_async_engine
            • dependencies/database.py — get_db() dependency
            • services/sensor_service.py + water_log_service.py

        asyncpg advantages over psycopg2:
            • Implements PostgreSQL binary wire protocol natively (no libpq)
            • True async I/O — no thread pool required for DB calls
            • 3–5x faster for read-heavy query patterns
            • Native support for PostgreSQL binary types (useful for PostGIS WKB)
        """
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field
    @property
    def is_development(self) -> bool:
        """True when APP_ENV=development — enables SQL echo logging."""
        return self.app_env.lower() == "development"

    @computed_field
    @property
    def is_production(self) -> bool:
        """True when APP_ENV=production — suppresses verbose logging."""
        return self.app_env.lower() == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return the application-wide Settings singleton.

    The first call reads from environment + .env file, validates all fields,
    and caches the result.  Every subsequent call returns the cached instance
    at near-zero cost.

    Usage in FastAPI routes (dependency injection):
        @router.get("/")
        async def endpoint(settings: Settings = Depends(get_settings)):
            ...

    Usage in non-route code (direct call):
        settings = get_settings()
        url = settings.async_database_url

    Test teardown:
        get_settings.cache_clear()  # force re-read after patching env vars
    """
    return Settings()
