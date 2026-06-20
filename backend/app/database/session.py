# backend/app/database/session.py
#
# Responsibility: Create the async SQLAlchemy engine and session factory for
# the CRUD API layer.
#
# ─── Dual-engine architecture ─────────────────────────────────────────────────
#
#  connection.py  (THIS FILE IS UNTOUCHED)
#  ───────────────────────────────────────────────────────────────────────────
#  create_engine(postgresql+psycopg2://...)   ← sync, psycopg2
#  SessionFactory → used by init_db, dem_parser, bulk_insert_service
#
#  session.py  (THIS FILE — NEW)
#  ───────────────────────────────────────────────────────────────────────────
#  create_async_engine(postgresql+asyncpg://...) ← async, asyncpg
#  async_sessionmaker → used by CRUD routes: sensors, logs, history
#
# The two engines are completely independent.  Changing one does not affect
# the other.  This pattern allows an incremental migration from sync → async
# without a big-bang rewrite.
#
# ─── AsyncSession lifecycle explained ────────────────────────────────────────
#
#  Request arrives
#       ↓
#  get_db() creates AsyncSession from pool
#       ↓
#  Handler coroutine receives session via Depends(get_db)
#       ↓
#  await session.execute(select(...))   ← yields to event loop
#  Other coroutines run during DB I/O   ← asyncio concurrency
#       ↓
#  await session.commit() / rollback()  ← explicit transaction control
#       ↓
#  finally: await session.close()       ← returns connection to pool
#       ↓
#  Response sent
#
# ─── greenlet dependency ─────────────────────────────────────────────────────
# SQLAlchemy's async support uses greenlet internally to bridge the
# synchronous ORM internals (attribute access, relationship loading) with the
# asyncio event loop.  This is why `greenlet` must be installed even when
# using pure async code — it's an implementation detail of SQLAlchemy async.
#
# ─── Connection pool tuning ───────────────────────────────────────────────────
# For IoT-heavy workloads (continuous sensor reads + history queries):
#
#   pool_size = 5           — 5 persistent connections (reused across requests)
#   max_overflow = 10       — up to 15 total connections during burst traffic
#   pool_pre_ping = True    — sends a trivial query before reusing a connection
#                             to detect stale TCP connections (DB restart etc.)
#   pool_recycle = 1800     — close and reopen idle connections every 30 min
#                             (prevents PostgreSQL's idle_in_transaction timeout)
#
# Rule of thumb: pool_size ≈ uvicorn_workers × 3

from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings
from app.core.logger import get_logger

logger = get_logger(__name__, log_file="backend.log")


@lru_cache(maxsize=1)
def get_async_engine() -> AsyncEngine:
    """
    Return the singleton async SQLAlchemy engine (asyncpg driver).

    lru_cache ensures the engine is created exactly once per process.
    Creating an engine is expensive (pool allocation, SSL setup, etc.)
    and must not happen on every request.

    Returns:
        AsyncEngine connected to PostgreSQL via asyncpg.
    """
    settings = get_settings()

    engine = create_async_engine(
        settings.async_database_url,
        # ── Pool configuration ──────────────────────────────────────────────
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        pool_recycle=1800,
        pool_timeout=30,
        # ── Logging ─────────────────────────────────────────────────────────
        # echo=True logs every SQL statement — useful in dev, too noisy in prod.
        echo=settings.is_development,
        # ── asyncpg-specific connection args ─────────────────────────────────
        # These are passed through to asyncpg.connect().
        connect_args={
            "command_timeout": 60,          # statement timeout in seconds
            "server_settings": {
                "application_name": "jal_prahari_api",  # visible in pg_stat_activity
            },
        },
    )

    logger.info(
        "Async engine created | host=%s db=%s driver=asyncpg pool_size=%d",
        settings.postgres_host,
        settings.postgres_db,
        5,
    )
    return engine


def get_async_session_factory() -> async_sessionmaker[AsyncSession]:
    """
    Return a configured async_sessionmaker bound to the async engine.

    async_sessionmaker is the async equivalent of SQLAlchemy's sessionmaker.
    Calling the factory yields a new AsyncSession that draws a connection
    from the engine's pool.

    Configuration choices:
        expire_on_commit=False
            In async contexts, accessing an ORM attribute after commit() may
            raise DetachedInstanceError because the session is already closed
            and lazy-loading would require a new I/O operation on the event
            loop.  expire_on_commit=False keeps attribute values accessible
            in memory after commit, avoiding this footgun.

        autoflush=False
            Prevents SQLAlchemy from issuing unexpected SELECT queries mid-
            transaction (implicit flush before queries).  We flush explicitly
            via await session.flush() or await session.commit().

        autocommit=False
            Explicit transaction control.  Services call commit() and rollback()
            deliberately — never auto-commit.
    """
    return async_sessionmaker(
        bind=get_async_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )


async def close_async_engine() -> None:
    """
    Gracefully dispose the async engine connection pool.

    Called from main.py's lifespan shutdown hook to cleanly drain
    the pool and close all asyncpg connections before the process exits.
    Prevents "Event loop is closed" warnings from asyncpg background tasks.
    """
    engine = get_async_engine()
    await engine.dispose()
    logger.info("Async database engine disposed.")
