"""ContextVar that carries the session ID down to tool handlers.

`produce.run_plan` sets this before the pipeline starts; `tools.generate_image`
reads it when writing a usage record. This avoids threading session_id through
every tool signature.
"""

from __future__ import annotations

from contextvars import ContextVar

current_session_id: ContextVar[str | None] = ContextVar("parallax_session_id", default=None)
