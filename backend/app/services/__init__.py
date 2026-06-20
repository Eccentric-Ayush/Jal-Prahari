# backend/app/services/__init__.py
#
# Marks the `services` directory as a Python package.
# Service functions are imported directly in the modules that need them
# to keep the import graph explicit and shallow.
#
# Public exports listed here for IDE auto-complete and wildcard imports.

# ── High-throughput ingestion (sync + run_in_executor) ────────────────────────
from app.services.bulk_insert_service import bulk_insert_water_logs

# ── Sensor CRUD (async, asyncpg) ──────────────────────────────────────────────
from app.services.sensor_service import (
    get_all_sensors,
    get_sensor_by_id,
    create_sensor,
)

# ── Water log CRUD (async, asyncpg) ───────────────────────────────────────────
from app.services.water_log_service import (
    create_water_log,
    get_sensor_history,
)

__all__ = [
    # Ingestion
    "bulk_insert_water_logs",
    # Sensor
    "get_all_sensors",
    "get_sensor_by_id",
    "create_sensor",
    # Water log
    "create_water_log",
    "get_sensor_history",
]

