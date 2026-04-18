"""ContextVars that carry loop-scoped state down to tool handlers.

Both backends set these before entering their agent loop; `tools.generate_image`
reads them when writing a usage record. This avoids threading session_id and
backend_name through every tool signature — which matters especially on the
claude-code path where the MCP tool handler is a closure defined before the
session_id is known.
"""

from __future__ import annotations

from contextvars import ContextVar

current_session_id: ContextVar[str | None] = ContextVar("parallax_session_id", default=None)
current_backend: ContextVar[str] = ContextVar("parallax_backend", default="unknown")
