"""Single place that configures the application's error log.

``logs/app_errors.log`` is the debugging trail for provider/API failures: the
terminal shows a short, actionable message while the log keeps the raw wire
format.  Configuration is lazy and idempotent, so a utility path that never
imports an agent (e.g. ``session compress --summarize``) still logs somewhere
useful instead of falling back to stderr.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_PATH = LOG_DIR / "app_errors.log"

_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"

# Guards against adding the handler twice (module imported from several paths).
_configured = False


def configure_error_logging() -> None:
    """Attach the error-log file handler once; later calls are no-ops.

    Never raises: a read-only checkout or a locked log file must not turn a
    provider error into a crash.
    """
    global _configured
    if _configured:
        return
    _configured = True

    root = logging.getLogger()
    if any(getattr(handler, "_basic_agents_file", False) for handler in root.handlers):
        return

    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(LOG_PATH, mode="a", encoding="utf-8")
    except OSError:
        return

    handler._basic_agents_file = True
    handler.setFormatter(logging.Formatter(_FORMAT))
    # Mirror ``logging.basicConfig``: only take over the level when we are the
    # first handler, so a caller's DEBUG configuration is not clobbered.
    if not root.handlers:
        root.setLevel(logging.ERROR)
    root.addHandler(handler)


def log_path_display() -> str:
    """Log path relative to the current directory when possible (for hints)."""
    try:
        return os.path.relpath(LOG_PATH)
    except (OSError, ValueError):
        return str(LOG_PATH)
