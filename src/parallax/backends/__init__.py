"""Backend dispatcher.

Selection order:
  1. Explicit `name` arg (CLI --backend) — hard-fail if its prereq is missing.
  2. PARALLAX_BACKEND env var — hard-fail if its prereq is missing.
  3. Auto-detect: prefer claude-code if the `claude` CLI is available,
     otherwise fall back to anthropic-api if ANTHROPIC_API_KEY is set.
     If neither is available, fail with a message listing both setup paths.

Explicit picks never silently downgrade; only the implicit/default path
auto-falls-back. This is the "works out of the box if you have either auth
path" ergonomic, without overriding what the caller explicitly asked for.
"""

from __future__ import annotations

import os
import shutil
from typing import Any, Callable, Protocol

from ..log import get_logger

log = get_logger("backends")

DEFAULT_BACKEND = "claude-code"
AVAILABLE_BACKENDS = ("claude-code", "anthropic-api")


class BackendModule(Protocol):
    NAME: str
    run: Callable[..., dict[str, Any]]
    check_available: Callable[[], None]


def _load(backend_name: str) -> BackendModule:
    if backend_name == "claude-code":
        from . import claude_code as backend
    elif backend_name == "anthropic-api":
        from . import anthropic_api as backend
    else:
        raise ValueError(
            f"Unknown backend: {backend_name!r}. Must be one of {AVAILABLE_BACKENDS}."
        )
    return backend  # type: ignore[return-value]


def _auto_select() -> BackendModule:
    """Probe available backends and pick one, or raise with a helpful message."""
    if shutil.which("claude") is not None:
        log.info("auto-selecting claude-code backend (claude CLI found)")
        return _load("claude-code")
    if os.environ.get("ANTHROPIC_API_KEY"):
        log.info(
            "auto-selecting anthropic-api backend "
            "(claude CLI not on PATH; ANTHROPIC_API_KEY is set)"
        )
        return _load("anthropic-api")
    raise RuntimeError(
        "No backend available. Either:\n"
        "  - install the Claude Code CLI (https://claude.com/claude-code) "
        "and log in, OR\n"
        "  - export ANTHROPIC_API_KEY=... to use the raw API backend."
    )


def select(name: str | None = None) -> BackendModule:
    # Explicit caller choice — use it, hard-fail on missing prereq.
    if name is not None:
        backend = _load(name)
        backend.check_available()
        log.info("backend selected (explicit): %s", backend.NAME)
        return backend
    # Env-var choice is also explicit from the user's perspective.
    env_choice = os.environ.get("PARALLAX_BACKEND")
    if env_choice:
        backend = _load(env_choice)
        backend.check_available()
        log.info("backend selected (PARALLAX_BACKEND): %s", backend.NAME)
        return backend
    # No explicit pick — probe and fall back.
    backend = _auto_select()
    backend.check_available()
    return backend


def run(
    brief: str,
    session_id: str | None = None,
    *,
    backend: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    return select(backend).run(brief, session_id=session_id, **kwargs)
