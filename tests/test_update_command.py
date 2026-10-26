"""`parallax update`: editable installs fast-forward the local repo; others are unchanged."""
from __future__ import annotations

import io
import json
import subprocess
from unittest import mock

import pytest

from parallax.cli import _meta


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "PATH": __import__("os").environ["PATH"],
                        "HOME": str(cwd)})


def _commit_version(repo, version, msg):
    (repo / "pyproject.toml").write_text(f'[project]\nname = "parallax"\nversion = "{version}"\n')
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", msg)


@pytest.fixture
def repos(tmp_path):
    """origin (v0.6.1) and a clone checked out at v0.6.0 tracking origin/main."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    _commit_version(origin, "0.6.0", "v0.6.0")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True, capture_output=True)
    _commit_version(origin, "0.6.1", "v0.6.1")
    return origin, clone


def _run(monkeypatch, repo, installed="0.6.0", uv_rc=0):
    monkeypatch.setattr(_meta.shutil, "which", lambda _n: "/usr/bin/uv")
    monkeypatch.setattr("importlib.metadata.version", lambda _n: installed)
    monkeypatch.setattr(_meta, "_editable_source", lambda: str(repo) if repo else None)
    real_run = subprocess.run
    uv_calls = []

    def fake_run(cmd, *a, **kw):
        if cmd and cmd[0] == "/usr/bin/uv":
            uv_calls.append(cmd)
            return subprocess.CompletedProcess(cmd, uv_rc)
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr(_meta.subprocess, "run", fake_run)
    return uv_calls


def test_editable_clean_pulls_then_reinstalls(monkeypatch, repos, capsys):
    _, clone = repos
    uv_calls = _run(monkeypatch, clone)
    assert _meta._run_update() == 0
    out = capsys.readouterr().out
    assert "v0.6.0 → v0.6.1" in out and str(clone) in out and "main" in out
    assert uv_calls == [["/usr/bin/uv", "tool", "upgrade", "parallax", "--reinstall"]]
    assert 'version = "0.6.1"' in (clone / "pyproject.toml").read_text()


def test_editable_already_current_is_honest(monkeypatch, repos, capsys):
    origin, clone = repos
    subprocess.run(["git", "-C", str(clone), "pull", "-q", "--ff-only"], check=True, capture_output=True)
    uv_calls = _run(monkeypatch, clone, installed="0.6.1")
    assert _meta._run_update() == 0
    out = capsys.readouterr().out
    assert f"Already up to date (v0.6.1) from {clone} on branch main" in out
    assert uv_calls == []


def test_editable_stale_install_reinstalls_without_pull(monkeypatch, repos, capsys):
    """Repo already pulled but installed metadata is stale (the original bug report)."""
    _, clone = repos
    subprocess.run(["git", "-C", str(clone), "pull", "-q", "--ff-only"], check=True, capture_output=True)
    uv_calls = _run(monkeypatch, clone, installed="0.6.0")
    assert _meta._run_update() == 0
    assert "v0.6.0 → v0.6.1" in capsys.readouterr().out
    assert len(uv_calls) == 1


def test_editable_dirty_touches_nothing(monkeypatch, repos, capsys):
    _, clone = repos
    (clone / "scratch.txt").write_text("wip")
    head = subprocess.run(["git", "-C", str(clone), "rev-parse", "HEAD"], capture_output=True, text=True).stdout
    uv_calls = _run(monkeypatch, clone)
    assert _meta._run_update() == 1
    err = capsys.readouterr().err
    assert "uncommitted" in err and f"Run `git pull` in {clone}" in err
    assert uv_calls == []
    assert subprocess.run(["git", "-C", str(clone), "rev-parse", "HEAD"], capture_output=True, text=True).stdout == head
    assert (clone / "scratch.txt").exists()


def test_editable_cannot_fast_forward(monkeypatch, repos, capsys):
    _, clone = repos
    (clone / "x").write_text("1")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-qm", "diverge")
    head = subprocess.run(["git", "-C", str(clone), "rev-parse", "HEAD"], capture_output=True, text=True).stdout
    uv_calls = _run(monkeypatch, clone)
    assert _meta._run_update() == 1
    err = capsys.readouterr().err
    assert f"Run `git pull` in {clone}" in err
    assert uv_calls == []
    assert subprocess.run(["git", "-C", str(clone), "rev-parse", "HEAD"], capture_output=True, text=True).stdout == head


def test_editable_remote_unreachable(monkeypatch, repos, capsys):
    _, clone = repos
    _git(clone, "remote", "set-url", "origin", str(clone.parent / "does-not-exist"))
    uv_calls = _run(monkeypatch, clone)
    assert _meta._run_update() == 1
    err = capsys.readouterr().err
    assert "git pull --ff-only failed" in err and f"Run `git pull` in {clone}" in err
    assert uv_calls == []


def test_editable_no_origin_tracking(monkeypatch, repos, capsys):
    _, clone = repos
    _git(clone, "checkout", "-q", "-b", "local-only")
    uv_calls = _run(monkeypatch, clone)
    assert _meta._run_update() == 1
    assert "does not track an origin branch" in capsys.readouterr().err
    assert uv_calls == []


def test_non_editable_behaves_as_before(monkeypatch, capsys):
    uv_calls = _run(monkeypatch, None, installed="0.6.0")
    body = b'[project]\nversion = "0.6.1"\n'
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: io.BytesIO(body))
    assert _meta._run_update() == 0
    assert "Updating parallax v0.6.0 → v0.6.1…" in capsys.readouterr().out
    assert uv_calls == [["/usr/bin/uv", "tool", "upgrade", "parallax", "--reinstall"]]


def test_non_editable_up_to_date(monkeypatch, capsys):
    uv_calls = _run(monkeypatch, None, installed="0.6.1")
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: io.BytesIO(b'version = "0.6.1"\n'))
    assert _meta._run_update() == 0
    assert "Already up to date (v0.6.1)." in capsys.readouterr().out
    assert uv_calls == []


def _fake_dist(raw):
    d = mock.Mock()
    d.read_text.return_value = raw
    return d


def test_editable_source_detection():
    url = {"url": "file:///Users/me/My%20Repo", "dir_info": {"editable": True}}
    with mock.patch("importlib.metadata.distribution", return_value=_fake_dist(json.dumps(url))):
        assert _meta._editable_source() == "/Users/me/My Repo"
    non_ed = {"url": "file:///x", "dir_info": {}}
    with mock.patch("importlib.metadata.distribution", return_value=_fake_dist(json.dumps(non_ed))):
        assert _meta._editable_source() is None
    vcs = {"url": "https://github.com/a/b", "vcs_info": {"vcs": "git"}}
    with mock.patch("importlib.metadata.distribution", return_value=_fake_dist(json.dumps(vcs))):
        assert _meta._editable_source() is None
    with mock.patch("importlib.metadata.distribution", return_value=_fake_dist(None)):
        assert _meta._editable_source() is None
    with mock.patch("importlib.metadata.distribution", return_value=_fake_dist("{not json")):
        assert _meta._editable_source() is None
    with mock.patch("importlib.metadata.distribution", side_effect=Exception("boom")):
        assert _meta._editable_source() is None
