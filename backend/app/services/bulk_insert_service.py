# backend/app/services/bulk_insert_service.py
#
# Responsibility: High-performance bulk insertion of telemetry records into
# the water_logs PostGIS table.
#
# ─── Async design: run_in_executor() ─────────────────────────────────────────
#
# Problem
# ───────
# The existing database engine (connection.py) uses synchronous SQLAlchemy +
# psycopg2.  FastAPI runs entirely on an asyncio event loop.  If we call a
# blocking SQLAlchemy session method directly inside an `async def` handler,
# we block the *entire event loop* for the duration of the DB call.  While
# the DB call runs (~10–30 ms for a bulk insert), all other in-flight requests
# are frozen — effectively turning the async server into a single-threaded
# synchronous server.
#
# Solution: asyncio.get_event_loop().run_in_executor()
# ────────────────────────────────────────────────────
# run_in_executor() submits the blocking function to a thread pool and
# returns an awaitable Future.  The event loop yields immediately and
# continues processing other requests.  When the thread finishes, the
# event loop resumes the original coroutine with the result.
#
# Visual flow:
#
#   async handler (event loop thread)          DB thread (pool thread)
#   ───────────────────────────────────        ──────────────────────
#   await bulk_insert_water_logs(...)
#     → submit _sync_bulk_insert() to pool
#     → yield (event loop serves other         _sync_bulk_insert() running
#               requests during this gap)      (bulk_insert_mappings + commit)
#     ← resume when thread completes      ←─── return inserted_count
#   return IngestionResponse
#
# ─── Why bulk_insert_mappings() not session.add()? ───────────────────────────
#
# session.add(obj) flow (row-by-row):
#   For N=100 records:
#     100 × (ORM overhead + INSERT statement + network round-trip)
#     ≈ 100 × ~1 ms = ~100 ms for a local PostgreSQL
#
# session.bulk_insert_mappings() flow:
#   For N=100 records:
#     1 × executemany() → 1 network packet containing 100 value tuples
#     ≈ 1 × ~5 ms = ~5 ms for the same 100 records
#     → ~20x faster for batch sizes of 100
#
# bulk_insert_mappings() bypasses:
#   • Identity map (no object tracking)
#   • Unit of work (no change detection)
#   • ORM event hooks (no before_insert / after_insert signals)
# These are all unnecessary for append-only ingestion tables.
#
# ─── Transaction handling and rollback ───────────────────────────────────────
#
# All chunks in a batch are committed in a single transaction:
#   session.bulk_insert_mappings(chunk_1)  ─┐
#   session.bulk_insert_mappings(chunk_2)   ├─ all in one open transaction
#   session.bulk_insert_mappings(chunk_N)  ─┘
#   session.commit()  ← single commit
#
# If *any* chunk fails (FK violation, constraint error, connection drop):
#   session.rollback()  ← reverts ALL chunks — no partial batches in the DB
#   Re-raise the exception for the handler to return a 503
#
# This all-or-nothing guarantee is critical for data integrity.  A partial
# batch in the DB would corrupt per-sensor time-series analysis.
#
# ─── Connection pooling note ─────────────────────────────────────────────────
# The engine (connection.py) uses pool_size=5, max_overflow=10.
# Each thread in _DB_EXECUTOR holds a connection for the duration of the
# insert, then releases it back to the pool.
# With DB_POOL_THREADS=4 (default), at most 4 connections are held
# simultaneously — well within the pool_size=5 limit.
# Under burst load, max_overflow=10 allows up to 15 total connections.

import asyncio
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import List

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.logger import get_logger
from app.database.models import WaterLog

# ─────────────────────────────────────────────────────────────────────────────
# Module configuration
# ─────────────────────────────────────────────────────────────────────────────

logger = get_logger(__name__, log_file="ingestion.log")

# Records per executemany() call.
# Empirically: 100 is a good default for PostgreSQL on the same host.
# Tune upward (200–500) for low-latency LAN connections to a remote DB.
# Tune downward if you hit "statement too long" or memory pressure.
BATCH_SIZE: int = int(os.getenv("BATCH_SIZE", "100"))

# Thread pool dedicated to blocking DB inserts.
# I/O-bound operations benefit from threads even in CPython (the GIL is
# released during socket I/O, allowing true concurrent DB calls).
# max_workers = min(4, cpu_count) is a conservative safe default.
_DB_EXECUTOR: ThreadPoolExecutor = ThreadPoolExecutor(
    max_workers=int(os.getenv("DB_POOL_THREADS", "4")),
    thread_name_prefix="jal-db",
)


