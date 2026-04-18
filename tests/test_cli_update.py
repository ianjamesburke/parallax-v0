from __future__ import annotations

from parallax import cli


def test_update_missing_uv_fails_fast(monkeypatch, capsys):
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    rc = cli.main(["update"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "uv not found" in err


def test_update_shells_out_to_uv_tool_upgrade(monkeypatch):
    calls: list[list[str]] = []

    def fake_which(name):
        return "/usr/local/bin/uv" if name == "uv" else None

    class FakeCompleted:
        returncode = 0

    def fake_run(cmd, *a, **kw):
        calls.append(cmd)
        return FakeCompleted()

    monkeypatch.setattr(cli.shutil, "which", fake_which)
    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    rc = cli.main(["update"])
    assert rc == 0
    assert calls == [["/usr/local/bin/uv", "tool", "upgrade", "parallax"]]


def test_update_propagates_uv_exit_code(monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/u/bin/uv")

    class FakeCompleted:
        returncode = 3

    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **kw: FakeCompleted())
    assert cli.main(["update"]) == 3
