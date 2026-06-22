# backend/app/tests/test_websocket.py
#
# ─── WebSocket endpoint tests ─────────────────────────────────────────────────
#
# Tests the WS /ws/risk endpoint using FastAPI's built-in TestClient WebSocket
# support (which wraps Starlette's WebSocketTestSession).
#
# What is tested:
#   1. Connection succeeds (101 Upgrade handshake)
#   2. Server sends an initial JSON snapshot immediately on connect
#   3. The snapshot has the correct schema (generated_at, cluster_count, clusters)
#   4. Clean disconnect doesn't raise or crash the server
#
# Note: The background broadcast loop (_broadcast_loop) is NOT tested here
# because it requires asyncio.sleep(5) per cycle — too slow for unit tests.
# The broadcast loop's correctness is validated by the integration test
# (manual: open two tabs, both receive updates simultaneously).

import json
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    """Module-scoped TestClient — avoids starting/stopping the app per test."""
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


class TestWebSocketRisk:

    def test_connect_and_receive_initial_snapshot(self, client):
        """
        Verify that opening the WS connection immediately yields a valid
        risk snapshot with the expected envelope fields.
        """
        with client.websocket_connect("/ws/risk") as ws:
            # The server sends the snapshot on connect — no client message needed
            raw = ws.receive_text()
            payload = json.loads(raw)

            # Envelope fields must exist
            assert "generated_at" in payload, "Missing 'generated_at' in WS snapshot"
            assert "cluster_count" in payload, "Missing 'cluster_count' in WS snapshot"
            assert "clusters" in payload, "Missing 'clusters' in WS snapshot"

            # Type checks
            assert isinstance(payload["cluster_count"], int)
            assert isinstance(payload["clusters"], list)

            # If clusters returned, validate one entry
            if payload["clusters"]:
                cluster = payload["clusters"][0]
                assert "sensor_id"   in cluster
                assert "latitude"    in cluster
                assert "longitude"   in cluster
                assert "risk_index"  in cluster
                assert "risk_level"  in cluster

    def test_clean_disconnect_no_crash(self, client):
        """
        Verify the server doesn't raise when the client disconnects cleanly.
        """
        with client.websocket_connect("/ws/risk") as ws:
            ws.receive_text()   # consume the initial snapshot
            # Context manager exit performs a clean close (code 1000)
            # If the server crashes on disconnect, TestClient raises here.

    def test_schema_types_are_correct(self, client):
        """
        Validate the data types of each field in the snapshot envelope.
        """
        with client.websocket_connect("/ws/risk") as ws:
            payload = json.loads(ws.receive_text())

            # generated_at should be a valid ISO timestamp string
            from datetime import datetime
            dt = datetime.fromisoformat(payload["generated_at"].replace("Z", "+00:00"))
            assert dt is not None

            # cluster_count must match len(clusters)
            assert payload["cluster_count"] == len(payload["clusters"])

    def test_multiple_concurrent_connections(self, client):
        """
        Open two simultaneous WS connections and verify both receive
        an initial snapshot independently.
        """
        with client.websocket_connect("/ws/risk") as ws1:
            with client.websocket_connect("/ws/risk") as ws2:
                snap1 = json.loads(ws1.receive_text())
                snap2 = json.loads(ws2.receive_text())

                assert "clusters" in snap1
                assert "clusters" in snap2
                # Both should see the same cluster count
                assert snap1["cluster_count"] == snap2["cluster_count"]
