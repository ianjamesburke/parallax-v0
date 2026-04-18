"""Tests for the runtime logging module."""

from __future__ import annotations

import logging

import pytest

from parallax import log as log_module


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("PARALLAX_LOG_LEVEL", raising=False)
    # Reset the parallax root logger between tests.
    logger = logging.getLogger("parallax")
    for h in list(logger.handlers):
        logger.removeHandler(h)
    logger.setLevel(logging.WARNING)
    yield


def test_configure_default_is_warning():
    logger = log_module.configure()
    assert logger.level == logging.WARNING


def test_configure_respects_env_level(monkeypatch):
    monkeypatch.setenv("PARALLAX_LOG_LEVEL", "INFO")
    logger = log_module.configure()
    assert logger.level == logging.INFO


def test_explicit_level_beats_env(monkeypatch):
    monkeypatch.setenv("PARALLAX_LOG_LEVEL", "WARNING")
    logger = log_module.configure(logging.DEBUG)
    assert logger.level == logging.DEBUG


def test_configure_is_idempotent():
    log_module.configure(logging.INFO)
    log_module.configure(logging.INFO)
    logger = logging.getLogger("parallax")
    # Exactly one parallax-owned handler, even after multiple calls.
    owned = [h for h in logger.handlers if getattr(h, "_parallax_owned", False)]
    assert len(owned) == 1


def test_get_logger_roots_under_parallax():
    assert log_module.get_logger("tools").name == "parallax.tools"
    assert log_module.get_logger("parallax.tools").name == "parallax.tools"


def test_info_level_emits_on_stderr(capsys):
    log_module.configure(logging.INFO)
    logger = log_module.get_logger("tools")
    logger.info("tool call: generate_image(prompt='x', model='flux-pro')")
    err = capsys.readouterr().err
    assert "parallax.tools" in err
    assert "generate_image" in err


def test_warning_default_suppresses_info(capsys):
    log_module.configure()  # default WARNING
    logger = log_module.get_logger("tools")
    logger.info("should not appear")
    logger.warning("should appear")
    err = capsys.readouterr().err
    assert "should not appear" not in err
    assert "should appear" in err
