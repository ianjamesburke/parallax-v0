"""HTTP client primitives and credits checking for OpenRouter.

Single source of truth for endpoint URL, auth headers, and key handling.
Every callsite that hits OpenRouter goes through these helpers.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx

_BASE = "https://openrouter.ai/api/v1"
_BASE_HEADERS = {
    "HTTP-Referer": "https://github.com/ianjamesburke/parallax-v0",
    "X-Title": "parallax",
}


def _check_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is required for real-mode media calls. "
            "Set it, or export PARALLAX_TEST_MODE=1 to use stubs."
        )
    return key


def _auth_headers() -> dict[str, str]:
    return {
        **_BASE_HEADERS,
        "Authorization": f"Bearer {_check_key()}",
        "Content-Type": "application/json",
    }


def _post(path: str, body: dict, *, timeout: float = 300.0) -> "httpx.Response":
    """POST JSON to <_BASE>/<path>. Returns the raw response."""
    import httpx
    return httpx.post(f"{_BASE}{path}", headers=_auth_headers(), json=body, timeout=timeout)


def _get(path: str, *, timeout: float = 30.0, auth_only: bool = False) -> "httpx.Response":
    """GET <_BASE>/<path>. `auth_only` strips Content-Type from headers
    (some GETs to OpenRouter / signed-URL fetches reject it)."""
    import httpx
    headers = _auth_headers()
    if auth_only:
        headers.pop("Content-Type", None)
    return httpx.get(f"{_BASE}{path}", headers=headers, timeout=timeout)


def _stream_post(path: str, body: dict, *, timeout: float = 300.0):
    """Stream a POST to <_BASE>/<path>. Caller uses it as a context manager
    (`with _stream_post(...) as resp: ...`) and iterates resp.iter_lines()."""
    import httpx
    return httpx.stream(
        "POST", f"{_BASE}{path}", headers=_auth_headers(), json=body, timeout=timeout,
    )


class InsufficientCreditsError(RuntimeError):
    """Raised when OpenRouter rejects a call with HTTP 402 or pre-flight credits
    check shows balance below the minimum. Carries the top-up URL so the CLI
    layer can render a clean operator-facing error.
    """


@dataclass(frozen=True)
class CreditsBalance:
    total: float
    used: float
    remaining: float


def _raise_for_credits_or_status(resp: "httpx.Response") -> None:
    """Specialized response check: 402 → InsufficientCreditsError (no retries,
    no fallback), other 4xx/5xx → standard `HTTPStatusError` for the caller's
    retry/fallback machinery to inspect.
    """
    if resp.status_code == 402:
        try:
            msg = (resp.json().get("error") or {}).get("message", "")
        except Exception:
            msg = resp.text[:200]
        raise InsufficientCreditsError(
            f"OpenRouter rejected with 402: {msg or 'Insufficient credits'}. "
            f"Top up at https://openrouter.ai/settings/credits"
        )
    resp.raise_for_status()


def _strip_or_prefix(model_id: str) -> str:
    """Strip the leading `openrouter/` namespace from a pricing model_id.

    pricing.py prefixes everything with `openrouter/` so the dispatcher can
    tell at a glance which backend a row belongs to. The OpenRouter API
    itself takes plain `<vendor>/<model>` slugs.
    """
    return model_id[len("openrouter/"):] if model_id.startswith("openrouter/") else model_id


def check_credits(min_balance_usd: float = 0.50) -> CreditsBalance:
    """Pre-flight credits check. Hits /api/v1/credits and raises
    `InsufficientCreditsError` when remaining balance is below `min_balance_usd`.

    Returns the `CreditsBalance` on success so callers can log the headroom.

    `min_balance_usd` default 0.50 covers a 4-scene produce run (~$1-2 worth
    of stills + i2v + TTS); raise it for longer projects. Set to 0.0 to make
    the check informational only.
    """
    resp = _get("/credits", timeout=15.0)
    _raise_for_credits_or_status(resp)
    data = resp.json().get("data") or {}
    total = float(data.get("total_credits", 0.0))
    used = float(data.get("total_usage", 0.0))
    remaining = total - used
    balance = CreditsBalance(total=total, used=used, remaining=remaining)
    if remaining < min_balance_usd:
        raise InsufficientCreditsError(
            f"OpenRouter credits ${remaining:.2f} (below threshold "
            f"${min_balance_usd:.2f}). Total ${total:.2f}, used ${used:.2f}. "
            f"Top up at https://openrouter.ai/settings/credits"
        )
    return balance
