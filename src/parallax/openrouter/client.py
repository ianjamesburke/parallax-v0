"""HTTP client primitives and credits checking for OpenRouter.

Single source of truth for endpoint URL, auth headers, and key handling.
Every callsite that hits OpenRouter goes through these helpers.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx

_BASE = "https://openrouter.ai/api/v1"
_BASE_HEADERS = {
    "HTTP-Referer": "https://github.com/ianjamesburke/parallax-v0",
    "X-Title": "parallax",
}


@dataclass
class CostScope:
    """Cost evidence gathered during one generation call.

    `billed`: USD OpenRouter reported charging (`usage.cost`).
    `estimated`: USD computed from the catalog, for endpoints whose response
    carries no usage (e.g. /audio/speech).
    """
    billed: list[float] = field(default_factory=list)
    estimated: list[float] = field(default_factory=list)
    # OpenRouter generation ids (`gen-...`) for reconciling against /api/v1/generation.
    generation_ids: list[str] = field(default_factory=list)


_cost_scope: ContextVar[CostScope | None] = ContextVar("parallax_cost_scope", default=None)


@contextmanager
def capture_cost() -> Iterator[CostScope]:
    scope = CostScope()
    token = _cost_scope.set(scope)
    try:
        yield scope
    finally:
        _cost_scope.reset(token)


def note_billed_cost(payload: dict) -> None:
    """Record `payload["usage"]["cost"]` into the open scope, if both exist."""
    scope = _cost_scope.get()
    cost = (payload.get("usage") or {}).get("cost")
    if scope is not None and cost is not None:
        scope.billed.append(float(cost))


def note_generation_id(generation_id: str | None) -> None:
    scope = _cost_scope.get()
    if scope is not None and generation_id and generation_id not in scope.generation_ids:
        scope.generation_ids.append(generation_id)


def note_estimated_cost(usd: float) -> None:
    scope = _cost_scope.get()
    if scope is not None:
        scope.estimated.append(usd)


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


_OPENROUTER_HOST = "openrouter.ai"


class UntrustedURLError(RuntimeError):
    """Raised instead of sending the OpenRouter key to a non-OpenRouter URL."""


def _safe_url_for_message(url: object) -> str:
    """Scheme + host + path only: never echo query strings (signed tokens) or userinfo."""
    try:
        import httpx
        parsed = httpx.URL(str(url))
        return f"{parsed.scheme}://{parsed.host}{parsed.path}"
    except Exception:
        return "<unparseable url>"


def is_openrouter_url(url: object) -> bool:
    """True only for ``https://openrouter.ai/...`` or ``https://<sub>.openrouter.ai/...``.

    This is the single gate for attaching the bearer token to an absolute URL
    that came from a server response or a job receipt. Parsed with httpx (the
    same parser that sends the request) so the checked host is the contacted host.
    Rejects http, userinfo (``https://openrouter.ai@evil.com``), non-default
    ports, look-alikes (``openrouter.ai.evil.com``) and query-string tricks.
    """
    if not isinstance(url, str) or not url or any(c.isspace() or c == "\\" for c in url):
        return False
    try:
        import httpx
        parsed = httpx.URL(url)
    except Exception:
        return False
    if parsed.scheme != "https" or parsed.userinfo or parsed.port not in (None, 443):
        return False
    host = (parsed.host or "").lower().rstrip(".")
    return host == _OPENROUTER_HOST or host.endswith("." + _OPENROUTER_HOST)


def _openrouter_get(url: str, *, timeout: float) -> "httpx.Response":
    """GET an absolute OpenRouter URL with the bearer token (no Content-Type).

    Refuses (UntrustedURLError) before any request if the URL is not on the
    OpenRouter allowlist, so a call site cannot leak the key by forgetting a check.
    """
    import httpx
    if not is_openrouter_url(url):
        raise UntrustedURLError(
            f"refusing to send OpenRouter credentials to non-OpenRouter URL {_safe_url_for_message(url)}"
        )
    return httpx.get(url, headers={"Authorization": _auth_headers()["Authorization"]}, timeout=timeout)


def _download_url(url: str, *, timeout: float) -> "httpx.Response":
    """Download a provider result URL, attaching the key only for OpenRouter hosts.

    CDN / signed URLs elsewhere are fetched with no Authorization header. If such
    a URL answers 401/403 (i.e. claims it needs credentials) we refuse rather
    than retry with the OpenRouter key.
    """
    import httpx
    if is_openrouter_url(url):
        return _openrouter_get(url, timeout=timeout)
    response = httpx.get(url, headers={}, timeout=timeout)
    if response.status_code in (401, 403):
        raise UntrustedURLError(
            f"download URL {_safe_url_for_message(url)} requires credentials but is not an "
            "OpenRouter host; refusing to send the OpenRouter key"
        )
    return response


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
