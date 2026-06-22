# backend/app/core/connection_manager.py
#
# ─── WebSocket Connection Manager ────────────────────────────────────────────
#
# Responsibility:
#   Maintain a registry of all active WebSocket connections and provide a
#   broadcast method that fans out a JSON payload to every connected client.
#
# ─── Singleton pattern ────────────────────────────────────────────────────────
#   One ConnectionManager is instantiated at module level and imported wherever
#   needed.  This ensures all routes and the background broadcast loop share
#   the same connection set — no duplication, no sync issues.
#
# ─── Why a shared background task rather than one loop per client? ─────────────
#   Option A — loop per client:
#     Each new WS connection spawns its own asyncio task that runs
#     predict_cluster_risks() on a 5-second interval.
#     Problem: With N clients connected, the DB is hammered with N simultaneous
#     identical queries every 5 seconds.  50 clients → 50 parallel DB queries
#     for the same data.  This is wasteful and doesn't scale.
#
#   Option B — single shared broadcast loop (THIS IMPLEMENTATION):
#     One background task runs predict_cluster_risks() once every 5 seconds,
#     regardless of how many clients are connected.  The result is broadcast
#     to ALL connected clients in a single pass.
#     Result: 1 DB query per cycle, O(n) fan-out over the connection set.
#     This is the correct pattern for "live dashboard" architectures.
#
# ─── Graceful disconnect handling ─────────────────────────────────────────────
#   WebSocket.send_json() raises WebSocketDisconnect or ConnectionClosedError
#   if the client closed without a clean WS close frame (e.g., browser tab
#   closed, network drop).  broadcast() catches these per-connection and
#   removes the stale socket from the active set so it doesn't block future
#   broadcasts.

import asyncio
import logging
from typing import Set

from fastapi import WebSocket
from starlette.websockets import WebSocketState

logger = logging.getLogger("jal_prahari.connection_manager")


class ConnectionManager:
    """
    Thread-safe WebSocket connection registry for broadcast messaging.

    Methods:
        connect(ws)        — Accept and register a new WebSocket.
        disconnect(ws)     — Remove a WebSocket from the registry.
        broadcast(payload) — Send JSON payload to all active connections.
    """

    def __init__(self) -> None:
        # Set gives O(1) add/remove and avoids duplicate registrations.
        self._active: Set[WebSocket] = set()
        # Lock prevents race conditions if connect/disconnect are called
        # concurrently with broadcast (asyncio is single-threaded but
        # cooperative scheduling means we can interleave at await points).
        self._lock: asyncio.Lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        """Accept the WebSocket handshake and add it to the active set."""
        await websocket.accept()
        async with self._lock:
            self._active.add(websocket)
        logger.info(
            "WS client connected. Active connections: %d", len(self._active)
        )

    async def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket from the active set (idempotent)."""
        async with self._lock:
            self._active.discard(websocket)
        logger.info(
            "WS client disconnected. Active connections: %d", len(self._active)
        )

    async def broadcast(self, payload: dict) -> None:
        """
        Fan out a JSON payload to every active connection.

        Failures on individual sockets (client dropped without clean close)
        are caught, the stale socket is removed, and broadcasting continues
        to the remaining healthy connections.

        Args:
            payload: A JSON-serialisable dict (e.g. PredictionResponse.model_dump()).
        """
        # Snapshot the set so we can safely remove stale connections
        # without mutating the set we're iterating over.
        async with self._lock:
            snapshot = set(self._active)

        stale: Set[WebSocket] = set()

        for ws in snapshot:
            try:
                if ws.client_state == WebSocketState.CONNECTED:
                    await ws.send_json(payload)
            except Exception as exc:
                logger.warning(
                    "Failed to send to WS client (marking stale): %s", exc
                )
                stale.add(ws)

        if stale:
            async with self._lock:
                self._active -= stale
            logger.info(
                "Removed %d stale connections. Active: %d",
                len(stale),
                len(self._active),
            )

    @property
    def active_count(self) -> int:
        """Return the current number of active connections (non-blocking)."""
        return len(self._active)


# ─── Singleton ────────────────────────────────────────────────────────────────
# Instantiated once at import time and shared across the entire application.
# The background broadcast loop in main.py and the WS route in websocket.py
# both import this same object.
manager = ConnectionManager()
