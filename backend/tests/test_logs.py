# backend/tests/test_logs.py
#
# Tests for:
#   POST /api/logs                      — create a water log
#   GET  /api/sensors/{id}/history      — paginated history
#
# ─── Error scenario coverage ─────────────────────────────────────────────────
#   422 Unprocessable Entity:  bad field type or out-of-bounds value
#   404 Not Found:             sensor_id valid but not in DB
#   201 Created:               happy path
#   200 OK + pagination:       history query
#   500 Internal Server Error: DB failure

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient


# ─────────────────────────────────────────────────────────────────────────────
# Helper: mock WaterLog ORM instance
# ─────────────────────────────────────────────────────────────────────────────

def _make_mock_log(
    log_id: int = 1001,
    sensor_id: int = 1,
    water_level: float = 0.72,
) -> MagicMock:
    """Return a MagicMock matching the WaterLog ORM column structure."""
    mock_log = MagicMock()
    mock_log.id          = log_id
    mock_log.sensor_id   = sensor_id
    mock_log.water_level = water_level
    mock_log.timestamp   = datetime(2024, 6, 15, 10, 30, tzinfo=timezone.utc)
    return mock_log


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/logs — happy path
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_post_log_valid_sensor(client_with_mock_db: AsyncClient) -> None:
    """
    POST /api/logs with a valid sensor_id returns HTTP 201 with the new record.

    Expected response:
        {
            "success": true,
            "message": "Water log recorded for sensor 1.",
            "log": {
                "id": 1001,
                "sensor_id": 1,
                "water_level": 0.72,
                "timestamp": "2024-06-15T10:30:00Z"
            }
        }
    """
    mock_log = _make_mock_log(log_id=1001, sensor_id=1, water_level=0.72)

    with patch(
        "app.api.routes.logs.create_water_log",
        new_callable=AsyncMock,
        return_value=mock_log,
    ):
        response = await client_with_mock_db.post(
            "/api/logs",
            json={"sensor_id": 1, "water_level": 0.72},
        )

    assert response.status_code == 201

    body = response.json()
    assert body["success"]          is True
    assert "sensor 1"              in body["message"]
    assert body["log"]["id"]        == 1001
    assert body["log"]["sensor_id"] == 1
    assert body["log"]["water_level"] == pytest.approx(0.72, rel=1e-5)
    assert "timestamp" in body["log"]


@pytest.mark.asyncio
async def test_post_log_invalid_sensor_id_returns_404(
    client_with_mock_db: AsyncClient,
) -> None:
    """
    POST /api/logs with a sensor_id that doesn't exist returns HTTP 404.

    The sensor_id passes Pydantic validation (it's a valid positive int)
    but the service layer raises ValueError because it's not in the DB.

    Expected:
        HTTP 404
        {"detail": {"error": "Sensor not found", "detail": "..."}}
    """
    with patch(
        "app.api.routes.logs.create_water_log",
        new_callable=AsyncMock,
        side_effect=ValueError("Sensor with id=9999 does not exist."),
    ):
        response = await client_with_mock_db.post(
            "/api/logs",
            json={"sensor_id": 9999, "water_level": 0.5},
        )

    assert response.status_code == 404

    body = response.json()
    assert body["detail"]["error"] == "Sensor not found"
    assert "9999" in body["detail"]["detail"]


