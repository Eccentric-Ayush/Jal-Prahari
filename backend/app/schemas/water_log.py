# backend/app/schemas/water_log.py
#
# Responsibility: Pydantic v2 models for water-log ingestion and history queries.
#
# ─── Schema hierarchy ─────────────────────────────────────────────────────────
#
#   WaterLogCreate          — POST /api/logs request body
#   WaterLogResponse        — single log record (used in lists and responses)
#   WaterLogCreateResponse  — POST /api/logs response envelope
#   HistoryResponse         — GET /api/sensors/{id}/history paginated response
#
# ─── No client-provided timestamp ────────────────────────────────────────────
# WaterLogCreate does NOT include a `timestamp` field.
# The database sets the timestamp via:
#     timestamp = Column(DateTime(timezone=True), server_default=func.now())
#
# Why server-side timestamp?
#   - IoT sensors may have drifted clocks (NTP failures, cold restarts)
#   - Client-provided timestamps require trust and clock-skew handling
#   - Server timestamp guarantees strictly monotonic ordering per DB insert
#   - Simpler API contract (clients don't need to know UTC format)
#
# ─── Water level bounds ───────────────────────────────────────────────────────
# ge=-500.0  →  sensor 500 cm above water (extreme dry condition)
# le=10_000.0 →  100 m water depth (safety upper cap, not a physical limit)
#
# These bounds are intentionally loose — flood conditions can produce
# unexpected spikes.  Tighter validation would cause valid readings to
# be rejected during actual flood events, which defeats the purpose.
#
# ─── Pagination fields in HistoryResponse ────────────────────────────────────
# total, page, page_size are included so frontend clients can:
#   1. Compute total_pages = ceil(total / page_size)
#   2. Display "Page X of Y" pagination controls
#   3. Detect when they've reached the last page (len(history) < page_size)
#
# This is the "envelope pagination" pattern — the most compatible approach
# for REST clients that don't support HTTP Link headers.

from datetime import datetime
from typing import List

from pydantic import BaseModel, ConfigDict, Field


# ─────────────────────────────────────────────────────────────────────────────
# Create (request)
# ─────────────────────────────────────────────────────────────────────────────

class WaterLogCreate(BaseModel):
    """
    Request body for POST /api/logs.

    Minimal schema — only the two fields needed for a valid reading.
    Sensor existence is validated in the service layer (FK check),
    not here, so the error message can reference the actual sensor_id.
    """

    sensor_id: int = Field(
        ...,
        ge=1,
        description=(
            "Primary key of the sensor that recorded this measurement. "
            "Must reference an existing row in the sensors table."
        ),
        examples=[1, 42],
    )
    water_level: float = Field(
        ...,
        ge=-500.0,
        le=10_000.0,
        description=(
            "Observed water depth in centimetres above the sensor reference datum. "
            "Negative values indicate the sensor is above the water surface (dry). "
            "Range: −500 cm (extreme dry) to 10 000 cm (100 m safety cap)."
        ),
        examples=[0.72, 12.5, 145.0, -3.2],
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "sensor_id": 1,
                "water_level": 0.72,
            }
        }
    )


# ─────────────────────────────────────────────────────────────────────────────
# Response (single record)
# ─────────────────────────────────────────────────────────────────────────────

class WaterLogResponse(BaseModel):
    """
    A single water-log record as returned by the API.

    Used in two contexts:
        1. Immediately after POST /api/logs (the newly created record)
        2. As list items within HistoryResponse

    from_attributes=True:
        Allows WaterLogResponse.model_validate(orm_water_log_instance).
        SQLAlchemy ORM instances expose columns as Python attributes, so
        Pydantic can read them directly without a manual .model_dump() step.

    timestamp:
        Set by the database server (not by the client).  Always UTC.
        Serialised as ISO-8601 string (e.g., "2024-06-15T10:30:00.123456Z").
    """

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": 1001,
                "sensor_id": 1,
                "water_level": 0.72,
                "timestamp": "2024-06-15T10:30:00.123456Z",
            }
        },
    )

    id:          int      = Field(..., description="Database-assigned primary key of this log record.")
    sensor_id:   int      = Field(..., description="ID of the sensor that produced this reading.")
    water_level: float    = Field(..., description="Water depth in centimetres.")
    timestamp:   datetime = Field(..., description="Server-assigned UTC timestamp of the measurement.")


# ─────────────────────────────────────────────────────────────────────────────
# Create response envelope
# ─────────────────────────────────────────────────────────────────────────────

class WaterLogCreateResponse(BaseModel):
    """
    Response envelope returned by POST /api/logs on success.

    Wraps the created record with a success flag and a human-readable message.
    This envelope pattern (vs. returning the bare WaterLogResponse) provides:
        - A consistent success indicator for scripted clients
        - A human-readable message for debugging dashboards
        - Extensibility: add `warnings`, `metadata`, etc. without breaking
          the existing response shape
    """

    success: bool = Field(
        default=True,
        description="Always True on HTTP 201 — included for scripted client clarity.",
    )
    message: str = Field(
        ...,
        description="Human-readable confirmation of the insert.",
        examples=["Water log recorded for sensor 1."],
    )
    log: WaterLogResponse = Field(
        ...,
        description="The newly created water log record, including DB-assigned id and timestamp.",
    )


# ─────────────────────────────────────────────────────────────────────────────
# History response (paginated)
# ─────────────────────────────────────────────────────────────────────────────

class HistoryResponse(BaseModel):
    """
    Paginated history response for GET /api/sensors/{id}/history.

    Pagination metadata:
        total      : total rows matching sensor_id (for computing page count)
        page       : current page (1-indexed)
        page_size  : records returned per page
        history    : the actual records for this page, newest-first

    Frontend page computation:
        total_pages = ceil(total / page_size)
        has_next    = page < total_pages
        has_prev    = page > 1

    Ordering:
        Records are ordered timestamp DESC (newest first).
        This matches dashboard use-cases where operators want to see
        the most recent flood levels at a glance.

    Example response:
        {
            "sensor_id": 1,
            "total": 10420,
            "page": 1,
            "page_size": 50,
            "history": [
                {"id": 10420, "sensor_id": 1, "water_level": 87.3, "timestamp": "..."},
                ...
            ]
        }
    """

    sensor_id:  int                   = Field(..., description="Sensor ID for this history query.")
    total:      int                   = Field(..., ge=0, description="Total log records for this sensor.")
    page:       int                   = Field(..., ge=1, description="Current page number (1-indexed).")
    page_size:  int                   = Field(..., ge=1, le=200, description="Records per page.")
    history:    List[WaterLogResponse] = Field(
        default_factory=list,
        description="Log records for this page, ordered timestamp DESC.",
    )
