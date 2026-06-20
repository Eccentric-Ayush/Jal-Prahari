# data-layer/simulator/sensor_simulator.py
#
# Responsibility: Orchestrate N concurrent simulated IoT sensors, each
# periodically generating water-level readings and shipping them as HTTP POST
# batches to the FastAPI ingestion endpoint.
#
# ─── Concurrency model ───────────────────────────────────────────────────────
# The simulator uses asyncio + httpx.AsyncClient to fire many concurrent
# requests without spawning one OS thread per sensor.
#
# Why not threads?
#   1000 sensors × 1 thread = 1000 OS threads
#   Each thread consumes ~8 MB of stack → ~8 GB RAM just for stacks.
#   Thread context switching overhead grows quadratically with count.
#
# Why asyncio?
#   1000 sensors = 1000 lightweight coroutines, all sharing 1 OS thread.
#   Total overhead: ~1 KB per coroutine → ~1 MB for 1000 sensors.
#   Concurrency achieved via cooperative yielding at `await` points.
#
# Architecture diagram:
#
#   ┌─────────────────────────────────────────────────────────┐
#   │  asyncio event loop (single OS thread)                  │
#   │                                                         │
#   │  sensor_task(1) ──┐                                    │
#   │  sensor_task(2) ──┤──▶  httpx.AsyncClient ──▶  API    │
#   │  sensor_task(3) ──┤                                    │
#   │       ⋮          ──┘                                    │
#   │  metrics_task()   ← prints throughput every N seconds  │
#   └─────────────────────────────────────────────────────────┘
#
# ─── Each sensor_task does ───────────────────────────────────────────────────
#   loop:
#     1. Generate a reading via payload_generator.generate_reading()
#     2. Append to local buffer
#     3. When buffer reaches BATCH_SIZE: flush (POST) to API
#     4. Sleep interval_s seconds  ← yields control to event loop
#
# ─── Horizontal scalability ──────────────────────────────────────────────────
# Scaling from 50 → 1000 sensors requires only changing N_SENSORS.
# No code changes, no thread configuration — the asyncio model handles
# the concurrency scaling automatically.
#
# ─── Future Kafka / Redis integration ────────────────────────────────────────
# To integrate Kafka, replace the _flush_buffer() HTTP POST with:
#   await producer.send_and_wait(topic, json.dumps(batch).encode())
# The asyncio architecture is already compatible with aiokafka / aioredis.

import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

import httpx

# ── Path setup (enables `python -m simulator.sensor_simulator` from data-layer/)
_DATA_LAYER_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _DATA_LAYER_ROOT not in sys.path:
    sys.path.insert(0, _DATA_LAYER_ROOT)

from simulator.payload_generator import SensorState, build_sensor_states, generate_reading

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("jal_prahari.simulator")

# ─────────────────────────────────────────────────────────────────────────────
# Configuration — all overridable via environment variables
# ─────────────────────────────────────────────────────────────────────────────

ENDPOINT_URL:  str   = os.getenv("ENDPOINT_URL",  "http://localhost:8000/api/v1/telemetry")
N_SENSORS:     int   = int(os.getenv("N_SENSORS",  "50"))
INTERVAL_S:    float = float(os.getenv("INTERVAL_S", "1.0"))    # seconds between readings/sensor
BATCH_SIZE:    int   = int(os.getenv("SIM_BATCH_SIZE", "50"))   # readings per HTTP request
TIMEOUT_S:     float = float(os.getenv("TIMEOUT_S", "10.0"))
METRICS_EVERY: int   = int(os.getenv("METRICS_EVERY", "10"))    # print metrics every N seconds
RNG_SEED:      int   = int(os.getenv("RNG_SEED", "42"))


# ─────────────────────────────────────────────────────────────────────────────
# Metrics counters
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SimulatorMetrics:
    """
    Thread-safe (asyncio-safe) counters for the simulator.

    All mutations happen in the same event loop thread, so no locks are
    needed — asyncio is single-threaded cooperative concurrency.
    """
    total_sent:    int   = 0
    total_ok:      int   = 0
    total_failed:  int   = 0
    total_latency: float = 0.0     # cumulative ms across all successful requests
    window_sent:   int   = 0       # records sent in the current metrics window
    window_start:  float = field(default_factory=time.time)

    def record_success(self, n_records: int, latency_ms: float) -> None:
        self.total_sent    += n_records
        self.total_ok      += n_records
        self.total_latency += latency_ms
        self.window_sent   += n_records

    def record_failure(self, n_records: int) -> None:
        self.total_sent  += n_records
        self.total_failed += n_records
        self.window_sent += n_records

    def window_throughput(self) -> float:
        """Records per second in the current metrics window."""
        elapsed = time.time() - self.window_start
        return self.window_sent / elapsed if elapsed > 0 else 0.0

    def avg_latency_ms(self) -> float:
        """Average latency per successful request in milliseconds."""
        return self.total_latency / self.total_ok if self.total_ok > 0 else 0.0

    def success_rate(self) -> float:
        """Fraction of sent records that were acknowledged successfully."""
        return self.total_ok / self.total_sent if self.total_sent > 0 else 1.0

    def reset_window(self) -> None:
        self.window_sent  = 0
        self.window_start = time.time()


