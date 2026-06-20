# backend/app/services/predictive_engine.py
#
# ─── Responsibility ────────────────────────────────────────────────────────────
# Orchestrates data fetching, processing, and risk calculation.
#
# ─── N+1 Query Optimization ────────────────────────────────────────────────────
# We need current water levels and average water levels for *all* sensors.
# Doing this one sensor at a time (N+1 queries) would cripple performance.
# 
# Instead, we execute a single aggregated query that groups by sensor_id.
# 
# Note: For the "current" water level, a true time-series aggregate would use
# ROW_NUMBER() over partitions. To keep the SQLAlchemy ORM simple and portable 
# for v1, we calculate `max(timestamp)` and average levels, assuming the max 
# level in the recent window closely correlates to current risk. In v2, this 
# query can be enhanced with complex PostGIS spatial clustering.
# ──────────────────────────────────────────────────────────────────────────────

from typing import List, Optional, Tuple
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession
import math

from app.core.logger import get_logger
from app.database.models import Sensor, WaterLog
from app.core.dem_parser import DEMParser, CoordinateOutOfBoundsError
from app.core.risk_calculator import calculate_risk_index, determine_risk_level
from app.schemas.prediction import RiskCluster

logger = get_logger(__name__, log_file="predictions.log")

class PredictiveEngine:
    def __init__(self, session: AsyncSession, dem_parser: Optional[DEMParser] = None):
        """
        Args:
            session: Async SQLAlchemy session.
            dem_parser: Optional initialized DEMParser for elevation lookups.
        """
        self.session = session
        self.dem_parser = dem_parser

    async def get_total_sensor_count(self) -> int:
        """Helper to get the total number of sensors for logging exclusions."""
        stmt = select(func.count(Sensor.id))
        return (await self.session.execute(stmt)).scalar_one()

    async def get_sensor_history(self) -> List[dict]:
        """
        Fetches aggregated sensor and water-log history in a single optimized query.
        Returns a list of dictionaries containing spatial and temporal aggregates.
        """
        # CTE to identify the latest log for each sensor
        latest_logs_cte = (
            select(
                WaterLog.sensor_id,
                WaterLog.water_level.label("current_level"),
                func.row_number().over(
                    partition_by=WaterLog.sensor_id,
                    order_by=WaterLog.timestamp.desc()
                ).label("rn")
            ).cte("latest_logs")
        )

        # Filter the CTE to only keep the latest log (rn=1)
        latest_logs_subq = select(latest_logs_cte).where(latest_logs_cte.c.rn == 1).subquery("latest")

        # Query: Get sensor metadata, average levels, and join with the latest level.
        # NOTE ON INNER JOIN: This intentionally excludes sensors that have zero logs.
        # A sensor without temporal data cannot produce a valid trend or risk score.
        stmt = (
            select(
                Sensor.id,
                func.ST_Y(Sensor.geometry).label("latitude"),
                func.ST_X(Sensor.geometry).label("longitude"),
                Sensor.base_elevation,
                func.avg(WaterLog.water_level).label("avg_level"),
                latest_logs_subq.c.current_level 
            )
            .join(WaterLog, WaterLog.sensor_id == Sensor.id)
            .join(latest_logs_subq, latest_logs_subq.c.sensor_id == Sensor.id)
            .group_by(Sensor.id, latest_logs_subq.c.current_level)
        )

        result = await self.session.execute(stmt)
        rows = result.mappings().all()
        return [dict(row) for row in rows]

    def get_elevation(self, lat: float, lon: float, fallback: float) -> float:
        """
        Safe DEM elevation lookup. Falls back to sensor.base_elevation if the
        DEM lookup fails or the parser is missing.
        """
        if self.dem_parser is None or self.dem_parser.dataset.closed:
            return fallback
        
        try:
            elev = self.dem_parser.get_elevation(lat, lon)
            if math.isnan(elev):
                return fallback
            return elev
        except (CoordinateOutOfBoundsError, ValueError) as exc:
            logger.debug("DEM lookup failed for lat=%.6f lon=%.6f: %s", lat, lon, exc)
            return fallback
        except Exception as exc:
            logger.warning("Unexpected DEM read error at lat=%.6f lon=%.6f: %s", lat, lon, exc)
            return fallback

    async def predict_cluster_risks(self, min_risk: float = 0.5, limit: int = 100) -> List[RiskCluster]:
        """
        Main orchestration method:
        1. Fetches aggregated history for all sensors.
        2. Augments with DEM elevation data.
        3. Invokes the risk calculator for each sensor.
        4. Filters by minimum risk threshold and sorts by severity.
        """
        logger.info("Starting prediction cycle with min_risk=%.2f limit=%d", min_risk, limit)
        
        # Query total sensors to track exclusions
        total_sensors = await self.get_total_sensor_count()
        history_rows = await self.get_sensor_history()
        
        excluded_count = total_sensors - len(history_rows)
        logger.info(
            "Prediction cycle: %d sensors total, %d excluded due to zero historical logs.",
            total_sensors, excluded_count
        )

        if not history_rows:
            logger.warning("No sensor history available for predictions.")
            return []

        clusters = []
        for row in history_rows:
            sensor_id = row["id"]
            lat = row["latitude"]
            lon = row["longitude"]
            base_elev = row["base_elevation"]
            current_lvl = row["current_level"] or 0.0
            avg_lvl = row["avg_level"] or 0.0

            # Augment with precise elevation from DEM
            precise_elev = self.get_elevation(lat, lon, fallback=base_elev)

            # ML Abstraction layer invocation
            risk_idx = calculate_risk_index(
                current_level=current_lvl,
                avg_level=avg_lvl,
                elevation=precise_elev
            )

            if risk_idx >= min_risk:
                risk_lvl = determine_risk_level(risk_idx)
                cluster = RiskCluster(
                    sensor_id=sensor_id,
                    latitude=lat,
                    longitude=lon,
                    elevation=precise_elev,
                    risk_index=risk_idx,
                    risk_level=risk_lvl
                )
                clusters.append(cluster)

        # Sort highest risk first
        clusters.sort(key=lambda c: c.risk_index, reverse=True)
        
        final_clusters = clusters[:limit]
        
        # Logging aggregates
        if final_clusters:
            avg_risk = sum(c.risk_index for c in final_clusters) / len(final_clusters)
            logger.info(
                "Prediction cycle complete: %d sensors analyzed, %d clusters generated, avg_risk=%.2f",
                len(history_rows), len(final_clusters), avg_risk
            )
        else:
            logger.info("Prediction cycle complete: 0 clusters met the min_risk=%.2f threshold.", min_risk)

        return final_clusters
