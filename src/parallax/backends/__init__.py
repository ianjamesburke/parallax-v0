"""Backend dispatcher.

Selection order: explicit `name` arg → PARALLAX_BACKEND env var → default
'claude-code'. The selected backend's check_available() is called before
returning — no silent fallback if its prereq is missing.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Protocol

from ..log import get_logger

log = get_logger("backends")

DEFAULT_BACKEND = "claude-code"
AVAILABLE_BACKENDS = ("claude-code", "anthropic-api")


class BackendModule(Protocol):
    NAME: str
    run: Callable[..., dict[str, Any]]
    check_available: Callable[[], None]


def select(name: str | None = None) -> BackendModule:
    backend_name = name or os.environ.get("PARALLAX_BACKEND") or DEFAULT_BACKEND
    if backend_name == "claude-code":
        from . import claude_code as backend
    elif backend_name == "anthropic-api":
        from . import anthropic_api as backend
    else:
        raise ValueError(
            f"Unknown backend: {backend_name!r}. Must be one of {AVAILABLE_BACKENDS}."
        )
    backend.check_available()
    log.info("backend selected: %s", backend.NAME)
    return backend  # type: ignore[return-value]


def run(
    brief: str,
    session_id: str | None = None,
    *,
    backend: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    return select(backend).run(brief, session_id=session_id, **kwargs)
