# backend/app/database/models.py
#
# Responsibility: Declare every ORM model (table) for the Jal-Prahari schema.
#
# Design principles applied here:
#   1. SQLAlchemy 2.x "mapped_column" API — the modern, type-safe alternative
#      to Column().  Enables Mypy / Pyright static type checking out of the box.
#   2. GeoAlchemy2 Geometry type — bridges SQLAlchemy and PostGIS.  Columns
#      with this type automatically participate in spatial index creation.
#   3. Explicit SRID 4326 — WGS84 geographic coordinates, the universal
#      standard for GPS / IoT sensor data.  See Best Practices section.
#   4. server_default for timestamps — the DB server sets the timestamp,
#      not the Python process.  Avoids clock-skew issues in distributed
#      deployments or when rows are inserted via raw SQL.
#   5. Relationship with cascade="all, delete-orphan" — ensures water_logs
#      rows are removed when a parent sensor is deleted, preserving referential
#      integrity without manual cleanup code.

import datetime
from typing import List, Optional

from geoalchemy2 import Geometry
from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)


# ---------------------------------------------------------------------------
# Declarative Base
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    """
    Project-wide declarative base class.

    All ORM models inherit from this class.  SQLAlchemy uses the class
    registry attached to Base to discover tables when create_all() is called.
    Keeping a single Base per project prevents "metadata mismatch" errors that
    can occur when multiple bases are accidentally created.
    """
    pass


# ---------------------------------------------------------------------------
# Model: Sensor
# ---------------------------------------------------------------------------

class Sensor(Base):
    """
    Represents a physical IoT water-level sensor deployed in the urban field.

    Each sensor has a fixed geographic location (geometry), an elevation value
    derived from the Digital Elevation Model (base_elevation), and an
    auto-generated creation timestamp.

    Spatial column notes
    --------------------
    geometry:
        - Type     : POINT  — a single (longitude, latitude) coordinate pair.
        - SRID     : 4326   — WGS84, the coordinate reference system used by
                             GPS receivers, Mapbox GL JS, and the EPSG standard.
        - Nullable : False  — every sensor *must* have a location.  A sensor
                             without coordinates is operationally meaningless.

    The GeoAlchemy2 Geometry column automatically triggers PostGIS to create a
    spatial (GiST) index via the `spatial_index=True` parameter, which enables
    efficient bounding-box and nearest-neighbour queries.
    """

    __tablename__ = "sensors"

    # -----------------------------------------------------------------------
    # Primary key
    # -----------------------------------------------------------------------
    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
        comment="Surrogate primary key — auto-incremented by the database.",
    )

    # -----------------------------------------------------------------------
    # Descriptive fields
    # -----------------------------------------------------------------------
    name: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        index=True,           # B-tree index for exact-name lookups and LIKE queries
        comment="Human-readable, unique identifier for the sensor "
                "(e.g. 'SENSOR_DHARAVI_01').",
    )

    # -----------------------------------------------------------------------
    # Spatial field
    # -----------------------------------------------------------------------
    geometry: Mapped[object] = mapped_column(
        Geometry(
            geometry_type="POINT",
            srid=4326,
            spatial_index=True,   # GeoAlchemy2 creates a GiST index automatically
        ),
        nullable=False,
        comment="WGS84 geographic point (longitude, latitude). "
                "Stored natively in PostGIS for sub-millisecond spatial queries.",
    )

    # -----------------------------------------------------------------------
    # Elevation
    # -----------------------------------------------------------------------
    base_elevation: Mapped[Optional[float]] = mapped_column(
        Float,
        nullable=True,       # Optional: may be populated later from DEM processing
        comment="Sensor elevation above mean sea level in metres, "
                "sourced from the Digital Elevation Model (DEM) pipeline.",
    )

    # -----------------------------------------------------------------------
    # Audit timestamp
    # -----------------------------------------------------------------------
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),   # database-side default, not Python datetime.now()
        nullable=False,
        comment="UTC timestamp of sensor registration. "
                "Set by the database server to avoid clock-skew.",
    )

    # -----------------------------------------------------------------------
    # Relationship
    # -----------------------------------------------------------------------
    water_logs: Mapped[List["WaterLog"]] = relationship(
        "WaterLog",
        back_populates="sensor",
        cascade="all, delete-orphan",
        # lazy="select" is the default — queries are issued when the attribute
        # is first accessed.  Switch to "selectin" or "joined" once the API
        # layer is built and query patterns are known.
        lazy="select",
    )

    # -----------------------------------------------------------------------
    # Composite table-level indexes (beyond the auto-created ones above)
    # -----------------------------------------------------------------------
    __table_args__ = (
        # Covering index that accelerates geospatial queries filtered by name
        Index("ix_sensors_name_geometry", "name", "base_elevation"),
        {"comment": "Physical IoT sensors deployed in the urban water-logging monitoring network."},
    )

    def __repr__(self) -> str:
        return (
            f"<Sensor id={self.id!r} name={self.name!r} "
            f"elevation={self.base_elevation!r}m>"
        )


