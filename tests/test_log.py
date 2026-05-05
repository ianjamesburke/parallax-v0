"""Tests for the runtime logging module."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from parallax import log as log_module
from parallax import runlog


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("PARALLAX_LOG_LEVEL", raising=False)
    # Reset the parallax root logger between tests.
    logger = logging.getLogger("parallax")
    for h in list(logger.handlers):
        logger.removeHandler(h)
    logger.setLevel(logging.WARNING)
    yield


@pytest.fixture()
def active_run(tmp_path):
    """Start a runlog run bound to a temp dir; tear it down after the test."""
    runlog.start_run()
    runlog.bind_output_dir(tmp_path)
    yield tmp_path
    runlog.end_run()


def _read_runlog(out_dir: Path) -> list[dict]:
    lines = (out_dir / "run.log").read_text().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


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
    owned = [h for h in logger.handlers if getattr(h, "_parallax_owned", False)]
    runlog_h = [h for h in logger.handlers if getattr(h, "_parallax_runlog", False)]
    assert len(owned) == 1
    assert len(runlog_h) == 1


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


def test_warning_bridges_to_runlog(active_run):
    log_module.configure()
    logger = log_module.get_logger("whisper_backend")
    logger.warning("whisper_backend: whisperx not installed — using faster-whisper")
    events = _read_runlog(active_run)
    warn_events = [e for e in events if e.get("level") == "WARNING"]
    assert warn_events, "no WARNING events found in run.log"
    assert any("whisper_backend" in e.get("event", "") for e in warn_events)
    assert any("faster-whisper" in e.get("msg", "") for e in warn_events)


def test_warning_no_run_active_does_not_crash():
    log_module.configure()
    logger = log_module.get_logger("assembly")
    # No active run — handler must be silent, not raise.
    logger.warning("Scene 1 needs 5 words but only 3 remain")


def test_error_bridges_to_runlog_as_error(active_run):
    log_module.configure()
    logger = log_module.get_logger("assembly")
    logger.error("assembly: unrecoverable failure")
    events = _read_runlog(active_run)
    error_events = [e for e in events if e.get("level") == "ERROR"]
    assert error_events, "no ERROR events found in run.log"
    assert any("unrecoverable" in e.get("msg", "") for e in error_events)


def test_runlog_event_name_uses_module(active_run):
    log_module.configure()
    log_module.get_logger("assembly").warning("still not found")
    events = _read_runlog(active_run)
    warn_events = [e for e in events if e.get("level") == "WARNING"]
    assert any(e.get("event") == "assembly.warn" for e in warn_events)
