# backend/app/schemas/sensor.py
#
# Responsibility: Pydantic v2 models for sensor-related API payloads.
#
# ─── Schema hierarchy ─────────────────────────────────────────────────────────
#
#   SensorBase               — shared fields (name, base_elevation)
#       ↳ SensorCreate       — input model  (adds latitude, longitude)
#       ↳ SensorResponse     — output model (adds id, created_at, ORM mode)
#
#   SensorListResponse       — wraps list[SensorResponse] with a total count
#
# ─── Why separate Create and Response models? ─────────────────────────────────
# SensorCreate:   what the client SENDS.  No id, no created_at (DB sets these).
# SensorResponse: what the client RECEIVES.  Includes DB-generated fields.
#
# Mixing them into one model causes issues:
#   - id: int is Optional in create but required in response → confusing validators
#   - The response serialiser would expose writable-only fields (bad API design)
#
# ─── lat/lon as separate floats, not WKT ─────────────────────────────────────
# PostGIS stores geometry as binary WKB, and WKT strings for input.
# Exposing raw WKT (SRID=4326;POINT(72.877 19.076)) to API consumers:
#   - Forces clients to understand PostGIS WKT format
#   - Breaks if the CRS changes
#   - Complicates Mapbox GL JS integration (needs floats anyway)
#
# Using separate latitude/longitude floats:
#   - Standard REST API convention
#   - Zero transformation needed by frontend Mapbox
#   - The service layer handles WKT conversion internally
#
# ─── from_attributes=True ────────────────────────────────────────────────────
# Pydantic v2's ORM mode (from_attributes=True) allows:
#   SensorResponse.model_validate(orm_sensor_instance)
# instead of:
#   SensorResponse(**{...dict manually built from orm_instance...})
#
# This works because Pydantic reads field values from object attributes,
# not from a dict.  SQLAlchemy ORM instances expose their columns as attributes.
# Note: geometry is NOT in SensorResponse — it's extracted to lat/lon floats
# by the service layer before reaching the schema.

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


# ─────────────────────────────────────────────────────────────────────────────
# Base
# ─────────────────────────────────────────────────────────────────────────────

class SensorBase(BaseModel):
    """
    Shared fields between SensorCreate and SensorResponse.

    Inheriting this base avoids duplicating field definitions and ensures
    that any validation change (e.g., tightening max_length) propagates
    to all derived schemas automatically.
    """

    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Human-readable sensor name or identifier.",
        examples=["SENSOR_001", "DHARAVI_FLOOD_01", "KURLA_WEST_03"],
    )
    base_elevation: Optional[float] = Field(
        default=None,
        ge=-100.0,
        le=9000.0,
        description=(
            "Sensor height above mean sea level in metres. "
            "Used by the flood-level calculator to convert raw water depth "
            "to absolute elevation.  Null if DEM lookup has not yet run."
        ),
        examples=[4.5, 12.3, 0.8],
    )


# ─────────────────────────────────────────────────────────────────────────────
# Create
# ─────────────────────────────────────────────────────────────────────────────

class SensorCreate(SensorBase):
    """
    Request body for POST /api/sensors (future endpoint).

    Clients provide latitude and longitude as separate float fields.
    The service layer converts these to PostGIS WKT:
        SRID=4326;POINT({longitude} {latitude})

    Field bounds enforce WGS84 geographic coordinate ranges.
    """

    latitude: float = Field(
        ...,
        ge=-90.0,
        le=90.0,
        description="WGS84 geographic latitude of the sensor (−90 to +90).",
        examples=[19.076, 18.959, 19.217],
    )
    longitude: float = Field(
        ...,
        ge=-180.0,
        le=180.0,
        description="WGS84 geographic longitude of the sensor (−180 to +180).",
        examples=[72.877, 72.819, 72.978],
    )


# ─────────────────────────────────────────────────────────────────────────────
# Response
# ─────────────────────────────────────────────────────────────────────────────

class SensorResponse(SensorBase):
    """
    Response payload for a single sensor record.

    Returned by:
        GET /api/sensors         — one item in the list
        GET /api/sensors/{id}    — single sensor lookup

    from_attributes=True:
        Enables SensorResponse.model_validate(orm_sensor_obj).
        Pydantic reads fields directly from SQLAlchemy ORM instance attributes.

    Note: latitude and longitude are extracted from the binary PostGIS geometry
    by the service layer before this schema is constructed.  The schema itself
    only handles plain Python floats.
    """

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
    )

    id: int = Field(
        ...,
        description="Database-assigned primary key.",
        examples=[1, 42, 500],
    )
    latitude: float = Field(
        ...,
        description="WGS84 latitude extracted from PostGIS geometry.",
        examples=[19.076],
    )
    longitude: float = Field(
        ...,
        description="WGS84 longitude extracted from PostGIS geometry.",
        examples=[72.877],
    )
    created_at: datetime = Field(
        ...,
        description="UTC timestamp when the sensor was registered.",
    )

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": 1,
                "name": "SENSOR_001",
                "latitude": 19.076,
                "longitude": 72.877,
                "base_elevation": 4.5,
                "created_at": "2024-06-15T10:30:00Z",
            }
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# List response
# ─────────────────────────────────────────────────────────────────────────────

class SensorListResponse(BaseModel):
    """
    Response envelope for GET /api/sensors.

    Wraps the sensor list with a total count field.

    Why include `total` at the top level?
        - Frontend can show "Showing X of Y sensors" without computing len()
        - When pagination is added, `total` drives the page count calculation
        - Consistent with REST API best practices for list endpoints

    Empty dataset:
        Returns {"total": 0, "sensors": []} — not 404.
        An empty list is a valid state (e.g., before demo sensor seeding).
        404 means "this resource does not exist" — the endpoint always exists.
    """

    total: int = Field(
        ...,
        ge=0,
        description="Total number of sensors currently registered.",
    )
    sensors: List[SensorResponse] = Field(
        default_factory=list,
        description="List of sensor records, ordered by sensor id.",
    )
