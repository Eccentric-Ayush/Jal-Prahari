# backend/tests/test_prediction.py
import pytest
from httpx import AsyncClient
from unittest.mock import patch, AsyncMock, MagicMock
from app.schemas.prediction import RiskLevel

@pytest.fixture
def mock_dem_parser():
    """Mock the DEM Parser so we don't load a real GeoTIFF during tests."""
    parser = MagicMock()
    # Let's say all sensors are at an elevation of 10.0m
    parser.get_elevation.return_value = 10.0
    parser.dataset.closed = False
    return parser

@pytest.fixture
def mock_sensor_history():
    """Return mock sensor history mappings."""
    return [
        {
            "id": 1,
            "latitude": 19.01,
            "longitude": 72.81,
            "base_elevation": 5.0,
            "avg_level": 1.0,
            "current_level": 4.5  # High risk: rapid rise near max (5.0m)
        },
        {
            "id": 2,
            "latitude": 19.02,
            "longitude": 72.82,
            "base_elevation": 20.0,
            "avg_level": 0.5,
            "current_level": 0.6  # Low risk
        }
    ]

@pytest.mark.asyncio
async def test_get_predictions_returns_high_risks(client_with_mock_db: AsyncClient, mock_dem_parser, mock_sensor_history) -> None:
    """
    Test that predictions correctly evaluate thresholds and limit output.
    """
    # Override app state for the dem_parser
    client_with_mock_db._transport.app.state.dem_parser = mock_dem_parser

    with patch(
        "app.services.predictive_engine.PredictiveEngine.get_sensor_history",
        new_callable=AsyncMock,
        return_value=mock_sensor_history
    ), patch(
        "app.services.predictive_engine.PredictiveEngine.get_total_sensor_count",
        new_callable=AsyncMock,
        return_value=2
    ):
        response = await client_with_mock_db.get("/api/predict/risk?min_risk=0.5")

    assert response.status_code == 200
    body = response.json()

    assert "generated_at" in body
    assert body["cluster_count"] == 1
    
    cluster = body["clusters"][0]
    assert cluster["sensor_id"] == 1
    assert cluster["risk_index"] >= 0.5
    assert cluster["risk_level"] in ["HIGH", "CRITICAL"]

@pytest.mark.asyncio
async def test_get_predictions_empty_history(client_with_mock_db: AsyncClient) -> None:
    """
    Test behavior when no sensor history exists (should return empty 200 OK).
    """
    with patch(
        "app.services.predictive_engine.PredictiveEngine.get_sensor_history",
        new_callable=AsyncMock,
        return_value=[]
    ), patch(
        "app.services.predictive_engine.PredictiveEngine.get_total_sensor_count",
        new_callable=AsyncMock,
        return_value=0
    ):
        response = await client_with_mock_db.get("/api/predict/risk?min_risk=0.0")

    assert response.status_code == 200
    body = response.json()
    assert body["cluster_count"] == 0
    assert len(body["clusters"]) == 0

@pytest.mark.asyncio
async def test_get_predictions_threshold_filtering(client_with_mock_db: AsyncClient, mock_dem_parser, mock_sensor_history) -> None:
    """
    Test that modifying the min_risk parameter accurately filters clusters.
    """
    client_with_mock_db._transport.app.state.dem_parser = mock_dem_parser

    with patch(
        "app.services.predictive_engine.PredictiveEngine.get_sensor_history",
        new_callable=AsyncMock,
        return_value=mock_sensor_history
    ), patch(
        "app.services.predictive_engine.PredictiveEngine.get_total_sensor_count",
        new_callable=AsyncMock,
        return_value=2
    ):
        # min_risk=0.0 should return all sensors (2 sensors)
        response_all = await client_with_mock_db.get("/api/predict/risk?min_risk=0.0")
        assert response_all.status_code == 200
        assert response_all.json()["cluster_count"] == 2

        # min_risk=0.99 should probably return none based on the mocked values
        response_none = await client_with_mock_db.get("/api/predict/risk?min_risk=0.99")
        assert response_none.status_code == 200
        assert response_none.json()["cluster_count"] == 0
