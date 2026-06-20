# backend/app/schemas/telemetry_schema.py
#
# Responsibility: Pydantic v2 request and response models for the telemetry
# ingestion endpoint. These models are the single source of truth for:
#   - Input validation  (FastAPI auto-validates incoming JSON against these)
#   - OpenAPI docs      (FastAPI generates the spec from these)
#   - Response shaping  (model_dump() → dict → JSON)
#
# ─── Why Pydantic v2? ────────────────────────────────────────────────────────
# Pydantic v2 re-implements the validation core in Rust, yielding 5–50x
# faster validation than v1.  For a high-throughput ingestion endpoint
# receiving thousands of records per second, this is a meaningful gain.
#
# ─── Key v2 patterns used here ───────────────────────────────────────────────
# • model_config = ConfigDict(...)  — replaces inner `class Config:`
# • Field(ge=, le=, ...)            — replaces @validator for simple bounds
# • model_validate(raw_dict)        — replaces .parse_obj() / .from_orm()
# • model_dump()                    — replaces .dict()
#
# ─── Serialization bottleneck analysis ───────────────────────────────────────
# The main serialisation cost in a high-throughput API is:
#   1. JSON → Python dict          (handled by uvicorn/starlette's JSON parser)
#   2. Python dict → Pydantic model (model_validate — the "hot" step)
#   3. Pydantic model → plain dict  (model_dump — for DB insert)
#
# Pydantic v2 reduces step 2 by ~10–50x vs v1 because the critical path
# runs in compiled Rust rather than interpreted Python.  model_dump() in v2
# also skips Python-level attribute lookups, further reducing overhead.
#
# Additional optimisations applied here:
#   • ConfigDict(strict=False) — avoids extra type-coercion passes when the
#     payload already has the right types (most simulator payloads do).
#   • No nested models — keeping the schema flat avoids recursive traversal.
#   • Minimal Field definitions — only bounds checks, no custom validators.

from datetime import datetime, timezone
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


# ─────────────────────────────────────────────────────────────────────────────
# Request model: single reading
# ─────────────────────────────────────────────────────────────────────────────

class TelemetryReadingIn(BaseModel):
    """
    A single water-level reading emitted by one IoT sensor.

    Validation rules
    ────────────────
    sensor_id  : Positive integer, max 10 000 (scales to future expansion).
    water_level: Physically bounded.  −500 cm covers a fully dry sensor;
                 10 000 cm (100 m) is an extreme upper safety cap.
    timestamp  : ISO-8601 string is auto-parsed by Pydantic.
                 Defaults to the current UTC instant if the sender omits it,
                 so edge devices that don't have accurate clocks still produce
                 valid records (the DB server_default also sets a timestamp,
                 providing a second timestamp for skew detection).
    """

    model_config = ConfigDict(
        strict=False,           # coerce compatible types (e.g. str → datetime)
        populate_by_name=True,  # accept both alias and field name
    )

    sensor_id: int = Field(
        ...,
        ge=1,
        le=10_000,
        description="Unique integer identifier of the IoT sensor (1–10 000).",
        examples=[1, 42, 500],
    )

    water_level: float = Field(
        ...,
        ge=-500.0,
        le=10_000.0,
        description=(
            "Observed water depth in centimetres above the sensor reference "
            "datum.  Negative values indicate the sensor is above the water "
            "surface (dry condition)."
        ),
        examples=[12.5, 0.0, -3.2, 87.4],
    )

    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="ISO-8601 UTC timestamp of the measurement.",
        examples=["2024-06-15T10:30:00Z", "2024-06-15T10:30:00.123456+00:00"],
    )


# ─────────────────────────────────────────────────────────────────────────────
# Response model
# ─────────────────────────────────────────────────────────────────────────────

class IngestionResponse(BaseModel):
    """
    Response envelope returned by POST /api/v1/telemetry.

    Fields
    ──────
    accepted   : Records successfully written to water_logs.
    rejected   : Records that failed DB-level constraints (FK mismatch, etc.).
                 Note: Pydantic validation failures never reach this field —
                 they are caught before the DB call and return 422.
    batch_size : Total records in the received batch.
    latency_ms : End-to-end handler latency in milliseconds, including
                 JSON parsing, Pydantic validation, and DB insert.
    message    : Human-readable summary for debugging and dashboards.
    """

    model_config = ConfigDict(strict=False)

    accepted:   int   = Field(..., ge=0, description="Records inserted into water_logs.")
    rejected:   int   = Field(..., ge=0, description="Records that failed DB insertion.")
    batch_size: int   = Field(..., ge=0, description="Total records received in this batch.")
    latency_ms: float = Field(..., ge=0.0, description="End-to-end handler latency (ms).")
    message:    str   = Field(..., description="Human-readable ingestion summary.")


# ─────────────────────────────────────────────────────────────────────────────
# Health check response
# ─────────────────────────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    """Response body for the GET /health liveness probe."""

    status:       str  = Field(..., examples=["ok"])
    timestamp:    str  = Field(..., description="UTC ISO-8601 server time.")
    version:      str  = Field(..., examples=["1.0.0"])
    db_connected: bool = Field(..., description="True if the DB connection pool is healthy.")


# ─────────────────────────────────────────────────────────────────────────────
# Structured error body
# ─────────────────────────────────────────────────────────────────────────────

class ErrorDetail(BaseModel):
    """
    Structured error body for 4xx / 5xx responses.

    Used in the OpenAPI spec so clients know exactly what to expect when
    the endpoint returns an error, enabling typed error handling.
    """

    error:       str = Field(..., description="Short error code or name.")
    detail:      str = Field(..., description="Human-readable explanation.")
    status_code: int = Field(..., ge=400, le=599, description="HTTP status code.")
