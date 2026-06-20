# data-layer/serializers/schemas.py
#
# Responsibility: Pydantic v2 models for the simulator-side serialization.
#
# ─── Why a separate schema from the backend's telemetry_schema.py? ────────────
# The data-layer is intentionally a standalone package that can run on:
#   • Edge IoT gateways (Raspberry Pi, industrial PCs)
#   • Separate CI/CD pipelines for load testing
#   • Data engineering pipelines that pre-validate before forwarding
#
# Sharing the backend's schema would force the data-layer to depend on the
# full FastAPI stack (heavy).  A lightweight duplicate avoids that coupling
# and allows each side to evolve independently (the simulator might add
# device_battery, firmware_version, etc. without touching the server schema).
#
# ─── Pydantic v2 serialization: model_validate() vs model_dump() ─────────────
#
#   model_validate(raw_dict)
#   ─────────────────────────
#   Replaces Pydantic v1's .parse_obj() / .from_orm().
#   Runs the full Rust-compiled validation pipeline:
#     dict → type coercion → bounds checks → Python model instance
#   For 50 000 records/sec, v2 reduces this step from ~8 µs/record (v1) to
#   ~0.5 µs/record — a 16x throughput improvement in the validation layer alone.
#
#   model_dump()
#   ────────────
#   Replaces Pydantic v1's .dict().
#   In v2, this is implemented in Rust and skips Python __dict__ traversal.
#   Output is a plain dict suitable for json.dumps() or SQLAlchemy insert.
#   Using mode="json" converts datetime → ISO-8601 string automatically.

from datetime import datetime, timezone
from typing import List

from pydantic import BaseModel, ConfigDict, Field


class TelemetryReading(BaseModel):
    """
    A single sensor telemetry reading (simulator-side model).

    Mirrors the backend's TelemetryReadingIn exactly so payloads generated
    here are guaranteed to pass server-side validation without modification.

    Usage:
        reading = TelemetryReading(sensor_id=1, water_level=12.5)
        payload = reading.to_payload()   # → {"sensor_id": 1, ...}
    """

    model_config = ConfigDict(
        strict=False,
        populate_by_name=True,
    )

    sensor_id: int = Field(
        ...,
        ge=1,
        le=10_000,
        description="Sensor integer ID.",
    )

    water_level: float = Field(
        ...,
        ge=-500.0,
        le=10_000.0,
        description="Water depth in centimetres above the sensor datum.",
    )

    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="ISO-8601 UTC measurement timestamp.",
    )

    def to_payload(self) -> dict:
        """
        Return a JSON-serialisable dict for inclusion in the HTTP body.

        Converts datetime → ISO-8601 string using Pydantic's mode="json"
        serialiser so the dict can be passed directly to json.dumps().

        Why not model_dump() without mode="json"?
            model_dump() returns a raw Python dict with a datetime object.
            json.dumps() does not know how to serialise datetime — it raises
            TypeError.  mode="json" instructs Pydantic to pre-convert all
            non-JSON-native types to their JSON equivalents.
        """
        return self.model_dump(mode="json")


class TelemetryBatch(BaseModel):
    """
    A collection of readings to be sent in a single HTTP POST request.

    Encapsulates a list of TelemetryReading objects and provides a
    convenience method to serialize all readings to a JSON-ready list.

    Batch size limit (500) mirrors the server's max_length constraint,
    ensuring client-side enforcement before the request is sent.
    """

    model_config = ConfigDict(strict=False)

    readings: List[TelemetryReading] = Field(
        ...,
        min_length=1,
        max_length=500,
        description="List of sensor readings in this batch.",
    )

    def to_payload(self) -> List[dict]:
        """
        Return a JSON-serialisable list of reading dicts.

        Example:
            batch = TelemetryBatch(readings=[reading1, reading2])
            json.dumps(batch.to_payload())  # valid JSON array
        """
        return [r.to_payload() for r in self.readings]

    @classmethod
    def from_raw(cls, raw_list: List[dict]) -> "TelemetryBatch":
        """
        Construct a TelemetryBatch from a list of raw dicts.

        Uses model_validate() (Pydantic v2) which is ~16x faster than
        the v1 equivalent .parse_obj() because the validation runs in Rust.

        Args:
            raw_list : List of dicts from payload_generator.generate_batch().

        Returns:
            Validated TelemetryBatch instance.

        Raises:
            pydantic.ValidationError if any record fails validation.
        """
        return cls.model_validate({"readings": raw_list})
