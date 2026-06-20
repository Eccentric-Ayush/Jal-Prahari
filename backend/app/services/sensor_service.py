# backend/app/services/sensor_service.py
#
# Responsibility: All database operations and business logic for sensors.
#
# ─── Service layer benefits ───────────────────────────────────────────────────
# Separating service logic from route handlers provides:
#
#   1. Testability
#      Service functions accept an AsyncSession — easy to mock in unit tests
#      without an HTTP client or a real DB connection.
#
#   2. Reusability
#      get_sensor_by_id() is called from BOTH the sensors route AND the
#      water_log_service validator.  One implementation, two callers.
#
#   3. Single responsibility
#      Routes know ONLY about HTTP (status codes, request parsing).
#      Services know ONLY about business rules and database queries.
#
#   4. Future extension points
#      Adding a Redis cache layer: change get_all_sensors() — routes unchanged.
#      Adding Kafka publish: change create_sensor() — routes unchanged.
#      Adding audit logs: change any service write — routes unchanged.
#
# ─── SQLAlchemy 2.x async query pattern ──────────────────────────────────────
#
#   OLD (SQLAlchemy 1.x, sync):
#       session.query(Sensor).filter(Sensor.id == 1).first()
#
#   NEW (SQLAlchemy 2.x, async):
#       stmt   = select(Sensor).where(Sensor.id == 1)
#       result = await session.execute(stmt)
#       sensor = result.scalar_one_or_none()
#
#   Key differences:
#     • select() is composable — add .where(), .order_by(), .limit() fluently
#     • await session.execute() returns a CursorResult, not an ORM object
#     • .scalars().all() extracts a list of ORM objects from the result
#     • .scalar_one_or_none() safely returns one object or None
#
# ─── Geometry extraction ──────────────────────────────────────────────────────
# PostGIS stores POINT geometry as binary WKB (Well-Known Binary).
# The `geometry` column on the Sensor ORM model holds a GeoAlchemy2 element.
#
# Two extraction options:
#   Option A — to_shape() (GeoAlchemy2 → Shapely):
#       from geoalchemy2.shape import to_shape
#       point = to_shape(sensor.geometry)
#       lat, lon = point.y, point.x
#
#   Option B — ST_AsText() SQL function (PostGIS → WKT string):
#       func.ST_AsText(Sensor.geometry)  → "POINT(72.877 19.076)"
#
# We use Option A (to_shape) because:
#   - Avoids an extra SQL function call per row
#   - Shapely objects are composable (future: distance, bounding-box queries)
#   - Works on any GeoAlchemy2 geometry type without SQL changes

from typing import List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import get_logger
from app.database.models import Sensor
from app.schemas.sensor import SensorCreate

logger = get_logger(__name__, log_file="backend.log")


# (Helpers removed: extraction done in SQL via ST_X/ST_Y)


# ─────────────────────────────────────────────────────────────────────────────
# Read operations
# ─────────────────────────────────────────────────────────────────────────────

async def get_all_sensors(
    session: AsyncSession,
) -> Tuple[List[dict], int]:
    """
    Retrieve all registered sensors with lat/lon extracted via PostGIS
    ST_X()/ST_Y() functions — done in SQL, not Python, for async-driver safety.
    """
    count_stmt = select(func.count()).select_from(Sensor)
    total: int = (await session.execute(count_stmt)).scalar_one()

    if total == 0:
        logger.info("Sensor table is empty — returning empty list.")
        return [], 0

    data_stmt = select(
        Sensor.id,
        Sensor.name,
        Sensor.base_elevation,
        Sensor.created_at,
        func.ST_Y(Sensor.geometry).label("latitude"),
        func.ST_X(Sensor.geometry).label("longitude"),
    ).order_by(Sensor.id)

    result = await session.execute(data_stmt)
    rows = result.mappings().all()  # returns list of dict-like rows

    sensors = [dict(row) for row in rows]

    logger.info("Fetched %d sensors from database.", total)
    return sensors, total


async def get_sensor_by_id(
    session: AsyncSession,
    sensor_id: int,
) -> Optional[Sensor]:
    """
    Fetch a single sensor by primary key.

    Used by:
        • GET /api/sensors/{id}          (future endpoint)
        • water_log_service.create_water_log() — FK existence check

    Args:
        session   : Active AsyncSession.
        sensor_id : Primary key to look up.

    Returns:
        Sensor ORM instance, or None if not found.
    """
    stmt   = select(Sensor).where(Sensor.id == sensor_id)
    result = await session.execute(stmt)
    sensor = result.scalar_one_or_none()

    if sensor is None:
        logger.warning("Sensor id=%d not found in database.", sensor_id)

    return sensor


# ─────────────────────────────────────────────────────────────────────────────
# Write operations
# ─────────────────────────────────────────────────────────────────────────────

async def create_sensor(
    session: AsyncSession,
    data: SensorCreate,
) -> Sensor:
    """
    Insert a new sensor row.

    Converts the client-provided (latitude, longitude) floats to the
    PostGIS WKT format:
        SRID=4326;POINT({longitude} {latitude})

    ⚠️ PostGIS POINT format is (longitude, latitude) — the reverse of
    the common geographic (latitude, longitude) convention.  This matches
    GeoJSON and the WGS84 standard for coordinate order.

    Args:
        session : Active AsyncSession.
        data    : Validated SensorCreate schema instance.

    Returns:
        Newly created Sensor ORM instance with DB-assigned id and created_at.
    """
    geometry_wkt = f"SRID=4326;POINT({data.longitude:.6f} {data.latitude:.6f})"

    sensor = Sensor(
        name=data.name,
        geometry=geometry_wkt,
        base_elevation=data.base_elevation,
    )
    session.add(sensor)
    await session.commit()
    await session.refresh(sensor)

    logger.info(
        "Sensor created: id=%d name=%s lat=%.6f lon=%.6f",
        sensor.id,
        sensor.name,
        data.latitude,
        data.longitude,
    )
    return sensor
