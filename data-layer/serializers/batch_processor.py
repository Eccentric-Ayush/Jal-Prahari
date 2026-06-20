# data-layer/serializers/batch_processor.py
#
# Responsibility: Utility functions for chunking records into fixed-size
# batches and pre-validating payloads on the simulator side before sending.
#
# ─── Why chunk before sending? ────────────────────────────────────────────────
# Sending all records in one giant HTTP request has two failure modes:
#
#   1. Size limits
#      Most reverse proxies (nginx, AWS ALB) cap request bodies at 1–10 MB.
#      A single request containing 10 000 sensor readings × ~100 bytes each
#      = ~1 MB — right at the limit.  Chunking keeps every request small.
#
#   2. All-or-nothing failure amplification
#      If one record in a 5 000-row batch fails DB validation (e.g., sensor_id
#      FK mismatch), the server rolls back the *entire* transaction.  Chunked
#      batches limit blast radius to BATCH_SIZE rows per failure.
#
# ─── Why pre-validate on the simulator side? ─────────────────────────────────
# Running Pydantic validation *before* sending means:
#
#   • Bad records are caught locally with rich field-level error messages.
#   • The server never sees records it will immediately reject (saves RTT).
#   • The error log on the simulator shows which sensor generated bad data,
#     making debugging far faster than reading server-side 422 responses.
#   • In production, invalid records can be routed to a dead-letter queue
#     rather than silently dropped.

import logging
from typing import Generator, List, Tuple

from pydantic import ValidationError

from serializers.schemas import TelemetryReading

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Chunking
# ─────────────────────────────────────────────────────────────────────────────

def chunk_records(
    records: List[dict],
    batch_size: int = 100,
) -> Generator[List[dict], None, None]:
    """
    Yield successive fixed-size chunks from a flat list of raw record dicts.

    This is a pure generator — it does not materialise all chunks in memory
    at once, making it safe to use with very large record lists.

    Args:
        records    : The flat list of raw record dicts to chunk.
        batch_size : Maximum number of records per chunk (default 100).

    Yields:
        Sublists of length ≤ batch_size, in order.

    Example:
        >>> data = [{"sensor_id": i} for i in range(7)]
        >>> list(chunk_records(data, batch_size=3))
        [[{0}, {1}, {2}], [{3}, {4}, {5}], [{6}]]
    """
    if batch_size < 1:
        raise ValueError(f"batch_size must be ≥ 1, got {batch_size}")

    for start in range(0, len(records), batch_size):
        yield records[start : start + batch_size]


# ─────────────────────────────────────────────────────────────────────────────
# Pre-validation
# ─────────────────────────────────────────────────────────────────────────────

def validate_batch(
    raw_records: List[dict],
) -> Tuple[List[TelemetryReading], List[dict]]:
    """
    Validate a list of raw dicts against the TelemetryReading schema.

    Uses Pydantic v2's model_validate() for each record individually so that
    invalid records are isolated: one bad record does not block the rest.

    Args:
        raw_records : List of raw dicts (e.g., from payload_generator).

    Returns:
        A 2-tuple:
            valid  : List of validated TelemetryReading instances.
            errors : List of dicts, each containing:
                         "record": the original raw dict
                         "errors": Pydantic's error detail list

    Usage:
        valid, errors = validate_batch(raw_records)
        if errors:
            logger.warning("Dropped %d invalid records", len(errors))
        payload = [r.to_payload() for r in valid]
    """
    valid:  List[TelemetryReading] = []
    errors: List[dict]             = []

    for record in raw_records:
        try:
            # model_validate() is the Pydantic v2 equivalent of .parse_obj().
            # It runs the full Rust validation pipeline on the raw dict and
            # raises ValidationError immediately if any field fails.
            reading = TelemetryReading.model_validate(record)
            valid.append(reading)

        except ValidationError as exc:
            errors.append(
                {
                    "record": record,
                    "errors": exc.errors(include_url=False),  # exclude verbose docs URLs
                }
            )
            logger.warning(
                "Pre-validation failed | sensor_id=%s | errors=%s",
                record.get("sensor_id", "UNKNOWN"),
                exc.errors(include_url=False),
            )

    if errors:
        logger.warning(
            "Batch pre-validation: %d valid, %d rejected",
            len(valid),
            len(errors),
        )
    else:
        logger.debug("Batch pre-validation: all %d records valid.", len(valid))

    return valid, errors


# ─────────────────────────────────────────────────────────────────────────────
# Combined: chunk + validate + yield payload lists
# ─────────────────────────────────────────────────────────────────────────────

def prepare_batches(
    raw_records: List[dict],
    batch_size: int = 100,
) -> Generator[List[dict], None, None]:
    """
    Validate and chunk raw records, yielding JSON-ready payload lists.

    Combines validate_batch() and chunk_records() into a single pipeline:
        raw dicts → validate → filter valid → chunk → yield payload dicts

    Args:
        raw_records : Flat list of raw record dicts from the simulator.
        batch_size  : Maximum records per yielded chunk.

    Yields:
        Lists of JSON-serialisable dicts ready to be sent as HTTP bodies.

    Example:
        for batch in prepare_batches(raw_records, batch_size=50):
            await client.post(endpoint, json=batch)
    """
    valid, errors = validate_batch(raw_records)

    if errors:
        logger.warning(
            "Skipping %d invalid records before chunking.", len(errors)
        )

    valid_payloads = [r.to_payload() for r in valid]

    for chunk in chunk_records(valid_payloads, batch_size=batch_size):
        yield chunk
