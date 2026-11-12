"""Headless Chrome is sandboxed unless the operator explicitly opts out."""

from __future__ import annotations

import inspect

import pytest

from parallax import chrome, html_motion, storyboard_package
from parallax.chrome import NO_SANDBOX_ENV, chrome_launch_args


@pytest.fixture(autouse=True)
def _no_opt_out(monkeypatch):
    monkeypatch.delenv(NO_SANDBOX_ENV, raising=False)


def test_default_launch_args_are_sandboxed(capsys):
    args = chrome_launch_args("/usr/bin/chrome", "--headless=new", "--disable-gpu")
    assert args == ["/usr/bin/chrome", "--headless=new", "--disable-gpu"]
    assert "--no-sandbox" not in args
    assert capsys.readouterr().err == ""


def test_default_stays_sandboxed_as_root(monkeypatch):
    monkeypatch.setattr("os.geteuid", lambda: 0, raising=False)
    assert "--no-sandbox" not in chrome_launch_args("/usr/bin/chrome", "--headless")


@pytest.mark.parametrize("value", ["", "0", "false", "no"])
def test_non_truthy_opt_out_values_keep_sandbox(monkeypatch, value):
    monkeypatch.setenv(NO_SANDBOX_ENV, value)
    assert "--no-sandbox" not in chrome_launch_args("/usr/bin/chrome", "--headless")


@pytest.mark.parametrize("value", ["1", "true", "YES"])
def test_explicit_opt_out_adds_no_sandbox_and_warns_every_time(monkeypatch, capsys, value):
    monkeypatch.setenv(NO_SANDBOX_ENV, value)
    for _ in range(2):
        assert "--no-sandbox" in chrome_launch_args("/usr/bin/chrome", "--headless")
    err = capsys.readouterr().err
    assert err.count("WITHOUT its sandbox") == 2
    assert NO_SANDBOX_ENV in err


def test_callers_cannot_sneak_no_sandbox_past_the_helper():
    with pytest.raises(ValueError):
        chrome_launch_args("/usr/bin/chrome", "--no-sandbox")


def test_storyboard_pdf_renderer_uses_helper(monkeypatch, tmp_path, capsys):
    fake = tmp_path / "chrome"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PARALLAX_CHROME", str(fake))
    args = storyboard_package.find_pdf_renderer()
    assert args[0] == str(fake) and "--no-sandbox" not in args

    monkeypatch.setenv(NO_SANDBOX_ENV, "1")
    args = storyboard_package.find_pdf_renderer()
    assert "--no-sandbox" in args
    assert "WITHOUT its sandbox" in capsys.readouterr().err


def test_html_motion_cdp_session_uses_helper(monkeypatch):
    captured: list[list[str]] = []

    class _Stop(Exception):
        pass

    def fake_popen(args, **kwargs):
        captured.append(list(args))
        raise _Stop

    monkeypatch.setattr(html_motion.subprocess, "Popen", fake_popen)
    for env in (None, "1"):
        if env:
            monkeypatch.setenv(NO_SANDBOX_ENV, env)
        with pytest.raises(_Stop):
            html_motion._CDPPipeSession("/usr/bin/chrome", 320, 240).__enter__()
    default, opted_out = captured
    assert "--no-sandbox" not in default and "--remote-debugging-pipe" in default
    assert "--no-sandbox" in opted_out


def test_no_chrome_launch_site_hardcodes_no_sandbox():
    for module in (html_motion, storyboard_package):
        assert '"--no-sandbox"' not in inspect.getsource(module)
    assert "chrome_launch_args" in inspect.getsource(html_motion._CDPPipeSession.__enter__)
    assert "chrome_launch_args" in inspect.getsource(storyboard_package.find_pdf_renderer)
    assert chrome.SANDBOX_HINT and NO_SANDBOX_ENV in chrome.SANDBOX_HINT
