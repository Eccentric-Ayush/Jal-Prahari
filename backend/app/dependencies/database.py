# backend/app/dependencies/database.py
#
# Responsibility: FastAPI dependency that yields a scoped AsyncSession for
# each incoming request.
#
# ─── Why dependency injection for DB sessions? ───────────────────────────────
# Direct session creation inside route handlers has three problems:
#
#   1. No automatic cleanup — exceptions can leave sessions open.
#   2. No testability     — tests cannot swap the real DB for a mock DB.
#   3. No separation      — route code mixes infrastructure with logic.
#
# FastAPI's Depends(get_db) solves all three:
#   1. Generator + finally guarantees session.close() on every code path.
#   2. Tests override get_db via app.dependency_overrides[get_db] = mock_db.
#   3. Routes receive a clean session object — no knowledge of pool mechanics.
#
# ─── Generator dependency pattern ────────────────────────────────────────────
# FastAPI calls generator dependencies in three phases:
#
#   Phase 1 (before yield):  Create the AsyncSession.
#   Phase 2 (yield):         Pass the session to the route handler.
#   Phase 3 (after yield):   Run cleanup — even if the handler raised.
#
# This is equivalent to a try/finally block managed by the framework.
#
# ─── AsyncSession lifecycle ───────────────────────────────────────────────────
# Session lifecycle per request:
#
#   → Request arrives at POST /api/logs
#   → get_db() creates AsyncSession (draws connection from pool)
#   → Handler runs: await sensor_service.get_all_sensors(session)
#       - Event loop yields during DB I/O
#       - Other concurrent requests run during this window
#   → Handler calls await session.commit() (in service layer)
#   → Handler returns response
#   → finally: await session.close() returns connection to pool
#   → Response dispatched
#
# ─── IoT-heavy workload benefit ──────────────────────────────────────────────
# Jal-Prahari handles:
#   - Continuous sensor readings (POST /api/logs, high rate)
#   - Dashboard history queries (GET /api/sensors/{id}/history, concurrent)
#
# With AsyncSession:
#   - Each request gets its own session (no cross-request contamination)
#   - DB calls are non-blocking (event loop stays responsive)
#   - 100 concurrent history queries share a pool of 5 connections
#     because most time is spent waiting for DB I/O, not holding a connection
#
# Compare with sync Session:
#   - Each request BLOCKS the event loop for the full query duration
#   - 100 concurrent requests → 100 blocked threads or sequential queue
#
# ─── Transaction responsibility ───────────────────────────────────────────────
# get_db() does NOT commit — that is the service layer's responsibility.
# The dependency only ensures the session is properly closed (not committed).
# If a handler fails without committing, no partial data reaches the DB.

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import get_logger
from app.database.session import get_async_session_factory

logger = get_logger(__name__, log_file="backend.log")


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency: yield a scoped AsyncSession per request.

    Usage in any route handler:
        @router.get("/example")
        async def my_route(db: AsyncSession = Depends(get_db)):
            result = await db.execute(select(Sensor))
            return result.scalars().all()

    The session is automatically closed after the route handler returns,
    even if it raises an exception.  Rollback is performed on unhandled
    exceptions before closing, ensuring no dirty transaction lingers.

    Yields:
        AsyncSession — active, uncommitted session drawn from the pool.
    """
    factory = get_async_session_factory()

    async with factory() as session:
        try:
            yield session
        except Exception:
            # If the handler raised an unhandled exception, rollback any
            # pending transaction before closing.  This prevents partial
            # writes from being committed by accident.
            await session.rollback()
            logger.warning(
                "AsyncSession rolled back due to unhandled exception in handler."
            )
            raise
        # The `async with factory() as session:` context manager calls
        # session.close() automatically when the block exits — no explicit
        # close needed here.
