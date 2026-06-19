# backend/app/core/logger.py
#
# ─── Responsibility ────────────────────────────────────────────────────────────
# Provide a single, reusable factory function that returns a fully configured
# Python `logging.Logger` instance.
#
# Design decisions:
#
#   1. RotatingFileHandler (not FileHandler)
#      Plain FileHandler files grow forever and fill disks silently.
#      RotatingFileHandler caps each file at `max_bytes` and keeps `backup_count`
#      historical files, giving you 50 MB of log history at the cost of 50 MB of
#      disk — predictable and safe for production deployments.
#
#   2. Two separate handlers — file + console
#      • File handler: full ISO-8601 timestamp + level + logger name + message.
#        This format is machine-parseable by tools like Loki, Splunk, or grep.
#      • Console handler: abbreviated format without the date, for quick
#        developer feedback during local runs.
#
#   3. Logger-name namespacing
#      Each module calls get_logger(__name__) which produces a logger named
#      "app.core.dem_parser", "app.core.ingestion", etc.  This lets you filter
#      logs by component in any log aggregator without code changes.
#
#   4. Idempotent handler attachment
#      Python's logging module is global.  If two modules import the same logger,
#      calling basicConfig() twice doubles every log line.  The guard
#      `if not logger.handlers` prevents duplicate handlers across repeated
#      imports or test reloads.
#
#   5. logs/ directory auto-creation
#      The directory is created with parents=True, exist_ok=True so the module
#      works in a fresh checkout or inside a Docker container without any
#      manual setup step.
# ──────────────────────────────────────────────────────────────────────────────

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants — centralise tunables so they are easy to find and change
# ---------------------------------------------------------------------------

# Resolve the logs directory relative to this file's location so the path is
# always correct regardless of the working directory the process was started from.
#
# __file__ = .../backend/app/core/logger.py
# .parents[2]  = .../backend/
_BACKEND_ROOT: Path = Path(__file__).resolve().parents[2]
LOGS_DIR: Path = _BACKEND_ROOT / "logs"

LOG_FILE_NAME: str   = "dem_parser.log"
LOG_FILE_PATH: Path  = LOGS_DIR / LOG_FILE_NAME

MAX_BYTES: int       = 10 * 1024 * 1024   # 10 MB per file
BACKUP_COUNT: int    = 5                   # keep 5 rotated files → up to 50 MB history

# Full format for the file handler — machine-parseable
FILE_FORMAT: str = (
    "%(asctime)s | %(levelname)-8s | %(name)s | %(funcName)s:%(lineno)d | %(message)s"
)

# Abbreviated format for the console — human-readable
CONSOLE_FORMAT: str = "%(levelname)-8s  %(name)s — %(message)s"

DATE_FORMAT: str = "%Y-%m-%dT%H:%M:%S"


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------

def get_logger(name: str, level: int = logging.DEBUG) -> logging.Logger:
    """
    Return a configured :class:`logging.Logger` instance for *name*.

    Parameters
    ----------
    name : str
        Typically ``__name__`` from the calling module, e.g.
        ``"app.core.dem_parser"``.
    level : int
        The minimum severity level that this logger will emit.
        Defaults to ``logging.DEBUG`` so that all messages reach the handlers;
        individual handlers are configured with their own level thresholds.

    Returns
    -------
    logging.Logger
        A logger with:
        - A :class:`RotatingFileHandler` writing to ``logs/dem_parser.log``
          at **DEBUG** level and above.
        - A :class:`StreamHandler` (stdout) writing at **INFO** level and above.

    Notes
    -----
    The function is idempotent: calling it multiple times with the same *name*
    does not duplicate handlers on the logger.

    Examples
    --------
    >>> from app.core.logger import get_logger
    >>> log = get_logger(__name__)
    >>> log.info("DEM parser initialised")
    INFO     app.core.dem_parser — DEM parser initialised
    """

    # Ensure the logs directory exists before any handler tries to open the file
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(name)

    # Guard: do not add duplicate handlers on repeated imports or test reloads
    if logger.handlers:
        return logger

    logger.setLevel(level)

    # ── Handler 1: Rotating file ─────────────────────────────────────────────
    file_handler = RotatingFileHandler(
        filename=LOG_FILE_PATH,
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)           # capture everything in the file
    file_handler.setFormatter(
        logging.Formatter(fmt=FILE_FORMAT, datefmt=DATE_FORMAT)
    )

    # ── Handler 2: Console (stdout) ──────────────────────────────────────────
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)         # only INFO and above on screen
    console_handler.setFormatter(
        logging.Formatter(fmt=CONSOLE_FORMAT)
    )

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    # Prevent log records from bubbling up to the root logger and being printed
    # a second time if the root logger also has handlers configured.
    logger.propagate = False

    return logger
