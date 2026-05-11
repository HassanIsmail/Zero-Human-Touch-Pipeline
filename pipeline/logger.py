"""
pipeline/logger.py

Shared logging configuration for the Zero Human Touch Pipeline.
Creates a logs/ directory at the project root and logs to both
console (INFO) and a rotating daily file (DEBUG).
"""

import logging
import os
from logging.handlers import TimedRotatingFileHandler
from datetime import datetime

# Resolve the project root relative to this file's location.
# This file lives at <project_root>/pipeline/logger.py, so one level up is root.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOGS_DIR = os.path.join(_PROJECT_ROOT, "logs")

# Create the logs directory at import time so every caller can use it immediately.
os.makedirs(_LOGS_DIR, exist_ok=True)

# Build the log file path.  The base name uses today's date; the rotating handler
# will suffix older files with the date when it rolls over.
_LOG_FILENAME = os.path.join(
    _LOGS_DIR,
    "pipeline-{}.log".format(datetime.now().strftime("%Y%m%d")),
)

# Track whether the root handler has already been configured so that repeated
# calls to get_logger() within the same process do not add duplicate handlers.
_configured = False


def _configure_root_logger() -> None:
    """Configure the root logger with a console handler and a file handler.

    This function is idempotent — it does nothing if the root logger already
    has handlers attached.
    """
    global _configured
    if _configured:
        return

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)  # Allow all levels; handlers will filter.

    # ------------------------------------------------------------------
    # Console handler — INFO and above, human-readable format.
    # ------------------------------------------------------------------
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_fmt = logging.Formatter(
        fmt="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_handler.setFormatter(console_fmt)

    # ------------------------------------------------------------------
    # File handler — DEBUG and above, rotates every midnight, keeps 30 days.
    # ------------------------------------------------------------------
    file_handler = TimedRotatingFileHandler(
        filename=_LOG_FILENAME,
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
        utc=True,
    )
    file_handler.setLevel(logging.DEBUG)
    file_fmt = logging.Formatter(
        fmt="%(asctime)s  %(levelname)-8s  %(name)s  [%(filename)s:%(lineno)d] — %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    file_handler.setFormatter(file_fmt)

    root.addHandler(console_handler)
    root.addHandler(file_handler)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a named logger, ensuring the shared handlers are in place.

    Args:
        name: Typically ``__name__`` of the calling module.

    Returns:
        A :class:`logging.Logger` instance ready for use.
    """
    _configure_root_logger()
    return logging.getLogger(name)
