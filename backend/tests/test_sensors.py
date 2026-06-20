# backend/tests/test_sensors.py
#
# Tests for GET /api/sensors endpoint.
#
# ─── What we test ─────────────────────────────────────────────────────────────
#   1. Empty dataset    → HTTP 200 with {"total": 0, "sensors": []}
#   2. Sensors present  → HTTP 200 with populated sensor list
#   3. Response schema  → all required fields are present and typed correctly
#   4. DB error         → HTTP 500 is returned cleanly (not a Python traceback)
#
# ─── What we do NOT test here ─────────────────────────────────────────────────
#   - The SQL query logic inside get_all_sensors() → tested via service unit tests
#   - PostGIS geometry parsing → tested via sensor_service unit tests
#   - Database connection setup → tested via integration tests
#
# ─── Mock configuration guide ─────────────────────────────────────────────────
# The route calls: sensors, total = await get_all_sensors(db)
# get_all_sensors() calls:
#     count = (await db.execute(count_stmt)).scalar_one()
#     sensors = (await db.execute(data_stmt)).scalars().all()
#
# We patch get_all_sensors at the route level using unittest.mock.patch
# so the mock controls the return value without needing to chain
# db.execute().scalar_one() mock setups.

from datetime import datetime, timezone
from typing import List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_mock_sensor(
    sensor_id: int = 1,
    name: str = "SENSOR_001",
    lat: float = 19.076,
    lon: float = 72.877,
    elevation: float = 4.5,
) -> MagicMock:
    """
    Create a MagicMock that mimics a Sensor ORM instance.

    The sensor_to_dict() function in sensor_service reads:
        sensor.id, sensor.name, sensor.geometry, sensor.base_elevation, sensor.created_at

    We return a pre-built dict from sensor_to_dict in tests, so the geometry
    mock doesn't need to be a real GeoAlchemy2 object.
    """
    mock_sensor = MagicMock()
    mock_sensor.id             = sensor_id
    mock_sensor.name           = name
    mock_sensor.base_elevation = elevation
    mock_sensor.created_at     = datetime(2024, 6, 15, 10, 30, tzinfo=timezone.utc)
    # geometry mock — to_shape() will be patched at service level, so
    # the geometry attribute itself only needs to be truthy
    mock_sensor.geometry = MagicMock()
    return mock_sensor


def _make_sensor_dict(
    sensor_id: int = 1,
    name: str = "SENSOR_001",
    lat: float = 19.076,
    lon: float = 72.877,
    elevation: float = 4.5,
) -> dict:
    """Return a dict matching SensorResponse field names."""
    return {
        "id":             sensor_id,
        "name":           name,
        "latitude":       lat,
        "longitude":      lon,
        "base_elevation": elevation,
        "created_at":     datetime(2024, 6, 15, 10, 30, tzinfo=timezone.utc),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_sensors_empty_dataset(client_with_mock_db: AsyncClient) -> None:
    """
    GET /api/sensors returns 200 with empty list when no sensors exist.

    Expected:
        HTTP 200
        {"total": 0, "sensors": []}

    Behaviour under test:
        The endpoint must NOT return 404 for an empty table.
        An empty list is a valid API state (before seeding / in fresh deployments).
    """
    with patch(
        "app.api.routes.sensors.get_all_sensors",
        new_callable=AsyncMock,
        return_value=([], 0),
    ):
        response = await client_with_mock_db.get("/api/sensors")

    assert response.status_code == 200

    body = response.json()
    assert body["total"]   == 0
    assert body["sensors"] == []


@pytest.mark.asyncio
async def test_get_sensors_returns_sensor_list(client_with_mock_db: AsyncClient) -> None:
    """
    GET /api/sensors returns HTTP 200 with sensor data when sensors exist.

    Expected:
        HTTP 200
        {
            "total": 2,
            "sensors": [
                {"id": 1, "name": "SENSOR_001", "latitude": 19.076, ...},
                {"id": 2, "name": "SENSOR_002", "latitude": 18.959, ...}
            ]
        }
    """
    sensor_dicts = [
        _make_sensor_dict(sensor_id=1, name="SENSOR_001", lat=19.076, lon=72.877),
        _make_sensor_dict(sensor_id=2, name="SENSOR_002", lat=18.959, lon=72.819),
    ]

    with patch(
        "app.api.routes.sensors.get_all_sensors",
        new_callable=AsyncMock,
        return_value=(sensor_dicts, 2),
    ):
        response = await client_with_mock_db.get("/api/sensors")

    assert response.status_code == 200

    body = response.json()
    assert body["total"] == 2
    assert len(body["sensors"]) == 2

    # Verify first sensor structure
    first = body["sensors"][0]
    assert first["id"]        == 1
    assert first["name"]      == "SENSOR_001"
    assert first["latitude"]  == 19.076
    assert first["longitude"] == 72.877
    assert "base_elevation" in first
    assert "created_at"     in first


@pytest.mark.asyncio
async def test_get_sensors_response_schema(client_with_mock_db: AsyncClient) -> None:
    """
    All required fields are present in the response and correctly typed.

    Tests the Pydantic serialisation layer — ensures FastAPI correctly
    serialises SensorResponse and SensorListResponse to JSON.
    """
    sensor_dict  = _make_sensor_dict()

    with patch(
        "app.api.routes.sensors.get_all_sensors",
        new_callable=AsyncMock,
        return_value=([sensor_dict], 1),
    ):
        response = await client_with_mock_db.get("/api/sensors")

    assert response.status_code == 200
    body = response.json()

    # Top-level envelope
    assert "total"   in body
    assert "sensors" in body
    assert isinstance(body["total"],   int)
    assert isinstance(body["sensors"], list)

    # Per-sensor fields
    sensor = body["sensors"][0]
    required_fields = {"id", "name", "latitude", "longitude", "base_elevation", "created_at"}
    assert required_fields.issubset(sensor.keys()), (
        f"Missing fields: {required_fields - sensor.keys()}"
    )
    assert isinstance(sensor["id"],        int)
    assert isinstance(sensor["latitude"],  float)
    assert isinstance(sensor["longitude"], float)
    assert isinstance(sensor["name"],      str)


@pytest.mark.asyncio
async def test_get_sensors_db_error_returns_500(client_with_mock_db: AsyncClient) -> None:
    """
    GET /api/sensors returns HTTP 500 (not an unhandled traceback) on DB failure.

    This ensures the route's exception handler works — the client receives a
    clean JSON error body, not a raw Python exception page.
    """
    with patch(
        "app.api.routes.sensors.get_all_sensors",
        new_callable=AsyncMock,
        side_effect=Exception("Connection pool exhausted"),
    ):
        response = await client_with_mock_db.get("/api/sensors")

    assert response.status_code == 500

    body = response.json()
    assert "detail" in body
    assert body["detail"]["error"] == "Database query failed"


@pytest.mark.asyncio
async def test_health_check(client: AsyncClient) -> None:
    """
    GET /health returns HTTP 200 with status=ok.

    This endpoint has no DB dependency — tests the base FastAPI app setup.
    """
    response = await client.get("/health")

    assert response.status_code == 200

    body = response.json()
    assert body["status"]  == "ok"
    assert "timestamp"     in body
    assert "version"       in body