# ---------------------------------------------------------------------------
# Model: WaterLog
# ---------------------------------------------------------------------------

class WaterLog(Base):
    """
    Time-series record of water depth measurements from a single sensor.

    Each row captures one reading from one sensor at one point in time.  The
    table is intentionally narrow (high write throughput, simple schema) to
    support future partitioning by timestamp — a standard pattern for IoT
    time-series data in PostgreSQL.

    Index strategy
    --------------
    - sensor_id   : Foreign key index — essential for the most common query
                   pattern: "give me all readings for sensor X".
    - timestamp   : B-tree index — enables efficient range queries like
                   "readings from the last 24 hours".
    - Composite (sensor_id, timestamp) : Covers the query "readings for sensor
                   X ordered by time", which is the backbone of every chart
                   and alert in the dashboard.
    """

    __tablename__ = "water_logs"

    # -----------------------------------------------------------------------
    # Primary key — BigInteger future-proofs against high-frequency insertion
    # -----------------------------------------------------------------------
    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
        comment="Surrogate primary key — BigInteger to handle millions of IoT readings.",
    )

    # -----------------------------------------------------------------------
    # Foreign key
    # -----------------------------------------------------------------------
    sensor_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(
            "sensors.id",
            ondelete="CASCADE",   # DELETE parent sensor → DELETE all its logs
            onupdate="CASCADE",   # UPDATE parent id   → UPDATE all FK values
        ),
        nullable=False,
        comment="References the sensor that produced this reading.",
    )

    # -----------------------------------------------------------------------
    # Measurement
    # -----------------------------------------------------------------------
    water_level: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        comment="Observed water depth in centimetres above the sensor's "
                "reference datum.  Negative values indicate a dry sensor.",
    )

    # -----------------------------------------------------------------------
    # Timestamp — server-side, timezone-aware
    # -----------------------------------------------------------------------
    timestamp: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="UTC timestamp when the reading was recorded by the database. "
                "For edge-device time use a separate 'device_timestamp' column.",
    )

    # -----------------------------------------------------------------------
    # Relationship (back-reference)
    # -----------------------------------------------------------------------
    sensor: Mapped["Sensor"] = relationship(
        "Sensor",
        back_populates="water_logs",
    )

    # -----------------------------------------------------------------------
    # Composite and individual indexes
    # -----------------------------------------------------------------------
    __table_args__ = (
        # ① Index on sensor_id alone — fastest path for FK lookup
        Index("ix_water_logs_sensor_id", "sensor_id"),

        # ② Index on timestamp alone — fast time-range scans across all sensors
        Index("ix_water_logs_timestamp", "timestamp"),

        # ③ Composite index: sensor_id + timestamp DESC — the critical index for
        #    the query pattern "latest N readings for sensor X", used by every
        #    real-time chart and alerting rule.
        Index(
            "ix_water_logs_sensor_id_timestamp",
            "sensor_id",
            text("timestamp DESC"),  # explicit DESC ordering to match ORDER BY
        ),

        {"comment": "Append-only time-series table storing water depth readings "
                    "from deployed IoT sensors. Candidate for time-based partitioning."},
    )

    def __repr__(self) -> str:
        return (
            f"<WaterLog id={self.id!r} sensor_id={self.sensor_id!r} "
            f"level={self.water_level!r}cm @ {self.timestamp!r}>"
        )
