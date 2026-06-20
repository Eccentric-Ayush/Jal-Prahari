# backend/app/schemas/__init__.py
#
# Marks the `schemas` directory as a Python package and re-exports the
# public API so consumers can import cleanly:
#
#   from app.schemas import TelemetryReadingIn, SensorResponse, HistoryResponse

# ── Telemetry ingestion schemas (existing, high-throughput endpoint) ──────────
from app.schemas.telemetry_schema import (
    TelemetryReadingIn,
    IngestionResponse,
    HealthResponse,
    ErrorDetail,
)

# ── Sensor CRUD schemas (new, async CRUD layer) ───────────────────────────────
from app.schemas.sensor import (
    SensorBase,
    SensorCreate,
    SensorResponse,
    SensorListResponse,
)

# ── Water log CRUD schemas (new, async CRUD layer) ────────────────────────────
from app.schemas.water_log import (
    WaterLogCreate,
    WaterLogResponse,
    WaterLogCreateResponse,
    HistoryResponse,
)

__all__ = [
    # Telemetry
    "TelemetryReadingIn",
    "IngestionResponse",
    "HealthResponse",
    "ErrorDetail",
    # Sensor
    "SensorBase",
    "SensorCreate",
    "SensorResponse",
    "SensorListResponse",
    # Water log
    "WaterLogCreate",
    "WaterLogResponse",
    "WaterLogCreateResponse",
    "HistoryResponse",
]
