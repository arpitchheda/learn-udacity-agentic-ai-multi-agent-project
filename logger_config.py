"""
logger_config.py — Centralized logging setup for Munder Difflin multi-agent system.

Call setup_logging() once at startup. After that, any module can do:
    from logger_config import get_logger
    logger = get_logger(__name__)
"""

import logging
import sys
from pathlib import Path

LOG_FILE = "run_output.log"
_configured = False


def setup_logging(log_file: str = LOG_FILE, level: int = logging.DEBUG) -> None:
    """
    Configure root logger with a file handler (DEBUG) and console handler (INFO).

    The file gets every DEBUG message with timestamps; the console shows INFO+
    so the terminal stays readable during a run.

    Args:
        log_file: Path to the log file (created/overwritten each run).
        level:    Root logger level (default DEBUG).
    """
    global _configured
    if _configured:
        return
    _configured = True

    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)-8s] %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # File handler — DEBUG and above, UTF-8, overwrite each run
    fh = logging.FileHandler(log_file, mode="w", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)

    # Console handler — INFO and above (keeps terminal concise)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("%(message)s"))

    # Silence ALL third-party noise at the root level, then whitelist only
    # project loggers at the requested level.
    root = logging.getLogger()
    root.setLevel(logging.WARNING)
    root.addHandler(fh)
    root.addHandler(ch)

    # Only our own loggers get verbose output — covers project_starter, agents.*, db_helpers
    for name in ("project_starter", "agents", "db_helpers"):
        logging.getLogger(name).setLevel(level)


def get_logger(name: str) -> logging.Logger:
    """Return a named logger. Call setup_logging() first."""
    return logging.getLogger(name)