# ─────────────────────────────────────────────────────────────────────────────
# Synchronous insert function (runs inside thread pool)
# ─────────────────────────────────────────────────────────────────────────────

def _sync_bulk_insert(session: Session, records: List[dict]) -> int:
    """
    Execute a transactional bulk INSERT for all records, chunked by BATCH_SIZE.

    ⚠️  This function is SYNCHRONOUS and BLOCKING.
        It must ONLY be called via run_in_executor() from the async layer.
        Never call it directly from an async handler.

    Transaction strategy
    ────────────────────
    All chunks share one transaction.  On success: single commit.
    On any failure: full rollback before re-raising.  This ensures the
    database never contains a partial batch.

    Args:
        session : An open SQLAlchemy Session (obtained via get_db dependency).
        records : Flat list of dicts with keys matching WaterLog columns.
                  Required keys: sensor_id (int), water_level (float), timestamp (datetime).

    Returns:
        Number of rows successfully inserted.

    Raises:
        SQLAlchemyError : On any DB-level failure (FK violation, connection
                          drop, constraint error).  Transaction is rolled back.
        Exception       : Any unexpected error; also rolled back.
    """
    if not records:
        logger.warning("_sync_bulk_insert called with empty records list.")
        return 0

    inserted    = 0
    n_chunks    = (len(records) + BATCH_SIZE - 1) // BATCH_SIZE
    t_start     = time.perf_counter()

    logger.info(
        "Starting bulk insert | total=%d rows | chunk_size=%d | chunks=%d",
        len(records),
        BATCH_SIZE,
        n_chunks,
    )

    try:
        for chunk_idx, chunk_start in enumerate(range(0, len(records), BATCH_SIZE)):
            chunk = records[chunk_start : chunk_start + BATCH_SIZE]

            # bulk_insert_mappings():
            #   • Maps dict keys → column names without constructing ORM objects
            #   • Issues a single executemany() per chunk
            #   • All chunks share the current open transaction
            session.bulk_insert_mappings(WaterLog, chunk)
            inserted += len(chunk)

            logger.debug(
                "Chunk %d/%d inserted | rows=%d",
                chunk_idx + 1,
                n_chunks,
                len(chunk),
            )

        # Commit the entire batch atomically
        session.commit()

    except SQLAlchemyError as exc:
        # Rollback reverts every chunk already sent in this transaction
        session.rollback()
        elapsed_ms = (time.perf_counter() - t_start) * 1000
        logger.error(
            "Bulk insert FAILED after inserting %d/%d rows | "
            "Transaction ROLLED BACK | elapsed=%.2f ms | error=%s",
            inserted,
            len(records),
            elapsed_ms,
            exc,
        )
        raise  # re-raise so the async handler returns HTTP 503

    except Exception as exc:
        session.rollback()
        logger.error("Unexpected error in _sync_bulk_insert: %s", exc)
        raise

    elapsed_ms = (time.perf_counter() - t_start) * 1000
    logger.info(
        "Bulk insert SUCCESS | rows=%d | batch_size=%d | elapsed=%.2f ms",
        inserted,
        BATCH_SIZE,
        elapsed_ms,
    )
    return inserted


# ─────────────────────────────────────────────────────────────────────────────
# Public async interface
# ─────────────────────────────────────────────────────────────────────────────

async def bulk_insert_water_logs(session: Session, records: List[dict]) -> int:
    """
    Async wrapper around _sync_bulk_insert().

    Submits the blocking DB insert to the dedicated thread pool and yields
    control back to the FastAPI event loop immediately.  The event loop
    continues processing other HTTP requests while the thread pool executes
    the insert.

    This is the ONLY function that should be called from async handlers.

    Args:
        session : An open SQLAlchemy Session (from the get_db dependency).
        records : Validated list of dicts ready for bulk insertion.
                  Expected keys: sensor_id, water_level, timestamp.

    Returns:
        Number of rows successfully inserted.

    Raises:
        SQLAlchemyError : Propagated from _sync_bulk_insert() on DB failure.
        Exception       : Any other unexpected failure.

    Usage (in FastAPI handler):
        inserted = await bulk_insert_water_logs(db_session, validated_records)
    """
    if not records:
        logger.warning("bulk_insert_water_logs called with 0 records — skipping.")
        return 0

    logger.info(
        "Dispatching %d records to thread pool executor.", len(records)
    )

    loop = asyncio.get_event_loop()
    inserted: int = await loop.run_in_executor(
        _DB_EXECUTOR,          # use the dedicated DB thread pool
        _sync_bulk_insert,     # the blocking function to run
        session,               # arg 1: SQLAlchemy session
        records,               # arg 2: record list
    )

    return inserted
