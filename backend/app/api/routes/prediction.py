# backend/app/api/routes/prediction.py
#
# ─── API Route for Predictive Engine ──────────────────────────────────────────
# Exposes the flood-risk simulation to frontend clients.
# ──────────────────────────────────────────────────────────────────────────────

from fastapi import APIRouter, Depends, Query, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timezone

from app.core.logger import get_logger
from app.dependencies.database import get_db
from app.schemas.prediction import PredictionResponse
from app.services.predictive_engine import PredictiveEngine

logger = get_logger(__name__, log_file="predictions.log")

router = APIRouter(tags=["Prediction"])

@router.get(
    "/predict/risk",
    response_model=PredictionResponse,
    status_code=status.HTTP_200_OK,
    summary="Generate real-time flood-risk predictions",
    description=(
        "Retrieves the predicted flood risk for sensor clusters based on recent "
        "water levels, historical trends, and DEM elevation vulnerabilities.\n\n"
        "**Note:** This is a provisional heuristic simulation. Risk values are "
        "indicative and generated from rule-based thresholds."
    ),
    responses={
        500: {"description": "Internal server error during prediction pipeline execution."}
    }
)
async def get_risk_predictions(
    request: Request,
    min_risk: float = Query(0.5, ge=0.0, le=1.0, description="Minimum risk threshold (default 0.5 to only show HIGH/CRITICAL)"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of clusters to return"),
    db: AsyncSession = Depends(get_db)
) -> PredictionResponse:
    """
    GET /api/predict/risk

    1. Attempts to retrieve the cached DEMParser from the FastAPI app state.
    2. Instantiates the PredictiveEngine.
    3. Returns the PredictionResponse envelope.
    """
    try:
        # FastAPI lifespan ensures app.state.dem_parser is initialized.
        # Fallback to None if not present (e.g., tests without lifespan)
        dem_parser = getattr(request.app.state, "dem_parser", None)

        engine = PredictiveEngine(session=db, dem_parser=dem_parser)
        clusters = await engine.predict_cluster_risks(min_risk=min_risk, limit=limit)

        return PredictionResponse(
            generated_at=datetime.now(timezone.utc),
            cluster_count=len(clusters),
            clusters=clusters
        )
    except Exception as exc:
        logger.error("Prediction engine failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "Prediction pipeline failed",
                "detail": str(exc)
            }
        )
