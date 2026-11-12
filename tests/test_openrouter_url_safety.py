"""The OpenRouter key only goes to OpenRouter hosts; job receipts are untrusted input.

No real network: `httpx.get` / `httpx.post` are replaced with recorders, and
the suite-wide conftest guard blocks anything that slips through.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from parallax import cli, openrouter, runlog
from parallax.openrouter import (
    JOB_RECEIPT_MARKER,
    JobReceiptError,
    UntrustedURLError,
    _download_url,
    _openrouter_get,
    is_openrouter_url,
    load_job_receipt,
    resume_video_job,
    write_job_receipt,
)

KEY = "test-key-not-real"
POLL = "https://openrouter.ai/api/v1/videos/gen-1"
CDN = "https://cdn.example-storage.com/videos/gen-1.mp4?sig=abc"


@pytest.fixture(autouse=True)
def _real_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    monkeypatch.delenv("PARALLAX_TEST_MODE", raising=False)
    monkeypatch.delenv("PARALLAX_CHROME_NO_SANDBOX", raising=False)
    monkeypatch.setenv("PARALLAX_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("PARALLAX_USAGE_LOG", str(tmp_path / "usage.ndjson"))
    monkeypatch.setattr(openrouter, "_VIDEO_POLL_INTERVAL_S", 0.0)
    runlog.start_run("url-safety-test")
    yield
    runlog.end_run()


class _Resp:
    def __init__(self, status_code=200, body=None, content=b""):
        self.status_code, self._body, self.content, self.text, self.headers = status_code, body or {}, content, "", {}

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=self)  # type: ignore[arg-type]


class _Net:
    """Records every GET/POST; answers polls with `completed` and downloads with bytes."""

    def __init__(self, monkeypatch, *, download_url=CDN, download_status=200, polling_url=POLL):
        self.gets: list[tuple[str, dict]] = []
        self.posts: list[str] = []
        self.download_url, self.download_status, self.polling_url = download_url, download_status, polling_url
        monkeypatch.setattr(httpx, "get", self.get)
        monkeypatch.setattr(httpx, "post", self.post)

    def get(self, url, *, headers, timeout):
        self.gets.append((url, dict(headers)))
        if url == self.download_url:
            return _Resp(self.download_status, content=b"MP4BYTES")
        return _Resp(200, {"id": "gen-1", "status": "completed", "unsigned_urls": [self.download_url], "usage": {"cost": 0.1}})

    def post(self, url, *, headers, json, timeout):
        self.posts.append(url)
        return _Resp(202, {"id": "gen-1", "polling_url": self.polling_url, "status": "pending"})

    def auth_for(self, url):
        return [h.get("Authorization") for u, h in self.gets if u == url]


# ---------------------------------------------------------------------------
# HTTP-layer allowlist
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "https://openrouter.ai/api/v1/videos/x",
    "https://api.openrouter.ai/v1/x",
    "https://www.openrouter.ai/x",
    "https://OpenRouter.AI/x",
    "https://openrouter.ai:443/x",
])
def test_openrouter_hosts_are_allowlisted(url):
    assert is_openrouter_url(url)


@pytest.mark.parametrize("url", [
    "http://openrouter.ai/api/v1/videos/x",
    "https://evil.example/x",
    "https://openrouter.ai.evil.com/x",
    "https://evil.com/?u=openrouter.ai",
    "https://evil.com/#openrouter.ai",
    "https://evil.com/openrouter.ai",
    "https://evilopenrouter.ai/x",
    "https://openrouter.ai@evil.com/x",
    "https://user:pw@openrouter.ai/x",
    "https://evil.com\\@openrouter.ai/x",
    "https://openrouter.ai:8443/x",
    "https://openrouter.ai /x",
    "openrouter.ai/x",
    "//openrouter.ai/x",
    "ftp://openrouter.ai/x",
    "",
    None,
    123,
])
def test_spoofed_and_foreign_urls_are_not_allowlisted(url):
    assert not is_openrouter_url(url)


def test_openrouter_get_attaches_key_only_for_allowlisted_url(monkeypatch):
    net = _Net(monkeypatch)
    _openrouter_get(POLL, timeout=1)
    assert net.auth_for(POLL) == [f"Bearer {KEY}"]
    for bad in ("http://openrouter.ai/x", "https://evil.example/x", "https://openrouter.ai.evil.com/x",
                "https://evil.com/?u=openrouter.ai", "https://openrouter.ai@evil.com/x"):
        with pytest.raises(UntrustedURLError) as exc:
            _openrouter_get(bad, timeout=1)
        assert KEY not in str(exc.value)
    assert len(net.gets) == 1, "refused URLs must not be requested at all"


def test_download_sends_key_to_openrouter_but_not_to_cdn(monkeypatch):
    net = _Net(monkeypatch)
    _download_url("https://openrouter.ai/api/v1/videos/gen-1/content?index=0", timeout=1)
    _download_url(CDN, timeout=1)
    assert net.gets[0][1].get("Authorization") == f"Bearer {KEY}"
    assert "Authorization" not in net.gets[1][1]
    assert all(KEY not in v for v in net.gets[1][1].values())


@pytest.mark.parametrize("status", [401, 403])
def test_download_needing_auth_off_allowlist_is_refused_not_retried_with_key(monkeypatch, status):
    net = _Net(monkeypatch, download_status=status)
    with pytest.raises(UntrustedURLError, match="refusing to send the OpenRouter key"):
        _download_url(CDN, timeout=1)
    assert len(net.gets) == 1 and "Authorization" not in net.gets[0][1]


def test_refusal_messages_do_not_echo_query_tokens():
    with pytest.raises(UntrustedURLError) as exc:
        _openrouter_get("https://evil.example/x?token=SECRET123", timeout=1)
    assert "SECRET123" not in str(exc.value)


def test_video_generation_polls_with_key_and_downloads_cdn_without_it(monkeypatch, tmp_path):
    net = _Net(monkeypatch)
    path = openrouter.generate_video("x", alias="seedance", duration_s=4, out_dir=tmp_path)
    assert Path(path).read_bytes() == b"MP4BYTES"
    assert net.auth_for(POLL) == [f"Bearer {KEY}"]
    assert net.auth_for(CDN) == [None]


@pytest.mark.parametrize("evil", ["https://evil.example/poll", "http://openrouter.ai/api/v1/videos/x",
                                  "https://openrouter.ai.evil.com/v"])
def test_submit_response_with_foreign_polling_url_is_refused_without_fallback(monkeypatch, tmp_path, evil):
    net = _Net(monkeypatch, polling_url=evil)
    with pytest.raises(UntrustedURLError):
        openrouter.generate_video("x", alias="seedance", duration_s=4, out_dir=tmp_path, allow_fallback=True)
    assert net.gets == [], "no request may carry the key to a foreign polling URL"
    assert len(net.posts) == 1, "a security refusal must not resubmit (and re-pay) on a fallback model"


# ---------------------------------------------------------------------------
# Job receipts
# ---------------------------------------------------------------------------

def _receipt(path: Path, target: Path, **override) -> Path:
    write_job_receipt(path, {"id": "gen-1", "polling_url": POLL, "model": "seedance"}, out=target, estimate_usd=0.5)
    if override:
        data = json.loads(path.read_text())
        for k, v in override.items():
            if v is None:
                data.pop(k, None)
            else:
                data[k] = v
        path.write_text(json.dumps(data))
    return path


def test_written_receipt_carries_marker_and_schema(tmp_path):
    data = json.loads(_receipt(tmp_path / "a.job.json", tmp_path).read_text())
    assert data["parallax"] == JOB_RECEIPT_MARKER and data["schema"] == 1
    assert data["polling_url"] == POLL and data["out"] == str(tmp_path.resolve())


def test_resume_accepts_valid_receipt_inside_project(monkeypatch, tmp_path):
    net = _Net(monkeypatch)
    project = tmp_path / "proj"
    project.mkdir()
    job = _receipt(project / "animation.job.json", project)
    path = resume_video_job(job, project, root=project)
    assert path.parent == project and path.read_bytes() == b"MP4BYTES"
    assert net.auth_for(POLL) == [f"Bearer {KEY}"]
    assert net.auth_for(CDN) == [None]


@pytest.mark.parametrize("case", ["unmarked", "wrong_marker", "bad_schema", "evil_poll", "http_poll",
                                  "spoof_poll", "out_escapes", "abs_out_outside", "model_traversal",
                                  "missing_id", "not_json", "not_object"])
def test_resume_rejects_untrusted_receipts_before_any_request(monkeypatch, tmp_path, case):
    net = _Net(monkeypatch)
    project = tmp_path / "proj"
    project.mkdir()
    job = project / "animation.job.json"
    if case == "unmarked":  # 0.7.7-style / planted receipt: just a polling_url
        job.write_text(json.dumps({"id": "gen-1", "polling_url": POLL, "out": str(project)}))
    elif case == "not_json":
        job.write_text("{nope")
    elif case == "not_object":
        job.write_text("[]")
    else:
        override = {
            "wrong_marker": {"parallax": "something-else"},
            "bad_schema": {"schema": 99},
            "evil_poll": {"polling_url": "https://evil.example/poll"},
            "http_poll": {"polling_url": "http://openrouter.ai/api/v1/videos/gen-1"},
            "spoof_poll": {"polling_url": "https://openrouter.ai@evil.com/poll"},
            "out_escapes": {"out": "../../outside/clip.mp4"},
            "abs_out_outside": {"out": str(tmp_path / "elsewhere" / "clip.mp4")},
            "model_traversal": {"model": "../../evil"},
            "missing_id": {"id": None},
        }[case]
        _receipt(job, project, **override)
    with pytest.raises(JobReceiptError):
        resume_video_job(job, project, root=project)
    assert net.gets == []


def test_resume_rejects_receipt_outside_project(monkeypatch, tmp_path):
    net = _Net(monkeypatch)
    project, other = tmp_path / "proj", tmp_path / "other"
    project.mkdir(); other.mkdir()
    job = _receipt(other / "animation.job.json", other)
    with pytest.raises(JobReceiptError, match="outside the project"):
        load_job_receipt(job, root=project)
    with pytest.raises(JobReceiptError):
        resume_video_job(job, project, root=project)
    assert net.gets == []


def test_resume_rejects_symlinked_receipt_pointing_outside(monkeypatch, tmp_path):
    project, other = tmp_path / "proj", tmp_path / "other"
    project.mkdir(); other.mkdir()
    real = _receipt(other / "x.job.json", project)
    (project / "animation.job.json").symlink_to(real)
    with pytest.raises(JobReceiptError, match="outside the project"):
        load_job_receipt(project / "animation.job.json", root=project)


# ---------------------------------------------------------------------------
# CLI: `video animate` auto-resume and `video resume`
# ---------------------------------------------------------------------------

def test_animate_refuses_planted_receipt_and_submits_nothing(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("PARALLAX_TEST_MODE", "1")
    net = _Net(monkeypatch)
    (tmp_path / "animation.job.json").write_text(json.dumps({"polling_url": "https://evil.example/p", "out": "/tmp"}))
    with patch("parallax.openrouter.generate_video") as gen:
        rc = cli.main(["video", "animate", "--prompt", "x", "--out", str(tmp_path)])
    assert rc == 1
    gen.assert_not_called()
    assert net.gets == [] and net.posts == []
    assert "not a Parallax video job receipt" in capsys.readouterr().err


def test_animate_refuses_marked_receipt_with_evil_host(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("PARALLAX_TEST_MODE", "1")
    net = _Net(monkeypatch)
    _receipt(tmp_path / "shot.mp4.job.json", tmp_path / "shot.mp4", polling_url="https://openrouter.ai.evil.com/p")
    with patch("parallax.openrouter.generate_video") as gen:
        rc = cli.main(["video", "animate", "--prompt", "x", "--out", str(tmp_path / "shot.mp4")])
    assert rc == 1
    gen.assert_not_called()
    assert net.gets == []
    assert "non-OpenRouter URL" in capsys.readouterr().err


def test_animate_writes_marked_receipt_and_resumes_it(monkeypatch, tmp_path):
    monkeypatch.setenv("PARALLAX_TEST_MODE", "1")
    target = tmp_path / "shot.mp4"

    def interrupted(*args, on_submit, **kwargs):
        on_submit({"id": "gen-1", "polling_url": POLL, "model": "seedance"})
        raise RuntimeError("poll interrupted")  # run dies after the provider accepted the job

    with patch("parallax.openrouter.generate_video", side_effect=interrupted):
        assert cli.main(["video", "animate", "--prompt", "x", "--out", str(target)]) == 1
    receipt = tmp_path / "shot.mp4.job.json"
    assert load_job_receipt(receipt, root=tmp_path)["out"] == str(target.resolve())

    net = _Net(monkeypatch)
    with patch("parallax.openrouter.generate_video") as gen:
        assert cli.main(["video", "animate", "--prompt", "x", "--out", str(target)]) == 0
    gen.assert_not_called()
    assert target.read_bytes() == b"MP4BYTES"
    assert net.auth_for(POLL) == [f"Bearer {KEY}"] and net.auth_for(CDN) == [None]


def test_video_resume_cli_writes_inside_project(monkeypatch, tmp_path):
    net = _Net(monkeypatch)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "clips").mkdir()
    _receipt(tmp_path / "clips" / "a.mp4.job.json", tmp_path / "clips" / "a.mp4")
    assert cli.main(["video", "resume", "--job", "clips/a.mp4.job.json"]) == 0
    assert (tmp_path / "clips" / "a.mp4").read_bytes() == b"MP4BYTES"
    assert net.auth_for(CDN) == [None]


@pytest.mark.parametrize("case", ["outside_project", "out_escapes", "unmarked"])
def test_video_resume_cli_rejects_untrusted_receipts(monkeypatch, tmp_path, capsys, case):
    net = _Net(monkeypatch)
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)
    if case == "outside_project":
        job = _receipt(tmp_path / "planted.job.json", project)
    elif case == "out_escapes":
        job = _receipt(project / "a.job.json", project, out=str(tmp_path / "victim.mp4"))
    else:
        job = project / "a.job.json"
        job.write_text(json.dumps({"id": "g", "polling_url": "https://evil.example/p", "out": str(tmp_path / "victim.mp4")}))
    assert cli.main(["video", "resume", "--job", str(job)]) == 1
    assert net.gets == []
    assert not (tmp_path / "victim.mp4").exists()
    assert "refusing to resume" in capsys.readouterr().err
