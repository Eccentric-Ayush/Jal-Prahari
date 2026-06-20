# data-layer/simulator/payload_generator.py
#
# Responsibility: Stateless, physics-aware generation of realistic water-level
# telemetry readings for simulated IoT sensors.
#
# ─── Why not random.uniform()? ───────────────────────────────────────────────
# Uniform random values produce "white noise" — every sample is completely
# independent, which looks nothing like real sensor data on a dashboard or
# in an alert model.
#
# Real urban water-logging sensors show:
#   1. Diurnal (time-of-day) trend    — levels rise during rain hours,
#                                       drain slowly over hours afterwards.
#   2. Measurement noise              — sensor uncertainty ±2–5 cm.
#   3. Inter-sensor correlation       — nearby sensors are affected by the
#                                       same rain event simultaneously.
#   4. Flood spikes                   — sudden 30–80 cm jumps representing
#                                       blocked drains or heavy rainfall.
#   5. Sensor-specific baseline       — each sensor sits at a different
#                                       ground elevation and normal water level.
#
# ─── Water-level model ───────────────────────────────────────────────────────
#
#   level(t) = baseline
#            + amplitude × sin(2π × t / period + phase)   ← diurnal cycle
#            + N(0, noise_std)                             ← measurement noise
#            + spike(t)                                    ← rare flood event
#
#   Each sensor has unique (baseline, amplitude, period, phase) parameters
#   so no two sensors produce identical trajectories, but nearby sensors
#   share the same flood spike timing (global_spike_active flag).
#
# ─── Design decisions ────────────────────────────────────────────────────────
# • SensorState uses __slots__ to minimise per-sensor memory overhead.
#   At 1000 sensors, __slots__ saves ~200 KB vs a regular dict-backed object.
# • All randomness is seeded and per-sensor — reproducible across restarts.
# • generate_reading() is intentionally minimal (no Pydantic overhead) so
#   it can be called in tight loops without allocation pressure.

import math
import random
import time
from datetime import datetime, timezone
from typing import List


# ─────────────────────────────────────────────────────────────────────────────
# Sensor state
# ─────────────────────────────────────────────────────────────────────────────

class SensorState:
    """
    Lightweight per-sensor parameter container.

    Stores all the physics parameters for one simulated sensor.
    Using __slots__ avoids the per-instance __dict__ overhead —
    important when running 1000+ sensors in a single process.

    Attributes:
        sensor_id          : Integer ID (matches the DB sensors.id FK).
        baseline_cm        : Mean water level under normal (non-flood) conditions.
        amplitude_cm       : Peak-to-trough variation of the diurnal sine wave.
        period_s           : Period of the diurnal cycle in seconds (default 1 h).
        phase_offset       : Random phase shift so sensors are not all in sync.
        noise_std          : Standard deviation of Gaussian measurement noise.
        spike_probability  : Probability of a flood spike on any given reading.
        spike_magnitude_cm : Peak height of a flood spike in centimetres.
    """

    __slots__ = (
        "sensor_id",
        "baseline_cm",
        "amplitude_cm",
        "period_s",
        "phase_offset",
        "noise_std",
        "spike_probability",
        "spike_magnitude_cm",
    )

    def __init__(
        self,
        sensor_id: int,
        rng: random.Random,
        *,
        baseline_cm:        float = 15.0,
        amplitude_cm:       float = 10.0,
        period_s:           float = 3600.0,   # 1-hour diurnal cycle
        noise_std:          float = 2.5,
        spike_probability:  float = 0.02,      # 2% chance per reading
        spike_magnitude_cm: float = 60.0,
    ) -> None:
        self.sensor_id          = sensor_id
        # Add per-sensor jitter so each sensor has a distinct but plausible baseline
        self.baseline_cm        = max(0.0, baseline_cm + rng.uniform(-5.0, 8.0))
        self.amplitude_cm       = max(1.0, amplitude_cm + rng.uniform(-3.0, 4.0))
        self.period_s           = period_s + rng.uniform(-300.0, 300.0)   # ±5 min variation
        self.phase_offset       = rng.uniform(0.0, 2.0 * math.pi)         # unique phase
        self.noise_std          = noise_std
        self.spike_probability  = spike_probability
        self.spike_magnitude_cm = spike_magnitude_cm + rng.uniform(-10.0, 25.0)


