"""Runtime logging for parallax.

Stderr-only; session NDJSON and the Claude SDK's own jsonl transcript are the
persistent records. This module is for live debugging: what backend got
picked, what the agent decided to call, what came back.

Levels:
- WARNING (default): quiet — only warnings and errors on stderr.
- INFO (-v, PARALLAX_LOG_LEVEL=INFO): backend selected, each tool call,
  each tool result, SDK transcript location.
- DEBUG (-vv, PARALLAX_LOG_LEVEL=DEBUG): full tool args, full tool results,
  every SDK message type.

WARNING+ events are also bridged to the active run.log as structured JSONL
via _RunlogHandler, so agents can detect warnings after the fact by grepping
for `"level": "WARNING"` in run.log.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys

_ROOT_LOGGER_NAME = "parallax"
_DEFAULT_LEVEL = "WARNING"
_FORMAT = "%(asctime)s %(levelname)-5s %(name)s: %(message)s"

_LEVEL_MAP = {
    logging.WARNING: "WARNING",
    logging.ERROR: "ERROR",
    logging.CRITICAL: "ERROR",
}


class _RunlogHandler(logging.Handler):
    """Bridges WARNING+ log records into the active run.log as structured events."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            from . import runlog
            runlog_level = _LEVEL_MAP.get(record.levelno, "WARNING")
            module = record.name.removeprefix(_ROOT_LOGGER_NAME + ".")
            event_name = f"{module}.warn" if module != record.name else "warn"
            runlog.event(event_name, level=runlog_level, msg=self.format(record))
        except Exception:
            pass


def _resolve_level(explicit: str | int | None) -> int:
    if explicit is not None:
        return (
            explicit
            if isinstance(explicit, int)
            else logging.getLevelNamesMapping().get(explicit.upper(), logging.WARNING)
        )
    env = os.environ.get("PARALLAX_LOG_LEVEL")
    if env:
        return logging.getLevelNamesMapping().get(env.upper(), logging.WARNING)
    return logging.getLevelNamesMapping()[_DEFAULT_LEVEL]


def configure(level: str | int | None = None) -> logging.Logger:
    """Install a stderr handler on the parallax logger at the given level.

    Also installs a runlog bridge handler at WARNING level so all WARNING+
    events appear as structured JSONL in run.log when a run is active.

    Idempotent — calling again replaces the existing parallax-owned handlers.
    Does not touch the root logger or other libraries' loggers.
    """
    logger = logging.getLogger(_ROOT_LOGGER_NAME)
    resolved = _resolve_level(level)
    logger.setLevel(resolved)
    logger.propagate = False

    for h in list(logger.handlers):
        if getattr(h, "_parallax_owned", False) or getattr(h, "_parallax_runlog", False):
            logger.removeHandler(h)

    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setLevel(resolved)
    handler.setFormatter(logging.Formatter(_FORMAT, datefmt="%H:%M:%S"))
    handler._parallax_owned = True  # type: ignore[attr-defined]
    logger.addHandler(handler)

    runlog_handler = _RunlogHandler()
    runlog_handler.setLevel(logging.WARNING)
    runlog_handler.setFormatter(logging.Formatter("%(message)s"))
    runlog_handler._parallax_runlog = True  # type: ignore[attr-defined]
    logger.addHandler(runlog_handler)

    return logger


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the parallax root (e.g. 'backends.claude_code')."""
    if name.startswith(_ROOT_LOGGER_NAME + "."):
        return logging.getLogger(name)
    return logging.getLogger(f"{_ROOT_LOGGER_NAME}.{name}")
