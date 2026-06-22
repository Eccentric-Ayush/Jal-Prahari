# backend/app/api/websocket.py
#
# ─── WebSocket endpoint for real-time risk broadcasting ──────────────────────
#
# Route:  WS /ws/risk
#
# Protocol:
#   1. Client opens ws://localhost:8000/ws/risk
#   2. Server immediately sends the current risk snapshot (same schema as
#      GET /api/predict/risk) so the client has data before the first broadcast.
#   3. The shared background loop (started in main.py lifespan) pushes fresh
#      predictions to ALL connected clients every 5 seconds.
#   4. On disconnect (clean or abrupt), the client is removed from the registry.
#
# ─── Why we send an initial snapshot ─────────────────────────────────────────
#   Without an initial push, a client opening the WebSocket would see a blank
#   map/sidebar for up to 5 seconds (the next broadcast cycle).  Sending the
#   snapshot on connect guarantees instant population of the UI regardless of
#   when the client connects relative to the broadcast schedule.

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request
from starlette.websockets import WebSocketState

from app.core.connection_manager import manager
from app.core.logger import get_logger
from app.database.session import get_async_session_factory
from app.services.predictive_engine import PredictiveEngine

logger = get_logger(__name__, log_file="websocket.log")

router = APIRouter(tags=["WebSocket"])


@router.websocket("/ws/risk")
async def websocket_risk(websocket: WebSocket):
    """
    WebSocket endpoint: WS /ws/risk

    Lifecycle:
        connect  → register client, send current snapshot
        message  → ignored (read-only stream; clients don't send messages)
        disconnect → deregister client
    """
    await manager.connect(websocket)
    logger.info("New WS client connected to /ws/risk")

    try:
        # ── Step 1: Send the initial snapshot immediately on connect ──────────
        await _send_snapshot(websocket, websocket.app)

        # ── Step 2: Keep the connection alive; wait for disconnect ─────────────
        # We don't process incoming client messages (this is a server-push stream),
        # but we must await receive() to detect client disconnects promptly.
        # receive_text() raises WebSocketDisconnect when the client closes.
        while True:
            # Block until client sends something (usually a close frame)
            data = await websocket.receive_text()
            # Optionally handle client messages here in future (e.g., filter updates)

    except WebSocketDisconnect:
        logger.info("WS client disconnected cleanly from /ws/risk")
    except Exception as exc:
        logger.warning("WS connection error on /ws/risk: %s", exc)
    finally:
        await manager.disconnect(websocket)


async def _send_snapshot(websocket: WebSocket, app) -> None:
    """
    Compute the current risk snapshot and send it to a single newly-connected client.

    This runs a fresh predict_cluster_risks() query — same as the REST endpoint —
    so the connecting client gets live data immediately without waiting for the
    next broadcast cycle.
    """
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

        if websocket.client_state == WebSocketState.CONNECTED:
            await websocket.send_json(payload)
            logger.info(
                "Initial WS snapshot sent: %d clusters", len(clusters)
            )

    except Exception as exc:
        logger.error("Failed to send initial WS snapshot: %s", exc, exc_info=True)