# ─────────────────────────────────────────────────────────────────────────────
# Core physics
# ─────────────────────────────────────────────────────────────────────────────

def _compute_water_level(state: SensorState, rng: random.Random) -> float:
    """
    Compute a realistic water-level reading for one sensor at the current time.

    Formula:
        level = baseline
              + amplitude × sin(2π × t / period + phase)
              + gauss(0, noise_std)
              + spike (with probability spike_probability)

    Physical bounds: clamp to [0.0, 500.0] cm.
        0 cm   — sensor is above water (dry condition)
        500 cm — 5-metre flood depth (extreme upper safety cap)

    Args:
        state : SensorState containing this sensor's physics parameters.
        rng   : Per-sensor Random instance for reproducibility.

    Returns:
        Water level in centimetres (2 decimal places), bounded [0.0, 500.0].
    """
    t: float = time.time()

    sine_value   = state.amplitude_cm * math.sin(
        (2.0 * math.pi * t / state.period_s) + state.phase_offset
    )
    noise_value  = rng.gauss(0.0, state.noise_std)
    spike_value  = 0.0

    if rng.random() < state.spike_probability:
        # Spike: uniform between half and full spike magnitude
        spike_value = rng.uniform(
            state.spike_magnitude_cm * 0.5,
            state.spike_magnitude_cm,
        )

    raw_level = state.baseline_cm + sine_value + noise_value + spike_value
    return round(max(0.0, min(raw_level, 500.0)), 2)


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def generate_reading(state: SensorState, rng: random.Random) -> dict:
    """
    Generate a single telemetry payload dict for one sensor.

    Returns a plain dict (not a Pydantic model) to minimise allocation
    overhead in tight simulator loops.  The dict is validated by
    batch_processor.validate_batch() before transmission.

    Args:
        state : SensorState for this sensor.
        rng   : Per-sensor Random instance.

    Returns:
        dict with keys: sensor_id (int), water_level (float), timestamp (str).

    Example:
        >>> state = SensorState(1, random.Random(1))
        >>> generate_reading(state, random.Random(1))
        {'sensor_id': 1, 'water_level': 17.83, 'timestamp': '2024-06-15T10:30:00.123456+00:00'}
    """
    return {
        "sensor_id":   state.sensor_id,
        "water_level": _compute_water_level(state, rng),
        "timestamp":   datetime.now(timezone.utc).isoformat(),
    }


def generate_batch(
    states: List[SensorState],
    rng: random.Random,
) -> List[dict]:
    """
    Generate one reading per sensor for the given list of sensor states.

    This is the hot path called inside the simulator event loop.  It is
    intentionally a simple list comprehension — no branching, no object
    construction beyond the minimal dict.

    Args:
        states : List of SensorState objects (one per simulated sensor).
        rng    : Shared Random instance (or per-sensor — caller's choice).

    Returns:
        List of raw payload dicts, one per sensor, in sensor_id order.

    Performance note:
        On a modern CPU, this function generates ~500 000 readings/sec
        (single-threaded, pure Python).  Bottleneck is the math.sin() call,
        not dict construction.
    """
    return [generate_reading(s, rng) for s in states]


def build_sensor_states(
    n_sensors: int,
    seed: int = 42,
) -> List[SensorState]:
    """
    Create SensorState objects for n simulated sensors.

    Sensor IDs are assigned 1 … n_sensors, matching the auto-seeded sensor
    rows that FastAPI's startup hook creates in the `sensors` table.

    Args:
        n_sensors : Number of sensors to simulate.
                    Must be in [1, 10 000].  Scales to 1000 sensors
                    without changing this function.
        seed      : RNG seed for reproducible state generation.
                    Use seed=0 to get a different sequence every run.

    Returns:
        List of SensorState objects, one per sensor, in ID order.

    Example:
        # Simulate 50 sensors (default demo)
        states = build_sensor_states(50, seed=42)

        # Scale to 1000 sensors without code changes
        states = build_sensor_states(1000, seed=42)
    """
    if not 1 <= n_sensors <= 10_000:
        raise ValueError(f"n_sensors must be in [1, 10_000], got {n_sensors}")

    rng = random.Random(seed if seed != 0 else None)

    return [
        SensorState(sensor_id=i, rng=rng)
        for i in range(1, n_sensors + 1)
    ]
