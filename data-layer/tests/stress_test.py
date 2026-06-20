# data-layer/tests/stress_test.py
#
# Responsibility: Load-test the POST /api/v1/telemetry endpoint under
# configurable sensor counts (50 / 100 / 500) and print a detailed
# performance report.
#
# ─── What this tests ─────────────────────────────────────────────────────────
# • Throughput        : records inserted per second under load
# • Request latency   : p50 / p95 / p99 response times
# • Failure rate      : HTTP errors or timeouts under concurrent load
# • Scalability       : how metrics change as sensor count grows
#
# ─── How it works ────────────────────────────────────────────────────────────
# Each scenario (N sensors):
#   1. Build N SensorState objects
#   2. Generate REQUESTS_PER_SENSOR batches per sensor (each = RECORDS_PER_BATCH readings)
#   3. Fire all requests concurrently using asyncio.gather()
#   4. Measure wall-clock elapsed time and per-request latency
#   5. Compute and print a formatted performance table
#
# ─── Expected outputs ────────────────────────────────────────────────────────
# On a development machine (PostgreSQL local, FastAPI uvicorn single worker):
#
#   Scenario: 50 sensors
#   ┌─────────────────────────────────────────────────────────────┐
#   │  Sensors         : 50                                       │
#   │  Total requests  : 250                                      │
#   │  Total records   : 12 500                                   │
#   │  Elapsed         : 6.2 s                                    │
#   │  Throughput      : 2 016 records/sec                        │
#   │  Requests/sec    : 40.3                                     │
#   │  Latency p50     : 18.4 ms                                  │
#   │  Latency p95     : 42.1 ms                                  │
#   │  Latency p99     : 68.9 ms                                  │
#   │  Failed requests : 0                                        │
#   └─────────────────────────────────────────────────────────────┘
#
#   Scenario: 100 sensors
#   │  Throughput      : ~4 000 records/sec                       │
#   │  Latency p50     : ~22 ms                                   │
#
#   Scenario: 500 sensors
#   │  Throughput      : ~12 000 records/sec                      │
#   │  Latency p50     : ~35 ms                                   │
#
# ─── Run ─────────────────────────────────────────────────────────────────────
# Prerequisites:
#   1. docker-compose up -d postgis
#   2. uvicorn app.main:app --port 8000  (from backend/)
#
# Run from the project root:
#   python data-layer/tests/stress_test.py
#   python data-layer/tests/stress_test.py --sensors 50 100 500
#   python data-layer/tests/stress_test.py --endpoint http://staging:8000/api/v1/telemetry

import argparse
import asyncio
import json
import os
import random
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import httpx

# ── Path setup — allows running directly from the project root
_DATA_LAYER_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _DATA_LAYER_ROOT not in sys.path:
    sys.path.insert(0, _DATA_LAYER_ROOT)

from simulator.payload_generator import build_sensor_states, generate_batch, SensorState

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_ENDPOINT        = os.getenv("ENDPOINT_URL", "http://localhost:8000/api/v1/telemetry")
DEFAULT_RECORDS_PER_REQ = int(os.getenv("STRESS_RECORDS_PER_REQ", "50"))
DEFAULT_REQS_PER_SENSOR = int(os.getenv("STRESS_REQS_PER_SENSOR", "5"))
DEFAULT_TIMEOUT_S       = float(os.getenv("STRESS_TIMEOUT_S", "30.0"))
DEFAULT_SCENARIOS       = [50, 100, 500]


# ─────────────────────────────────────────────────────────────────────────────
# Result dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RequestResult:
    """Outcome of a single HTTP POST request."""
    success:    bool
    latency_ms: float
    n_records:  int
    status:     int  = 0
    error:      str  = ""


