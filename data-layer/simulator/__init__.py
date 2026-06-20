# data-layer/simulator/__init__.py
#
# Marks the simulator package and exposes its public surface.
# External code should import through this __init__ rather than the
# submodules directly, insulating callers from internal restructuring.

from simulator.payload_generator import (
    SensorState,
    build_sensor_states,
    generate_reading,
    generate_batch,
)

__all__ = [
    "SensorState",
    "build_sensor_states",
    "generate_reading",
    "generate_batch",
]
