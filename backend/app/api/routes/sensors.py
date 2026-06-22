# backend/app/api/routes/sensors.py
#
# Responsibility: HTTP route handlers for sensor-related endpoints.
#
# ─── Route vs Service separation ─────────────────────────────────────────────
# Routes are THIN controllers.  Their only jobs are:
#   1. Parse and validate the HTTP request (FastAPI + Pydantic do this).
#   2. Call the appropriate service function with the validated data.
#   3. Catch service-layer exceptions and map them to HTTP status codes.
#   4. Serialise the service response to the declared response_model.
#
# Routes do NOT:
#   • Build SQL queries           (sensor_service.py responsibility)
#   • Enforce business rules      (sensor_service.py responsibility)
#   • Extract geometry from WKB   (sensor_service.py responsibility)
#
# This separation means:
#   - Adding a new endpoint with the same logic = new route, no service change
#   - Changing the DB query = service change, no route change
#   - Testing business logic = test services directly, no HTTP overhead
#
# ─── Error handling strategy ──────────────────────────────────────────────────
# All routes follow the same pattern:
#
#   try:
#       result = await service_function(db, ...)
#   except ValueError as exc:       ← business rule violation (e.g., not found)
#       raise HTTPException(404, ...)
#   except Exception as exc:        ← unexpected DB or runtime error
#       raise HTTPException(500, ...)
#
# ValueError is used as the service-to-route error contract.  It is
# intentionally not a custom exception class to keep the service layer
# free of HTTP dependencies.  If the service is ever used outside HTTP
# (e.g., CLI, Celery task), ValueError is still meaningful.
#
# ─── response_model ──────────────────────────────────────────────────────────
# Declaring response_model on every endpoint does three things:
#   1. Pydantic validates and serialises the return value.
#   2. FastAPI includes the response schema in the OpenAPI spec.
#   3. Extra fields from the ORM model are stripped (no data leakage).
#
# ─── Async + Depends(get_db) ─────────────────────────────────────────────────
# `async def` with `db: AsyncSession = Depends(get_db)`:
#   - FastAPI calls get_db() before the handler runs.
#   - The session is injected into the handler.
#   - FastAPI calls get_db()'s finally block after the handler returns.
#   - The handler itself is non-blocking: `await service_fn()` yields the
#     event loop to other coroutines during DB I/O.

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import get_logger
from app.dependencies.database import get_db
from app.schemas.sensor import SensorListResponse, SensorResponse
from app.services.sensor_service import get_all_sensors

logger = get_logger(__name__, log_file="backend.log")

router = APIRouter(tags=["Sensors"])


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/sensors
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/sensors",
    response_model=SensorListResponse,
    status_code=status.HTTP_200_OK,
    summary="List all registered sensors",
    description=(
        "Retrieve all IoT sensors registered in the system. "
        "Geographic coordinates are extracted from PostGIS geometry and returned "
        "as human-readable latitude/longitude floats.\n\n"
        "**Returns an empty list (not 404) when no sensors are registered.** "
        "This is the correct REST behaviour — the endpoint exists, it just has "
        "no data yet (e.g., before demo sensor seeding on startup)."
    ),
    responses={
        200: {
            "description": "Sensor list returned successfully.",
            "content": {
                "application/json": {
                    "example": {
                        "total": 2,
                        "sensors": [
                            {
                                "id": 1,
                                "name": "SENSOR_001",
                                "latitude": 19.076,
                                "longitude": 72.877,
                                "base_elevation": 4.5,
                                "created_at": "2024-06-15T10:30:00Z",
                            },
                            {
                                "id": 2,
                                "name": "SENSOR_002",
                                "latitude": 18.959,
                                "longitude": 72.819,
                                "base_elevation": 2.1,
                                "created_at": "2024-06-15T10:31:00Z",
                            },
                        ],
                    }
                }
            },
        },
        500: {"description": "Internal server error — database unavailable."},
    },
)
async def list_sensors(
    db: AsyncSession = Depends(get_db),
) -> SensorListResponse:
    """
    GET /api/sensors

    Returns all sensors with their coordinates and metadata.

    Async flow:
        1. Depends(get_db) provides an AsyncSession (connection from pool).
        2. get_all_sensors(db) issues two async queries:
               COUNT(*)   → total sensor count
               SELECT ... → specific columns including ST_X/ST_Y
        3. SensorResponse.model_validate(dict) produces the Pydantic model
           directly from the returned dictionary.
        4. FastAPI serialises SensorListResponse to JSON.

    Empty dataset:
        Returns {"total": 0, "sensors": []} with HTTP 200.
        Not 404 — the resource (the sensors list) always exists.
    """
    try:
        sensors, total = await get_all_sensors(db)
    except Exception as exc:
        logger.error("GET /api/sensors failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error":  "Database query failed",
                "detail": str(exc),
            },
        )

    sensor_responses = [
        SensorResponse.model_validate(s) for s in sensors
    ]

    logger.info("GET /api/sensors -> %d sensors returned.", total)
    return SensorListResponse(total=total, sensors=sensor_responses)
