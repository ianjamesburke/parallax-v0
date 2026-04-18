"""Dispatcher tests — route selection and fail-fast on missing prereqs."""

from __future__ import annotations

import pytest

from parallax import backends


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("PARALLAX_BACKEND", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    yield


def test_explicit_name_overrides_env(monkeypatch):
    monkeypatch.setenv("PARALLAX_BACKEND", "claude-code")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    # Force the anthropic-api backend even though env says claude-code.
    selected = backends.select("anthropic-api")
    assert selected.NAME == "anthropic-api"


def test_env_var_overrides_default(monkeypatch):
    monkeypatch.setenv("PARALLAX_BACKEND", "anthropic-api")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    selected = backends.select()
    assert selected.NAME == "anthropic-api"


def test_unknown_backend_name_raises():
    with pytest.raises(ValueError, match="Unknown backend"):
        backends.select("openai")


def test_anthropic_api_fails_fast_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        backends.select("anthropic-api")


def test_claude_code_fails_fast_without_cli(monkeypatch):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _name: None)
    with pytest.raises(RuntimeError, match="claude` CLI"):
        backends.select("claude-code")


def test_default_is_claude_code(monkeypatch):
    """Default backend (no args, no env) resolves to claude-code — even if the
    CLI isn't present, the resolution must attempt claude-code first."""
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _name: None)
    with pytest.raises(RuntimeError, match="claude` CLI"):
        backends.select()
