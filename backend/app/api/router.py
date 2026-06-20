# backend/app/api/router.py
#
# Responsibility: Central router that assembles all CRUD route modules.
#
# ─── Why a central router? ───────────────────────────────────────────────────
# Without a central router, main.py must import each route module individually:
#
#   app.include_router(sensors_router)
#   app.include_router(logs_router)
#   app.include_router(water_quality_router)   ← future
#   app.include_router(alerts_router)          ← future
#
# With a central api_router, main.py imports ONE object:
#
#   app.include_router(api_router)
#
# Benefits:
#   1. main.py stays clean as the app grows (no router explosion)
#   2. The prefix ("/api") is declared once, not on every sub-router
#   3. Future: apply shared middleware/auth to the entire /api prefix here
#
# ─── API versioning strategy ──────────────────────────────────────────────────
# Current route layout:
#
#   /api/sensors                    → sensors_router (CRUD, this file)
#   /api/logs                       → logs_router    (CRUD, this file)
#   /api/sensors/{id}/history       → logs_router    (CRUD, this file)
#   /api/v1/telemetry               → ingestion_router (high-throughput, ingestion.py)
#
# The ingestion endpoint is intentionally versioned (/api/v1/telemetry)
# because it is the high-churn public-facing endpoint most likely to change.
# The CRUD endpoints use /api (unversioned) for simplicity during initial build.
#
# Migration path when breaking changes are needed:
#   api_v2_router = APIRouter(prefix="/api/v2")
#   api_v2_router.include_router(sensors_v2_router)
#   app.include_router(api_v2_router)   ← mount alongside existing /api router
#   # Old /api routes continue to work for existing clients
#
# ─── Future: Authentication ───────────────────────────────────────────────────
# To add JWT authentication to all CRUD endpoints:
#   api_router = APIRouter(
#       prefix="/api",
#       dependencies=[Depends(verify_jwt_token)],  ← applied to ALL routes
#   )
# No individual route files need to change.

from fastapi import APIRouter

from app.api.routes.logs import router as logs_router
from app.api.routes.sensors import router as sensors_router
from app.api.routes.prediction import router as prediction_router

# ─────────────────────────────────────────────────────────────────────────────
# Central CRUD router
# ─────────────────────────────────────────────────────────────────────────────

api_router = APIRouter(prefix="/api")

# Mount sub-routers — order matters for path resolution when prefixes overlap.
# More specific paths should be registered before wildcards.
api_router.include_router(sensors_router)   # GET /api/sensors
api_router.include_router(logs_router)      # POST /api/logs
                                            # GET  /api/sensors/{id}/history
api_router.include_router(prediction_router)# GET  /api/predict/risk
