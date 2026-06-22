# backend/app/main.py
#
# Responsibility: FastAPI application entry point.
#
# This module does three things:
#   1. Defines the lifespan hook (startup + shutdown logic).
#   2. Instantiates the FastAPI `app` with correct metadata.
#   3. Mounts all routers and middleware.
#
# ─── Lifespan hook ───────────────────────────────────────────────────────────
# FastAPI's `lifespan` context manager replaces the deprecated `@app.on_event`
# pattern.  Code before `yield` runs at startup; code after `yield` runs at
# shutdown.  This guarantees:
#   • DB tables exist before the first request arrives.
#   • Demo sensors are seeded so simulator FK constraints are satisfied.
#   • Connections and thread pools are cleaned up on graceful shutdown.
#
# ─── Sensor auto-seeding ─────────────────────────────────────────────────────
# The water_logs.sensor_id column has a FK constraint on sensors.id.
# The simulator uses IDs 1–N_SENSORS.  If the sensors table is empty when
# the simulator starts, every insert will fail with a FK violation.
#
# Auto-seeding on startup:
#   • Is idempotent (COUNT check before inserting).
#   • Uses bulk_save_objects() — a single round-trip for all 50 rows.
#   • Generates reproducible Mumbai-area coordinates (fixed RNG seed).
#   • Includes a realistic base_elevation per sensor (from DEM range).
#
# ─── CORS ────────────────────────────────────────────────────────────────────
# allow_origins=["*"] is acceptable for a local development / demo environment.
# In production: list specific frontend origins to prevent unauthorized
# cross-origin access to the ingestion API.

import asyncio
import random
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from app.api.ingestion import router as ingestion_router
from app.api.router import api_router
from app.api.websocket import router as ws_router
from app.core.config import get_settings
from app.core.connection_manager import manager as ws_manager
from app.core.dem_parser import load_dem
from app.core.logger import get_logger
from app.database.connection import get_session_factory
from app.database.init_db import initialise_database
from app.database.models import Sensor
from app.database.session import close_async_engine, get_async_session_factory
from app.services.predictive_engine import PredictiveEngine

# ─────────────────────────────────────────────────────────────────────────────
# Logger
# ─────────────────────────────────────────────────────────────────────────────

logger = get_logger(__name__, log_file="ingestion.log")

# ─────────────────────────────────────────────────────────────────────────────
# Sensor seeding constants
# ─────────────────────────────────────────────────────────────────────────────

# Delhi bounding box — sensors are scattered across the city grid.
_DELHI_MIN_LON = 76.85
_DELHI_MAX_LON = 77.35
_DELHI_MIN_LAT = 28.40
_DELHI_MAX_LAT = 28.90

# Number of demo sensors to seed.  Must match N_SENSORS in the simulator.
_N_DEMO_SENSORS = 50

# Fixed seed ensures the same 50 sensor positions every restart.
_SEED_RNG = random.Random(42)


# ─────────────────────────────────────────────────────────────────────────────
# Sensor seeding
# ─────────────────────────────────────────────────────────────────────────────

def _seed_demo_sensors(session: Session, n: int = _N_DEMO_SENSORS) -> None:
    """
    Insert n demo sensor rows if the sensors table is empty.

    Idempotency guarantee:
        Queries COUNT(*) before inserting.  If the table already has rows,
        returns immediately without modifying the database.  Safe to call on
        every application startup.

    Coordinate generation:
        Uses a fixed-seed RNG for reproducible Delhi-area coordinates.
        Each sensor gets:
          • A unique name  : SENSOR_001 … SENSOR_050
          • A random lon/lat within the Delhi bounding box (WGS84)
          • A random base_elevation between 1.5 m and 15.0 m above MSL

    Geometry format:
        GeoAlchemy2 accepts WKT strings in the format:
            'SRID=4326;POINT(longitude latitude)'
        Note: PostGIS uses (longitude, latitude) order — the same as GeoJSON,
        which is the OPPOSITE of the common (lat, lon) convention.

    Performance:
        bulk_save_objects() issues a single multi-row INSERT statement
        rather than one INSERT per object.  50 rows ≈ 1 ms insert time.

    Args:
        session : An open SQLAlchemy Session.
        n       : Number of sensors to seed.
    """
    existing: int = session.query(Sensor).count()

    if existing >= n:
        logger.info(
            "Sensor seeding skipped — %d sensors already exist (need %d).",
            existing,
            n,
        )
        return

    logger.info("Seeding %d demo sensors in Delhi bounding box …", n)

    sensors: list[Sensor] = []
    rng = random.Random(42)  # always reproducible

    for i in range(1, n + 1):
        lon  = rng.uniform(_DELHI_MIN_LON, _DELHI_MAX_LON)
        lat  = rng.uniform(_DELHI_MIN_LAT, _DELHI_MAX_LAT)
        elev = round(rng.uniform(1.5, 15.0), 2)

        sensors.append(
            Sensor(
                name           = f"SENSOR_{i:03d}",
                geometry       = f"SRID=4326;POINT({lon:.6f} {lat:.6f})",
                base_elevation = elev,
            )
        )

    session.bulk_save_objects(sensors)
    session.commit()
    logger.info("Seeded %d demo sensors successfully.", n)