# ─────────────────────────────────────────────────────────────────────────────
# Core coroutines
# ─────────────────────────────────────────────────────────────────────────────

async def sensor_task(
    state:      SensorState,
    client:     httpx.AsyncClient,
    metrics:    SimulatorMetrics,
    interval_s: float,
    batch_size: int,
) -> None:
    """
    Coroutine simulating one IoT sensor.

    Runs indefinitely until cancelled (e.g., by KeyboardInterrupt → CancelledError).

    Each iteration:
        1. Generate a reading using the sensor's physics state.
        2. Append to a local in-memory buffer.
        3. Flush (HTTP POST) when the buffer reaches batch_size.
        4. Sleep interval_s seconds — yields control to the event loop so
           other sensor coroutines and the metrics task can run.

    Buffer flush strategy:
        Buffering BATCH_SIZE readings before flushing means each HTTP request
        carries multiple readings, improving throughput.  A sensor that sends
        one reading/sec with BATCH_SIZE=50 will POST every 50 seconds —
        each POST containing 50 readings, amortising connection overhead.

    Args:
        state      : Physics parameters for this sensor.
        client     : Shared async HTTP client (connection pooled).
        metrics    : Shared metrics counters.
        interval_s : Seconds to sleep between readings.
        batch_size : Readings to accumulate before flushing.
    """
    import random as _random
    rng    = _random.Random(state.sensor_id)   # reproducible per-sensor RNG
    buffer: List[dict] = []

    while True:
        try:
            reading = generate_reading(state, rng)
            buffer.append(reading)

            if len(buffer) >= batch_size:
                await _flush_buffer(client, buffer, metrics)
                buffer.clear()

        except asyncio.CancelledError:
            # Flush remaining buffer before exit
            if buffer:
                await _flush_buffer(client, buffer, metrics)
            raise

        await asyncio.sleep(interval_s)


async def _flush_buffer(
    client:   httpx.AsyncClient,
    readings: List[dict],
    metrics:  SimulatorMetrics,
) -> None:
    """
    POST a batch of readings to the ingestion endpoint.

    Uses json.dumps() for serialisation — faster than passing the `json=`
    kwarg to httpx (which also calls json.dumps() internally but adds overhead
    from type checking).  We set Content-Type manually.

    Handles connection errors gracefully: log and increment failure counter,
    then continue (do not crash the simulator on transient network issues).
    """
    t_start = time.perf_counter()
    try:
        response = await client.post(
            ENDPOINT_URL,
            content=json.dumps(readings, default=str),   # default=str handles datetime
            headers={"Content-Type": "application/json"},
        )
        latency_ms = (time.perf_counter() - t_start) * 1000

        if response.status_code in (200, 201):
            metrics.record_success(len(readings), latency_ms)
            logger.debug(
                "✔ POST → HTTP %d | %d records | %.1f ms",
                response.status_code,
                len(readings),
                latency_ms,
            )
        else:
            metrics.record_failure(len(readings))
            logger.warning(
                "✗ POST → HTTP %d | body=%s",
                response.status_code,
                response.text[:300],
            )

    except httpx.TimeoutException:
        metrics.record_failure(len(readings))
        logger.error("Request timed out after %.0f s for batch of %d records.", TIMEOUT_S, len(readings))

    except httpx.RequestError as exc:
        metrics.record_failure(len(readings))
        logger.error("Request error: %s", exc)


async def metrics_task(metrics: SimulatorMetrics, interval_s: int) -> None:
    """
    Periodically print a performance summary table to stdout.

    Runs as a separate coroutine alongside all sensor tasks.  Resets the
    per-window counter after each print so throughput reflects the most
    recent interval, not the cumulative total.
    """
    while True:
        await asyncio.sleep(interval_s)
        tput     = metrics.window_throughput()
        avg_lat  = metrics.avg_latency_ms()
        s_rate   = metrics.success_rate() * 100

        print(
            f"\n{'─' * 62}\n"
            f"  📊  Jal-Prahari Simulator — Performance Metrics\n"
            f"{'─' * 62}\n"
            f"  Timestamp      : {datetime.now(timezone.utc).isoformat()}\n"
            f"  Throughput     : {tput:>10.1f}  records / sec\n"
            f"  Total sent     : {metrics.total_sent:>10,d}  records\n"
            f"  Successful     : {metrics.total_ok:>10,d}  records\n"
            f"  Failed         : {metrics.total_failed:>10,d}  records\n"
            f"  Success rate   : {s_rate:>10.1f}  %\n"
            f"  Avg latency    : {avg_lat:>10.2f}  ms\n"
            f"{'─' * 62}\n",
            flush=True,
        )
        metrics.reset_window()