@dataclass
class ScenarioResult:
    """Aggregated results for one stress test scenario."""
    n_sensors:        int
    total_requests:   int
    successful:       int
    failed:           int
    total_records:    int
    elapsed_s:        float
    latencies_ms:     List[float] = field(default_factory=list)

    @property
    def records_per_sec(self) -> float:
        return self.total_records / self.elapsed_s if self.elapsed_s > 0 else 0.0

    @property
    def requests_per_sec(self) -> float:
        return self.successful / self.elapsed_s if self.elapsed_s > 0 else 0.0

    @property
    def p50(self) -> float:
        return _percentile(self.latencies_ms, 0.50) if self.latencies_ms else 0.0

    @property
    def p95(self) -> float:
        return _percentile(self.latencies_ms, 0.95) if self.latencies_ms else 0.0

    @property
    def p99(self) -> float:
        return _percentile(self.latencies_ms, 0.99) if self.latencies_ms else 0.0

    @property
    def avg_ms(self) -> float:
        return statistics.mean(self.latencies_ms) if self.latencies_ms else 0.0

    @property
    def success_rate_pct(self) -> float:
        return (self.successful / self.total_requests * 100) if self.total_requests > 0 else 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────────────────────────

def _percentile(data: List[float], p: float) -> float:
    """Compute the p-th percentile of a list of floats (0 ≤ p ≤ 1)."""
    if not data:
        return 0.0
    sorted_data = sorted(data)
    k           = (len(sorted_data) - 1) * p
    lo, hi      = int(k), min(int(k) + 1, len(sorted_data) - 1)
    return sorted_data[lo] + (sorted_data[hi] - sorted_data[lo]) * (k - lo)


# ─────────────────────────────────────────────────────────────────────────────
# Core HTTP request function
# ─────────────────────────────────────────────────────────────────────────────

