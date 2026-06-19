# backend/app/database/init_db.py
#
# Responsibility: One-shot idempotent database initialisation.
#
# Run directly:
#     python -m app.database.init_db
#
# Or call initialise_database() from FastAPI's lifespan hook once the API
# layer is built.
#
# What this script does (in order):
#   1. Load environment variables from .env
#   2. Build the SQLAlchemy engine
#   3. Verify the database connection (fail fast with a clear error)
#   4. Check whether the PostGIS extension is installed
#   5. Install PostGIS if absent (requires SUPERUSER on the DB role)
#   6. Create all tables declared in models.py (CREATE TABLE IF NOT EXISTS)
#   7. Print a structured summary of tables and indexes created
#
# Idempotency guarantee:
#   Running this script multiple times is safe.  SQLAlchemy's create_all()
#   uses IF NOT EXISTS internally, and the PostGIS extension check prevents
#   a duplicate CREATE EXTENSION error.

import logging
import sys
import textwrap
from typing import Optional

from dotenv import load_dotenv
from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError, ProgrammingError

# Initialise logging before any local imports so early errors are captured.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("jal_prahari.init_db")

# Load .env early so that connection.py sees the variables when imported.
load_dotenv()

# Local imports — must come after load_dotenv()
from app.database.connection import get_engine          # noqa: E402
from app.database.models import Base                    # noqa: E402  (imports all models)


# ---------------------------------------------------------------------------
# Section 1 — Connectivity check
# ---------------------------------------------------------------------------

def verify_connection() -> None:
    """
    Execute a trivial query to confirm the engine can reach the database.

    Raises SystemExit on failure so the script stops immediately with a
    human-readable message rather than a cryptic SQLAlchemy traceback.
    """
    engine = get_engine()
    logger.info("Verifying database connectivity …")

    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT version();"))
            version: str = result.scalar()
            logger.info("✔  Connected to PostgreSQL: %s", version.split(",")[0])
    except OperationalError as exc:
        logger.critical(
            "✘  Cannot connect to the database.\n"
            "   Check that:\n"
            "     • The Docker container is running  (docker-compose up -d)\n"
            "     • POSTGRES_HOST / POSTGRES_PORT match the container\n"
            "     • POSTGRES_USER / POSTGRES_PASSWORD are correct\n"
            "   Original error: %s",
            exc.orig,
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# Section 2 — PostGIS extension
# ---------------------------------------------------------------------------

def _postgis_installed(conn) -> bool:
    """Return True if the PostGIS extension is already present in the database."""
    row = conn.execute(
        text(
            "SELECT COUNT(*) FROM pg_extension WHERE extname = 'postgis';"
        )
    ).scalar()
    return int(row) > 0


def ensure_postgis() -> None:
    """
    Enable the PostGIS extension if it is not already installed.

    The database role must have SUPERUSER or CREATE privilege on the database.
    In the Docker Compose setup provided with this project the default
    `postgres` role has these privileges automatically.

    PostGIS is the cornerstone of every spatial query.  Without it:
      - ST_* functions are unavailable.
      - Geometry column types are not recognised.
      - Spatial indexes cannot be created.
    """
    engine = get_engine()
    logger.info("Checking PostGIS extension …")

    with engine.begin() as conn:          # begin() auto-commits on exit
        if _postgis_installed(conn):
            # Retrieve version for informational logging
            version: str = conn.execute(
                text("SELECT PostGIS_lib_version();")
            ).scalar()
            logger.info("✔  PostGIS is already enabled (version %s).", version)
        else:
            logger.warning(
                "⚠  PostGIS extension not found — attempting to install …"
            )
            try:
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis;"))
                version = conn.execute(
                    text("SELECT PostGIS_lib_version();")
                ).scalar()
                logger.info("✔  PostGIS installed successfully (version %s).", version)
            except ProgrammingError as exc:
                logger.critical(
                    "✘  Failed to install PostGIS extension.\n"
                    "   The database role may lack SUPERUSER privilege.\n"
                    "   Connect as a superuser and run:\n"
                    "     CREATE EXTENSION IF NOT EXISTS postgis;\n"
                    "   Original error: %s",
                    exc.orig,
                )
                sys.exit(1)


# ---------------------------------------------------------------------------
# Section 3 — Table creation
# ---------------------------------------------------------------------------

def create_tables() -> None:
    """
    Create all tables declared under the shared DeclarativeBase.

    SQLAlchemy's create_all() wraps each CREATE TABLE in a
    'CREATE TABLE IF NOT EXISTS' guard, making this step safe to re-run.

    Tables are created in dependency order automatically: SQLAlchemy resolves
    foreign-key references and creates the referenced table (sensors) before
    the referencing table (water_logs).
    """
    engine = get_engine()
    logger.info("Creating database tables …")

    try:
        Base.metadata.create_all(bind=engine)
        logger.info("✔  All tables created (or already exist).")
    except Exception as exc:
        logger.critical("✘  Table creation failed: %s", exc)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Section 4 — Post-creation summary
# ---------------------------------------------------------------------------

def print_schema_summary() -> None:
    """
    Introspect the live database and print a summary of tables and indexes.

    Uses SQLAlchemy's Inspector API — no raw SQL required.
    """
    engine = get_engine()
    inspector = inspect(engine)

    tables = inspector.get_table_names()
    if not tables:
        logger.warning("No tables found in the database.")
        return

    logger.info("─" * 60)
    logger.info("Schema summary")
    logger.info("─" * 60)

    for table_name in sorted(tables):
        columns = inspector.get_columns(table_name)
        indexes = inspector.get_indexes(table_name)
        pk      = inspector.get_pk_constraint(table_name)
        fks     = inspector.get_foreign_keys(table_name)

        logger.info("\n  TABLE: %s", table_name.upper())
        logger.info("  Columns (%d):", len(columns))
        for col in columns:
            nullable = "" if col["nullable"] else " NOT NULL"
            logger.info("    %-25s %s%s", col["name"], col["type"], nullable)

        if pk and pk.get("constrained_columns"):
            logger.info("  Primary key: %s", pk["constrained_columns"])

        if fks:
            logger.info("  Foreign keys:")
            for fk in fks:
                logger.info(
                    "    %s → %s.%s",
                    fk["constrained_columns"],
                    fk["referred_table"],
                    fk["referred_columns"],
                )

        if indexes:
            logger.info("  Indexes (%d):", len(indexes))
            for idx in indexes:
                unique_flag = " [UNIQUE]" if idx["unique"] else ""
                logger.info(
                    "    %-40s columns=%s%s",
                    idx["name"],
                    idx["column_names"],
                    unique_flag,
                )

    logger.info("─" * 60)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def initialise_database() -> None:
    """
    Run the full initialisation sequence.

    This function is the single public entry point, callable from:
      - This script's __main__ block (CLI usage)
      - FastAPI's lifespan startup hook (once the API layer is built)
      - Test fixtures (after overriding the engine with a test DB)
    """
    logger.info("=" * 60)
    logger.info("  Jal-Prahari  —  Database Initialisation")
    logger.info("=" * 60)

    verify_connection()
    ensure_postgis()
    create_tables()
    print_schema_summary()

    logger.info("=" * 60)
    logger.info("  Initialisation complete ✔")
    logger.info("=" * 60)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    initialise_database()