# ─────────────────────────────────────────────────────────────────────────────
# Main orchestrator
# ─────────────────────────────────────────────────────────────────────────────

async def run_simulator(
    n_sensors:  int   = N_SENSORS,
    endpoint:   str   = ENDPOINT_URL,
    interval_s: float = INTERVAL_S,
    batch_size: int   = BATCH_SIZE,
    seed:       int   = RNG_SEED,
) -> None:
    """
    Launch N sensor coroutines and the metrics printer, then run forever.

    Args:
        n_sensors  : Number of simulated sensors (50–1000 without code changes).
        endpoint   : Full URL of the FastAPI ingestion endpoint.
        interval_s : Seconds between readings per sensor.
        batch_size : Readings to accumulate before each HTTP flush.
        seed       : RNG seed for reproducible sensor state generation.
    """
    print(
        f"\n{'═' * 62}\n"
        f"  🌊  Jal-Prahari IoT Sensor Simulator\n"
        f"{'═' * 62}\n"
        f"  Sensors         : {n_sensors}\n"
        f"  Endpoint        : {endpoint}\n"
        f"  Interval        : {interval_s:.1f} s / sensor\n"
        f"  Batch size      : {batch_size} readings / request\n"
        f"  RNG seed        : {seed}\n"
        f"  Expected RPS    : ~{int(n_sensors / batch_size)} requests/sec\n"
        f"{'═' * 62}\n",
        flush=True,
    )

    states  = build_sensor_states(n_sensors, seed=seed)
    metrics = SimulatorMetrics(window_start=time.time())

    # httpx.Limits controls the connection pool inside AsyncClient.
    # max_connections=200 prevents exhausting the OS TCP socket limit.
    # max_keepalive_connections=50 reduces TCP handshake overhead by
    # reusing existing connections (HTTP/1.1 keep-alive).
    limits  = httpx.Limits(max_connections=200, max_keepalive_connections=50)
    timeout = httpx.Timeout(TIMEOUT_S)

    async with httpx.AsyncClient(base_url=endpoint, limits=limits, timeout=timeout) as client:
        # Override base_url — use the full ENDPOINT_URL directly in _flush_buffer
        client_full = httpx.AsyncClient(limits=limits, timeout=timeout)

        try:
            tasks: list[asyncio.Task] = [
                asyncio.create_task(
                    sensor_task(state, client_full, metrics, interval_s, batch_size),
                    name=f"sensor-{state.sensor_id:04d}",
                )
                for state in states
            ]
            tasks.append(
                asyncio.create_task(
                    metrics_task(metrics, METRICS_EVERY),
                    name="metrics-printer",
                )
            )

            logger.info("✔  %d sensor tasks launched. Press Ctrl+C to stop.", n_sensors)
            await asyncio.gather(*tasks)

        except (asyncio.CancelledError, KeyboardInterrupt):
            pass

        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await client_full.aclose()

            print(
                f"\n{'═' * 62}\n"
                f"  Simulator stopped.\n"
                f"  Final stats:\n"
                f"    Total sent  : {metrics.total_sent:,}\n"
                f"    Successful  : {metrics.total_ok:,}\n"
                f"    Failed      : {metrics.total_failed:,}\n"
                f"    Avg latency : {metrics.avg_latency_ms():.2f} ms\n"
                f"{'═' * 62}\n",
                flush=True,
            )


# ─────────────────────────────────────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Jal-Prahari IoT Sensor Simulator",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--sensors",  type=int,   default=N_SENSORS,    help="Number of sensors to simulate")
    parser.add_argument("--endpoint", type=str,   default=ENDPOINT_URL, help="Ingestion endpoint URL")
    parser.add_argument("--interval", type=float, default=INTERVAL_S,   help="Seconds between sensor readings")
    parser.add_argument("--batch",    type=int,   default=BATCH_SIZE,   help="Readings per HTTP request")
    parser.add_argument("--seed",     type=int,   default=RNG_SEED,     help="RNG seed (0=random each run)")
    args = parser.parse_args()

    try:
        asyncio.run(
            run_simulator(
                n_sensors  = args.sensors,
                endpoint   = args.endpoint,
                interval_s = args.interval,
                batch_size = args.batch,
                seed       = args.seed,
            )
        )
    except KeyboardInterrupt:
        print("\nSimulator interrupted. Goodbye.")