async def send_batch(
    client:   httpx.AsyncClient,
    endpoint: str,
    batch:    List[dict],
) -> RequestResult:
    """
    Send one batch of readings to the ingestion endpoint.

    Returns a RequestResult regardless of success or failure so that
    asyncio.gather() does not abort on individual request errors.
    """
    t_start = time.perf_counter()
    try:
        response = await client.post(
            endpoint,
            content=json.dumps(batch, default=str),
            headers={"Content-Type": "application/json"},
        )
        latency_ms = (time.perf_counter() - t_start) * 1000
        success    = response.status_code in (200, 201)
        return RequestResult(
            success    = success,
            latency_ms = latency_ms,
            n_records  = len(batch),
            status     = response.status_code,
            error      = "" if success else response.text[:200],
        )

    except httpx.TimeoutException:
        latency_ms = (time.perf_counter() - t_start) * 1000
        return RequestResult(
            success    = False,
            latency_ms = latency_ms,
            n_records  = len(batch),
            status     = 0,
            error      = "TIMEOUT",
        )

    except httpx.RequestError as exc:
        latency_ms = (time.perf_counter() - t_start) * 1000
        return RequestResult(
            success    = False,
            latency_ms = latency_ms,
            n_records  = len(batch),
            status     = 0,
            error      = f"RequestError: {exc}",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Scenario runner
# ─────────────────────────────────────────────────────────────────────────────

async def run_scenario(
    n_sensors:          int,
    endpoint:           str,
    records_per_req:    int,
    reqs_per_sensor:    int,
    timeout_s:          float,
    rng_seed:           int = 42,
) -> ScenarioResult:
    """
    Execute one stress test scenario with n_sensors concurrent simulated sensors.

    Each sensor sends reqs_per_sensor batches of records_per_req readings.
    All requests are fired concurrently using asyncio.gather().

    Args:
        n_sensors       : Number of simulated sensors.
        endpoint        : Full ingestion endpoint URL.
        records_per_req : Readings per HTTP request.
        reqs_per_sensor : How many requests each sensor sends.
        timeout_s       : Per-request timeout in seconds.
        rng_seed        : RNG seed for reproducible payloads.

    Returns:
        ScenarioResult with aggregated metrics.
    """
    rng    = random.Random(rng_seed)
    states = build_sensor_states(n_sensors, seed=rng_seed)

    # Pre-generate all batches (avoids generation overhead during timing)
    all_batches: List[List[dict]] = []
    for state in states:
        for _ in range(reqs_per_sensor):
            # Generate records_per_req readings for this sensor
            batch = [
                {
                    "sensor_id":   state.sensor_id,
                    "water_level": round(max(0.0, min(rng.gauss(15.0, 8.0), 500.0)), 2),
                    "timestamp":   datetime.now(timezone.utc).isoformat(),
                }
                for _ in range(records_per_req)
            ]
            all_batches.append(batch)

    total_requests = len(all_batches)
    limits  = httpx.Limits(max_connections=min(total_requests, 500), max_keepalive_connections=100)
    timeout = httpx.Timeout(timeout_s)

    print(
        f"  → Sending {total_requests:,} concurrent requests "
        f"({records_per_req} records each) …",
        flush=True,
    )

    start_time = time.perf_counter()

    async with httpx.AsyncClient(limits=limits, timeout=timeout) as client:
        # Fire ALL requests simultaneously using asyncio.gather()
        # This maximises concurrency pressure on the server.
        results: List[RequestResult] = await asyncio.gather(
            *[send_batch(client, endpoint, batch) for batch in all_batches],
            return_exceptions=False,
        )

    elapsed_s = time.perf_counter() - start_time

    successful    = sum(1 for r in results if r.success)
    failed        = len(results) - successful
    total_records = sum(r.n_records for r in results if r.success)
    latencies     = [r.latency_ms for r in results if r.success]

    # Log failed requests for debugging
    for result in results:
        if not result.success:
            print(f"    ✗ HTTP {result.status}: {result.error}", flush=True)

    return ScenarioResult(
        n_sensors      = n_sensors,
        total_requests = total_requests,
        successful     = successful,
        failed         = failed,
        total_records  = total_records,
        elapsed_s      = elapsed_s,
        latencies_ms   = latencies,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Report printer
# ─────────────────────────────────────────────────────────────────────────────

def print_scenario_report(result: ScenarioResult) -> None:
    """Print a formatted performance report for one scenario."""
    border = "─" * 62
    print(f"\n  {border}")
    print(f"  📊  Stress Test Results — {result.n_sensors} Sensors")
    print(f"  {border}")
    print(f"  {'Sensors':<30}: {result.n_sensors:>10,}")
    print(f"  {'Total requests sent':<30}: {result.total_requests:>10,}")
    print(f"  {'Successful':<30}: {result.successful:>10,}")
    print(f"  {'Failed':<30}: {result.failed:>10,}")
    print(f"  {'Success rate':<30}: {result.success_rate_pct:>10.1f}  %")
    print(f"  {'Total records inserted':<30}: {result.total_records:>10,}")
    print(f"  {'Elapsed time':<30}: {result.elapsed_s:>10.2f}  s")
    print(f"  {'Throughput':<30}: {result.records_per_sec:>10.0f}  records/sec")
    print(f"  {'Requests/sec':<30}: {result.requests_per_sec:>10.1f}  req/sec")
    print(f"  {'Avg latency':<30}: {result.avg_ms:>10.2f}  ms")
    print(f"  {'p50 latency':<30}: {result.p50:>10.2f}  ms")
    print(f"  {'p95 latency':<30}: {result.p95:>10.2f}  ms")
    print(f"  {'p99 latency':<30}: {result.p99:>10.2f}  ms")
    print(f"  {border}")


def print_summary_table(results: List[ScenarioResult]) -> None:
    """Print a side-by-side comparison table of all scenarios."""
    print(f"\n\n  {'═' * 90}")
    print(f"  📈  Jal-Prahari — Stress Test Summary")
    print(f"  {'═' * 90}")
    header = f"  {'Sensors':>8}  {'Records/sec':>14}  {'Req/sec':>10}  {'p50 ms':>8}  {'p95 ms':>8}  {'p99 ms':>8}  {'Fail%':>6}"
    print(header)
    print(f"  {'─' * 88}")
    for r in results:
        fail_pct = (r.failed / r.total_requests * 100) if r.total_requests > 0 else 0.0
        print(
            f"  {r.n_sensors:>8}  "
            f"{r.records_per_sec:>14,.0f}  "
            f"{r.requests_per_sec:>10.1f}  "
            f"{r.p50:>8.1f}  "
            f"{r.p95:>8.1f}  "
            f"{r.p99:>8.1f}  "
            f"{fail_pct:>6.1f}"
        )
    print(f"  {'═' * 90}\n")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

async def main(
    scenarios:       List[int],
    endpoint:        str,
    records_per_req: int,
    reqs_per_sensor: int,
    timeout_s:       float,
) -> None:
    """Run all stress test scenarios in sequence and print the summary."""

    print(f"\n{'═' * 62}")
    print(f"  🔥  Jal-Prahari Stress Test")
    print(f"{'═' * 62}")
    print(f"  Endpoint        : {endpoint}")
    print(f"  Records/request : {records_per_req}")
    print(f"  Requests/sensor : {reqs_per_sensor}")
    print(f"  Timeout         : {timeout_s} s")
    print(f"  Scenarios       : {scenarios}")
    print(f"{'═' * 62}\n")

    # Pre-flight check — is the server reachable?
    print("  Checking server connectivity …", end=" ", flush=True)
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(endpoint.replace("/api/v1/telemetry", "/health"))
        if r.status_code == 200:
            print("✔  Server is up.\n")
        else:
            print(f"⚠  Server returned HTTP {r.status_code}.\n")
    except Exception as exc:
        print(f"\n  ✗  Cannot reach server: {exc}")
        print("  ⚠  Make sure the FastAPI server is running:\n"
              "     uvicorn app.main:app --port 8000  (from backend/)\n")
        sys.exit(1)

    all_results: List[ScenarioResult] = []

    for n_sensors in scenarios:
        print(f"\n{'─' * 62}")
        print(f"  Running scenario: {n_sensors} sensors")
        print(f"{'─' * 62}")

        result = await run_scenario(
            n_sensors       = n_sensors,
            endpoint        = endpoint,
            records_per_req = records_per_req,
            reqs_per_sensor = reqs_per_sensor,
            timeout_s       = timeout_s,
        )
        print_scenario_report(result)
        all_results.append(result)

        # Cool-down between scenarios to avoid DB connection pool exhaustion
        if n_sensors != scenarios[-1]:
            print("\n  Cooling down for 3 seconds …", flush=True)
            await asyncio.sleep(3)

    print_summary_table(all_results)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Jal-Prahari Stress Test — measures ingestion throughput and latency",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--sensors",
        nargs="+",
        type=int,
        default=DEFAULT_SCENARIOS,
        metavar="N",
        help="Sensor counts to test, e.g. --sensors 50 100 500",
    )
    parser.add_argument(
        "--endpoint",
        type=str,
        default=DEFAULT_ENDPOINT,
        help="FastAPI ingestion endpoint URL",
    )
    parser.add_argument(
        "--records",
        type=int,
        default=DEFAULT_RECORDS_PER_REQ,
        help="Readings per HTTP request",
    )
    parser.add_argument(
        "--requests",
        type=int,
        default=DEFAULT_REQS_PER_SENSOR,
        help="HTTP requests per sensor",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_S,
        help="Per-request timeout in seconds",
    )
    args = parser.parse_args()

    try:
        asyncio.run(
            main(
                scenarios       = sorted(set(args.sensors)),
                endpoint        = args.endpoint,
                records_per_req = args.records,
                reqs_per_sensor = args.requests,
                timeout_s       = args.timeout,
            )
        )
    except KeyboardInterrupt:
        print("\nStress test interrupted.")
