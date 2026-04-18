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


def test_auto_selects_claude_code_when_cli_present(monkeypatch):
    """No explicit pick, claude CLI present → prefer claude-code over anthropic-api."""
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/claude" if name == "claude" else None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")  # both are set; claude-code wins
    selected = backends.select()
    assert selected.NAME == "claude-code"


def test_auto_falls_back_to_anthropic_api_when_no_claude_cli(monkeypatch):
    """No explicit pick, no claude CLI, but ANTHROPIC_API_KEY is set → anthropic-api."""
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _name: None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    selected = backends.select()
    assert selected.NAME == "anthropic-api"


def test_auto_fails_with_helpful_message_when_neither_available(monkeypatch):
    """No claude CLI AND no ANTHROPIC_API_KEY → raise listing both setup paths."""
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _name: None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="No backend available"):
        backends.select()


def test_explicit_claude_code_does_not_auto_fall_back(monkeypatch):
    """--backend claude-code must hard-fail if the CLI is missing, even if
    ANTHROPIC_API_KEY is set. Never silently override an explicit choice."""
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _name: None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    with pytest.raises(RuntimeError, match="claude` CLI"):
        backends.select("claude-code")
