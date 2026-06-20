# backend/app/services/water_log_service.py
#
# Responsibility: Business logic for water-log creation and history retrieval.
#
# ─── FK validation strategy ───────────────────────────────────────────────────
# Before inserting a WaterLog, we check that sensor_id exists:
#
#   Option A — let the DB FK constraint catch it:
#       session.add(log) → await session.commit()
#       → psycopg2.errors.ForeignKeyViolation → SQLAlchemyError
#
#   Option B — explicit Python check before insert (chosen):
#       exists = await session.execute(select(func.count())...)
#       if not exists: raise ValueError(...)
#
# Why Option B?
#   1. Better error messages: "Sensor id=99 does not exist" vs a raw FK error
#   2. Cleaner HTTP mapping: ValueError → 404 (vs. mapping FK errors to status)
#   3. No rollback needed: we avoid opening a transaction that must be aborted
#   4. Testability: can be unit tested without triggering DB FK mechanics
#
# ─── Pagination strategy ─────────────────────────────────────────────────────
# Offset-based pagination (OFFSET / LIMIT):
#   page=1, page_size=50 → OFFSET 0   LIMIT 50
#   page=2, page_size=50 → OFFSET 50  LIMIT 50
#
# Chosen over cursor-based pagination because:
#   - Sensors have bounded history (data retention policies apply)
#   - Dashboard needs random page access (jump to page 5 of 20)
#   - Implementation is trivial — no cursor management or index hints needed
#
# Cursor-based pagination would be needed for:
#   - Real-time feeds (WebSocket streaming — future feature)
#   - Infinite scroll on a mobile app where page count is not shown
#
# ─── Ordering ────────────────────────────────────────────────────────────────
# History is returned ORDER BY timestamp DESC (newest first).
# Rationale:
#   - Operators monitoring flood alerts want the most recent levels first
#   - Chart libraries can reverse the order trivially on the frontend
#   - Consistent with dashboard UX patterns (most recent news/activity first)

from typing import List, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import get_logger
from app.database.models import Sensor, WaterLog
from app.schemas.water_log import WaterLogCreate

logger = get_logger(__name__, log_file="backend.log")


# ─────────────────────────────────────────────────────────────────────────────
# Write operations
# ─────────────────────────────────────────────────────────────────────────────

async def create_water_log(
    session: AsyncSession,
    data: WaterLogCreate,
) -> WaterLog:
    """
    Validate sensor existence, then insert a new water log record.

    Two-step process:
        Step 1 — COUNT check: Is sensor_id valid?
            Uses COUNT(*) instead of a full SELECT to minimise data transfer.
            A count of 0 means the sensor does not exist → raise ValueError.
        Step 2 — INSERT: Add the WaterLog row and commit.
            await session.refresh() ensures the ORM object reflects the
            DB-assigned id and server_default timestamp.

    Args:
        session : Active AsyncSession.
        data    : Validated WaterLogCreate schema instance.

    Returns:
        Newly created WaterLog ORM instance.

    Raises:
        ValueError : Sensor with the given id does not exist.
                     Mapped to HTTP 404 by the route handler.
    """
    # ── Step 1: Validate FK ────────────────────────────────────────────────
    count_stmt = (
        select(func.count())
        .select_from(Sensor)
        .where(Sensor.id == data.sensor_id)
    )
    sensor_count: int = (await session.execute(count_stmt)).scalar_one()

    if sensor_count == 0:
        logger.warning(
            "WaterLog insert rejected — sensor_id=%d does not exist.",
            data.sensor_id,
        )
        raise ValueError(f"Sensor with id={data.sensor_id} does not exist.")

    # ── Step 2: Insert ────────────────────────────────────────────────────
    log = WaterLog(
        sensor_id=data.sensor_id,
        water_level=data.water_level,
        # timestamp is set by DB server_default — do NOT pass it here
    )
    session.add(log)
    await session.commit()

    # refresh() re-queries the row so `log.id` and `log.timestamp` are populated
    await session.refresh(log)

    logger.info(
        "WaterLog created: id=%d sensor_id=%d water_level=%.4f timestamp=%s",
        log.id,
        log.sensor_id,
        log.water_level,
        log.timestamp,
    )
    return log


# ─────────────────────────────────────────────────────────────────────────────
# Read operations
# ─────────────────────────────────────────────────────────────────────────────

async def get_sensor_history(
    session: AsyncSession,
    sensor_id: int,
    page: int = 1,
    page_size: int = 50,
) -> Tuple[List[WaterLog], int]:
    """
    Return paginated water-log history for a sensor, ordered newest-first.

    Processing:
        1. Verify sensor exists → raise ValueError if not (→ HTTP 404).
        2. COUNT total matching rows → for pagination metadata.
        3. SELECT page of rows → OFFSET + LIMIT query.

    Args:
        session   : Active AsyncSession.
        sensor_id : Primary key of the target sensor.
        page      : Page number (1-indexed).
        page_size : Maximum records per page (capped at 200 by schema).

    Returns:
        Tuple: (list[WaterLog], total_row_count)
            list[WaterLog] : ORM instances for the current page.
            total_row_count: total matching rows for pagination metadata.

    Raises:
        ValueError : Sensor not found (→ HTTP 404 in the route).
    """
    # ── Step 1: Validate sensor exists ─────────────────────────────────────
    sensor_stmt = select(Sensor).where(Sensor.id == sensor_id)
    sensor      = (await session.execute(sensor_stmt)).scalar_one_or_none()

    if sensor is None:
        logger.warning(
            "History query rejected — sensor_id=%d not found.", sensor_id
        )
        raise ValueError(f"Sensor with id={sensor_id} does not exist.")

    # ── Step 2: Total count ────────────────────────────────────────────────
    count_stmt = (
        select(func.count())
        .select_from(WaterLog)
        .where(WaterLog.sensor_id == sensor_id)
    )
    total: int = (await session.execute(count_stmt)).scalar_one()

    if total == 0:
        logger.info(
            "No history records for sensor_id=%d — returning empty list.", sensor_id
        )
        return [], 0

    # ── Step 3: Paginated SELECT ───────────────────────────────────────────
    offset    = (page - 1) * page_size
    data_stmt = (
        select(WaterLog)
        .where(WaterLog.sensor_id == sensor_id)
        .order_by(WaterLog.timestamp.desc())    # newest first
        .offset(offset)
        .limit(page_size)
    )
    result = await session.execute(data_stmt)
    logs   = list(result.scalars().all())

    # Compute total pages for logging (ceiling division without math module)
    total_pages = -(-total // page_size)

    logger.info(
        "History: sensor_id=%d page=%d/%d records=%d total=%d",
        sensor_id,
        page,
        total_pages,
        len(logs),
        total,
    )
    return logs, total
