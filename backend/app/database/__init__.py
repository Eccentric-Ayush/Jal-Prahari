# backend/app/database/__init__.py
#
# This file marks the `database` directory as a Python package and exposes
# the public API of the module so that other parts of the backend can import
# cleanly, e.g.:
#
#   from app.database import Base, get_engine
#   from app.database import Sensor, WaterLog

from app.database.connection import get_engine, get_session_factory
from app.database.models import Base, Sensor, WaterLog

__all__ = [
    "Base",
    "Sensor",
    "WaterLog",
    "get_engine",
    "get_session_factory",
]
