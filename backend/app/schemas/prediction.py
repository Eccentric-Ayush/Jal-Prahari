# backend/app/schemas/prediction.py
from datetime import datetime, timezone
from enum import Enum
from typing import List

from pydantic import BaseModel, Field


class RiskLevel(str, Enum):
    """
    Categorical risk severity based on the calculated risk index.
    """
    LOW = "LOW"             # 0.00 – 0.25
    MODERATE = "MODERATE"   # 0.25 – 0.50
    HIGH = "HIGH"           # 0.50 – 0.75
    CRITICAL = "CRITICAL"   # 0.75 – 1.00


class RiskCluster(BaseModel):
    """
    Represents the predicted flood risk for a specific sensor location.
    Provides geographic coordinates and the computed risk score.
    """
    sensor_id: int = Field(..., description="Unique identifier of the IoT sensor.")
    latitude: float = Field(..., description="Geographic latitude (EPSG:4326).")
    longitude: float = Field(..., description="Geographic longitude (EPSG:4326).")
    elevation: float = Field(..., description="Elevation at the sensor location (metres).")
    risk_index: float = Field(
        ..., 
        ge=0.0, 
        le=1.0, 
        description="Calculated flood-risk probability score (0.0 to 1.0)."
    )
    risk_level: RiskLevel = Field(..., description="Categorical risk severity.")


class PredictionResponse(BaseModel):
    """
    Envelope response for the predictive engine endpoint.
    """
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when the prediction was executed."
    )
    cluster_count: int = Field(..., description="Number of risk clusters returned in the response.")
    clusters: List[RiskCluster] = Field(..., description="List of predicted sensor risk profiles.")

    class Config:
        json_schema_extra = {
            "example": {
                "generated_at": "2026-06-20T12:00:00Z",
                "cluster_count": 1,
                "clusters": [
                    {
                        "sensor_id": 7,
                        "latitude": 28.612,
                        "longitude": 77.231,
                        "elevation": 214.3,
                        "risk_index": 0.82,
                        "risk_level": "CRITICAL"
                    }
                ]
            }
        }