# ─────────────────────────────────────────────────────────────────────────────
# Lifespan context manager
# ─────────────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application startup and shutdown logic.

    Startup sequence (before yield):
        1. initialise_database()  — verifies DB connection, enables PostGIS,
                                    creates all tables (CREATE TABLE IF NOT EXISTS).
        2. _seed_demo_sensors()   — inserts 50 sensor rows if the table is empty.

    Shutdown (after yield):
        Log a graceful shutdown message.
        (Thread pools and DB connections are cleaned up by their own
         __del__ / finalise mechanisms.)
    """
    logger.info("=" * 60)
    logger.info("  Jal-Prahari Backend — Starting")
    logger.info("=" * 60)

    # Step 1: Ensure DB schema exists (idempotent)
    initialise_database()

    # Step 2: Seed demo sensors (idempotent)
    factory  = get_session_factory()
    db: Session = factory()
    try:
        _seed_demo_sensors(db)
    finally:
        db.close()

    # Step 3: Load DEM Parser into app state
    settings = get_settings()
    dem_parser = None
    try:
        dem_parser = load_dem(settings.dem_file_path)
        app.state.dem_parser = dem_parser
    except Exception as exc:
        logger.warning(
            "Failed to load DEMParser during startup. "
            "Elevation data will fallback to base_elevation. Error: %s", exc
        )

    logger.info("Startup complete. API is accepting requests.")

    # Step 4: Start the shared WebSocket broadcast background task
    # This single task runs predict_cluster_risks() every 5 seconds and
    # fans the result out to all connected WS clients (see _broadcast_loop below).
    broadcast_task = asyncio.create_task(_broadcast_loop(app))
    logger.info("WS broadcast background task started.")

    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    logger.info("Jal-Prahari Backend — Shutting down gracefully.")

    # Cancel the broadcast loop cleanly
    broadcast_task.cancel()
    try:
        await broadcast_task
    except asyncio.CancelledError:
        pass
    logger.info("WS broadcast task stopped.")

    if dem_parser is not None:
        dem_parser.close()
        logger.info("DEMParser closed.")

    # Dispose the async engine pool to cleanly close asyncpg connections.
    # Without this, asyncpg logs "Task was destroyed but it is pending" warnings.
    await close_async_engine()

# ─────────────────────────────────────────────────────────────────────────────
# Background WebSocket broadcast loop
# ─────────────────────────────────────────────────────────────────────────────

_WS_BROADCAST_INTERVAL = 5  # seconds


async def _broadcast_loop(app: FastAPI) -> None:
    """
    Single shared background task: runs predict_cluster_risks() every 5 seconds
    and broadcasts the JSON payload to ALL connected WebSocket clients.

    WHY a single shared loop rather than one loop per client:
        Option A (per-client loop): Each connection spawns its own asyncio task.
        With N clients, that means N identical DB queries running in parallel
        every 5 seconds.  Wasteful and doesn't scale.

        Option B (single shared loop, THIS IMPLEMENTATION):
        One task runs predict_cluster_risks() once per interval, regardless
        of how many clients are connected.  The result is broadcast to all
        clients in a single fan-out pass.  1 DB query per cycle, O(N) sends.
        This is the standard "pub/sub over WebSocket" pattern.

    Graceful shutdown:
        asyncio.CancelledError is raised in the lifespan shutdown hook via
        task.cancel().  We catch it and return cleanly without re-raising.
    """
    logger.info("WS broadcast loop started (interval=%ds)", _WS_BROADCAST_INTERVAL)
    try:
        while True:
            await asyncio.sleep(_WS_BROADCAST_INTERVAL)

            if ws_manager.active_count == 0:
                # Skip expensive DB query if no clients are listening.
                continue

            try:
                dem_parser = getattr(app.state, "dem_parser", None)
                session_factory = get_async_session_factory()

                async with session_factory() as session:
                    engine = PredictiveEngine(session=session, dem_parser=dem_parser)
                    clusters = await engine.predict_cluster_risks(min_risk=0.0, limit=100)

                payload = {
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "cluster_count": len(clusters),
                    "clusters": [c.model_dump() for c in clusters],
                }

                await ws_manager.broadcast(payload)
                logger.info(
                    "WS broadcast: %d clusters -> %d clients",
                    len(clusters),
                    ws_manager.active_count,
                )

            except Exception as exc:
                # Log the error but DON'T crash the loop — next cycle continues.
                logger.error("WS broadcast cycle failed: %s", exc, exc_info=True)

    except asyncio.CancelledError:
        logger.info("WS broadcast loop cancelled cleanly.")


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI application
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title       = "Jal-Prahari API",
    description = (
        "## GIS-Enabled Digital Twin — Urban Flood Monitoring\n\n"
        "Jal-Prahari is a production-grade IoT telemetry and CRUD platform for "
        "real-time urban flood monitoring and predictive water-logging forecasting.\n\n"
        "### Available API groups\n"
        "| Group | Endpoints | Description |\n"
        "|---|---|---|\n"
        "| **Sensors** | `GET /api/sensors` | List all registered IoT sensors with GPS coordinates |\n"
        "| **Water Logs** | `POST /api/logs` | Record a sensor water-level reading |\n"
        "| **History** | `GET /api/sensors/{id}/history` | Paginated reading history |\n"
        "| **Prediction** | `GET /api/predict/risk` | Generate rule-based flood-risk predictions |\n"
        "| **Telemetry** | `POST /api/v1/telemetry` | High-throughput batch ingestion (up to 500/req) |\n"
        "| **Health** | `GET /health` | Liveness probe for load balancers |\n\n"
        "### Architecture\n"
        "- **Async CRUD layer**: `AsyncSession` + `asyncpg` (3–5x faster reads)\n"
        "- **Ingestion layer**: `run_in_executor` + `bulk_insert_mappings` (~20x faster writes)\n"
        "- **PostGIS**: spatial storage with GiST indexes for geometry queries\n"
        "- **Pydantic v2**: Rust-compiled validation (5–50x faster than v1)\n"
    ),
    version     = "1.0.0",
    contact     = {
        "name": "Jal-Prahari Engineering",
        "url":  "https://github.com/Eccentric-Ayush/Jal-Prahari",
    },
    lifespan    = lifespan,
    docs_url    = "/docs",
    redoc_url   = "/redoc",
)

# ─────────────────────────────────────────────────────────────────────────────
# Middleware
# ─────────────────────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins     = [
        "http://localhost:5173", 
        "http://127.0.0.1:5173",
        "http://localhost:3000"
    ],
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)

# ─────────────────────────────────────────────────────────────────────────────
# Routers
# ─────────────────────────────────────────────────────────────────────────────

# High-throughput ingestion router (sync engine + run_in_executor)
# Endpoint: POST /api/v1/telemetry
app.include_router(ingestion_router)

# CRUD API router (async engine + AsyncSession)
# Endpoints: GET /api/sensors | POST /api/logs | GET /api/sensors/{id}/history
app.include_router(api_router)

# WebSocket router — WS /ws/risk
# Must be registered on the root app, NOT under the /api prefix,
# because Vite's proxy will forward /ws/* to the backend separately.
app.include_router(ws_router)

# ─────────────────────────────────────────────────────────────────────────────
# Health check
# ─────────────────────────────────────────────────────────────────────────────

@app.get(
    "/health",
    tags        = ["Health"],
    summary     = "Liveness probe",
    description = "Returns 200 OK when the server is up and accepting requests.",
)
async def health_check() -> dict:
    """
    Liveness probe endpoint for load balancers and Docker healthchecks.

    Returns a JSON object with the server status, current UTC timestamp,
    and the API version string.
    """
    return {
        "status":    "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version":   "1.0.0",
    }
