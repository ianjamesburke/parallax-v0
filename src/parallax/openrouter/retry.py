"""Transient network error classification for OpenRouter retry logic.

`_with_fallback` and `_call_with_transient_retry` live in __init__ so that
module-level names (like `_record_usage`, `runlog`) are always in scope and
monkeypatching the package namespace works correctly in tests.

This module contains only the pure, stateless helper that classifies whether
an exception is worth retrying — easy to unit-test in isolation.
"""

from __future__ import annotations


def _is_transient_network_error(e: Exception) -> bool:
    """True for TLS / connection / read / 5xx errors worth retrying.

    Match by class-name + message rather than importing httpx types — the
    concrete class can shift across versions and call layers. False for
    auth (401/403), validation, safety, and "no images returned" errors —
    those won't be fixed by waiting.
    """
    name = type(e).__name__
    msg = str(e).lower()
    transient_class_markers = (
        "ReadError", "ReadTimeout", "ConnectError", "ConnectTimeout",
        "RemoteProtocolError", "SSLError", "SSLZeroReturnError",
        "ProtocolError", "IncompleteRead", "Timeout",
    )
    if any(m in name for m in transient_class_markers):
        return True
    transient_msg_markers = (
        "ssl", "tls", "connection reset", "connection aborted",
        "temporarily unavailable", "bad gateway", "gateway timeout",
        "service unavailable", "internal server error",
        " 502", " 503", " 504",
    )
    return any(m in msg for m in transient_msg_markers)