@pytest.mark.asyncio
async def test_post_log_missing_sensor_id_returns_422(
    client_with_mock_db: AsyncClient,
) -> None:
    """
    POST /api/logs with missing sensor_id returns HTTP 422 (Pydantic validation).

    422 is generated automatically by FastAPI before the handler runs.
    No mock needed — this tests the Pydantic schema validation layer.
    """
    response = await client_with_mock_db.post(
        "/api/logs",
        json={"water_level": 0.5},   # sensor_id is missing
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_post_log_invalid_water_level_returns_422(
    client_with_mock_db: AsyncClient,
) -> None:
    """
    POST /api/logs with water_level out of bounds returns HTTP 422.

    water_level has bounds ge=-500.0, le=10_000.0 in WaterLogCreate.
    A value of 99999 exceeds the upper bound.
    """
    response = await client_with_mock_db.post(
        "/api/logs",
        json={"sensor_id": 1, "water_level": 99999.0},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_post_log_db_error_returns_500(client_with_mock_db: AsyncClient) -> None:
    """
    POST /api/logs returns HTTP 500 on unexpected DB failure.

    Ensures the route catches generic Exception (not just ValueError)
    and returns a clean JSON error body.
    """
    with patch(
        "app.api.routes.logs.create_water_log",
        new_callable=AsyncMock,
        side_effect=Exception("Deadlock detected"),
    ):
        response = await client_with_mock_db.post(
            "/api/logs",
            json={"sensor_id": 1, "water_level": 0.72},
        )

    assert response.status_code == 500
    assert response.json()["detail"]["error"] == "Database insert failed"


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/sensors/{id}/history — happy path
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_history_returns_paginated_results(
    client_with_mock_db: AsyncClient,
) -> None:
    """
    GET /api/sensors/1/history returns HTTP 200 with paginated history.

    Expected response structure:
        {
            "sensor_id": 1,
            "total": 120,
            "page": 1,
            "page_size": 50,
            "history": [...]   // 50 records
        }
    """
    mock_logs = [_make_mock_log(log_id=i, sensor_id=1, water_level=float(i)) for i in range(1, 51)]

    with patch(
        "app.api.routes.logs.get_sensor_history",
        new_callable=AsyncMock,
        return_value=(mock_logs, 120),
    ):
        response = await client_with_mock_db.get(
            "/api/sensors/1/history",
            params={"page": 1, "page_size": 50},
        )

    assert response.status_code == 200

    body = response.json()
    assert body["sensor_id"] == 1
    assert body["total"]     == 120
    assert body["page"]      == 1
    assert body["page_size"] == 50
    assert len(body["history"]) == 50


@pytest.mark.asyncio
async def test_get_history_default_pagination(client_with_mock_db: AsyncClient) -> None:
    """
    GET /api/sensors/1/history without query params uses default page=1, page_size=50.
    """
    mock_logs = [_make_mock_log(log_id=i) for i in range(1, 11)]  # 10 records

    with patch(
        "app.api.routes.logs.get_sensor_history",
        new_callable=AsyncMock,
        return_value=(mock_logs, 10),
    ):
        response = await client_with_mock_db.get("/api/sensors/1/history")

    assert response.status_code == 200

    body = response.json()
    assert body["page"]      == 1     # default
    assert body["page_size"] == 50    # default
    assert body["total"]     == 10
    assert len(body["history"]) == 10


@pytest.mark.asyncio
async def test_get_history_invalid_sensor_returns_404(
    client_with_mock_db: AsyncClient,
) -> None:
    """
    GET /api/sensors/9999/history returns HTTP 404 when sensor doesn't exist.
    """
    with patch(
        "app.api.routes.logs.get_sensor_history",
        new_callable=AsyncMock,
        side_effect=ValueError("Sensor with id=9999 does not exist."),
    ):
        response = await client_with_mock_db.get("/api/sensors/9999/history")

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "Sensor not found"


@pytest.mark.asyncio
async def test_get_history_invalid_page_size_returns_422(
    client_with_mock_db: AsyncClient,
) -> None:
    """
    GET /api/sensors/1/history?page_size=500 returns 422 (exceeds max 200).

    FastAPI validates Query parameters against ge/le bounds before the handler.
    """
    response = await client_with_mock_db.get(
        "/api/sensors/1/history",
        params={"page_size": 500},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_get_history_empty_returns_200_not_404(
    client_with_mock_db: AsyncClient,
) -> None:
    """
    GET /api/sensors/1/history returns 200 with empty list when no logs exist.

    The sensor exists but has no readings yet (e.g., newly registered sensor).
    This should return 200 {"history": []}, NOT 404.
    """
    with patch(
        "app.api.routes.logs.get_sensor_history",
        new_callable=AsyncMock,
        return_value=([], 0),
    ):
        response = await client_with_mock_db.get("/api/sensors/1/history")

    assert response.status_code == 200

    body = response.json()
    assert body["total"]   == 0
    assert body["history"] == []


@pytest.mark.asyncio
async def test_get_history_record_schema(client_with_mock_db: AsyncClient) -> None:
    """
    Each history record contains the required fields with correct types.

    Verifies Pydantic serialisation of WaterLogResponse within HistoryResponse.
    """
    mock_log = _make_mock_log(log_id=42, sensor_id=1, water_level=12.34)

    with patch(
        "app.api.routes.logs.get_sensor_history",
        new_callable=AsyncMock,
        return_value=([mock_log], 1),
    ):
        response = await client_with_mock_db.get("/api/sensors/1/history")

    assert response.status_code == 200

    record = response.json()["history"][0]
    assert record["id"]          == 42
    assert record["sensor_id"]   == 1
    assert record["water_level"] == pytest.approx(12.34, rel=1e-5)
    assert "timestamp"           in record
    assert isinstance(record["timestamp"], str)   # serialised as ISO-8601 string
