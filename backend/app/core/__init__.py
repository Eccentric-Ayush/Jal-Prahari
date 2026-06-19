# backend/app/core/__init__.py
#
# Marks `core` as a Python package and exposes its public API.
#
# Usage from other backend modules:
#
#   from app.core import load_dem, DEMParser, CoordinateOutOfBoundsError
#   from app.core import get_logger

from app.core.dem_parser import DEMParser, CoordinateOutOfBoundsError, load_dem
from app.core.logger import get_logger

__all__ = [
    "load_dem",
    "DEMParser",
    "CoordinateOutOfBoundsError",
    "get_logger",
]
