# backend/app/database/connection.py
#
# Responsibility: Build the SQLAlchemy engine and session factory from
# environment variables. Nothing here knows about specific tables or models —
# that separation keeps the module independently testable.
#
# Design decisions:
#   - pool_pre_ping=True:  Detects stale TCP connections before handing them
#                          to application code.  Essential for long-running
#                          containers where the DB may have restarted.
#   - pool_size / max_overflow: Conservative defaults suitable for a single
#                          FastAPI worker; raise these when deploying with
#                          Gunicorn + multiple workers.
#   - echo=False in prod:  SQL logging is controlled by LOG_LEVEL env var
#                          so it is never accidentally left on in production.

import os
import logging
from functools import lru_cache

from dotenv import load_dotenv
from sqlalchemy import create_engine, Engine, text
from sqlalchemy.orm import sessionmaker, Session

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

# Load .env (or .env.development, etc.) from the project root.
# load_dotenv is idempotent; calling it multiple times is safe.
load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_database_url() -> str:
    """
    Construct the PostgreSQL DSN from individual environment variables.

    Using individual variables instead of a single DATABASE_URL string lets
    Docker Compose, Kubernetes Secrets, and the .env file each set only the
    pieces they own (e.g. a K8s Secret for the password, a ConfigMap for the
    host) without re-building a composite string in infra code.
    """
    user     = os.environ["POSTGRES_USER"]
    password = os.environ["POSTGRES_PASSWORD"]
    host     = os.environ.get("POSTGRES_HOST", "localhost")
    port     = os.environ.get("POSTGRES_PORT", "5432")
    db       = os.environ["POSTGRES_DB"]

    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db}"


# ---------------------------------------------------------------------------
# Engine (module-level singleton via lru_cache)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """
    Return a module-level singleton SQLAlchemy Engine.

    lru_cache ensures this expensive object is created exactly once per
    process lifetime, while still being lazily initialised (i.e., the engine
    is not created at import time, only on first call).

    Args:
        None — all configuration is read from the environment.

    Returns:
        A configured SQLAlchemy Engine connected to the PostGIS database.
    """
    url = _build_database_url()

    engine = create_engine(
        url,
        # -------------------------------------------------------------------
        # Connection pool settings
        # -------------------------------------------------------------------
        pool_pre_ping=True,        # validates connections before use
        pool_size=5,               # connections kept alive in the pool
        max_overflow=10,           # extra connections allowed under burst load
        pool_timeout=30,           # seconds to wait for a free connection
        pool_recycle=1800,         # recycle connections every 30 min to avoid
                                   # hitting PostgreSQL's idle_in_transaction
                                   # session timeout
        # -------------------------------------------------------------------
        # Logging
        # -------------------------------------------------------------------
        echo=(os.getenv("LOG_LEVEL", "INFO").upper() == "DEBUG"),
    )

    logger.info("SQLAlchemy engine created — host=%s db=%s",
                os.environ.get("POSTGRES_HOST", "localhost"),
                os.environ["POSTGRES_DB"])

    return engine


# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------

def get_session_factory() -> sessionmaker[Session]:
    """
    Return a configured SQLAlchemy sessionmaker bound to the singleton engine.

    The session factory is intentionally NOT a module-level singleton so that
    tests can easily swap in a different engine via get_engine.cache_clear().
    """
    return sessionmaker(
        bind=get_engine(),
        autocommit=False,   # explicit transaction control — never rely on
                            # auto-commit in application code
        autoflush=False,    # flush only when we choose to, avoiding
                            # unexpected queries inside a request cycle
        expire_on_commit=False,  # keep attribute values accessible after
                                 # commit without re-querying; critical for
                                 # async/background task patterns
    )
