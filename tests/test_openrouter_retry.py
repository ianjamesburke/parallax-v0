"""Unit tests for _is_transient_network_error in openrouter.retry.

This function classifies exceptions as transient (worth retrying) or
non-transient (should propagate immediately to the fallback chain).
"""

from __future__ import annotations

import pytest

from parallax.openrouter.retry import _is_transient_network_error


# ---------------------------------------------------------------------------
# Transient — should return True
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cls_name,msg", [
    ("ReadError", ""),
    ("ReadTimeout", ""),
    ("ConnectError", ""),
    ("ConnectTimeout", ""),
    ("RemoteProtocolError", ""),
    ("SSLError", ""),
    ("SSLZeroReturnError", ""),
    ("ProtocolError", ""),
    ("IncompleteRead", ""),
    ("Timeout", ""),
])
def test_transient_by_class_name(cls_name, msg):
    exc = type(cls_name, (Exception,), {})(msg)
    assert _is_transient_network_error(exc) is True


@pytest.mark.parametrize("msg", [
    "ssl handshake failed",
    "tls error occurred",
    "connection reset by peer",
    "connection aborted",
    "service temporarily unavailable",
    "bad gateway",
    "gateway timeout",
    "service unavailable",
    "internal server error",
    "HTTP error 502",
    "got 503 from server",
    "upstream returned 504",
])
def test_transient_by_message(msg):
    exc = RuntimeError(msg)
    assert _is_transient_network_error(exc) is True


# ---------------------------------------------------------------------------
# Non-transient — should return False
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cls_name,msg", [
    ("ValueError", "invalid input"),
    ("RuntimeError", "no images returned"),
    ("HTTPStatusError", "401 Unauthorized"),
    ("HTTPStatusError", "403 Forbidden"),
    ("HTTPStatusError", "422 Unprocessable Entity"),
    ("KeyError", "model"),
    ("Exception", "safety filter triggered"),
])
def test_non_transient(cls_name, msg):
    exc = type(cls_name, (Exception,), {})(msg)
    assert _is_transient_network_error(exc) is False
