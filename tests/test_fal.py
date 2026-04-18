from __future__ import annotations

import os

import pytest

from parallax import fal
from parallax.pricing import resolve


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("FAL_KEY", "test-key")
    monkeypatch.setenv("PARALLAX_OUTPUT_DIR", str(tmp_path / "output"))
    yield


def test_missing_fal_key_fails_fast(monkeypatch):
    monkeypatch.delenv("FAL_KEY", raising=False)
    with pytest.raises(RuntimeError, match="FAL_KEY"):
        fal.check_available()


def test_generate_happy_path(tmp_path):
    spec = resolve("draft")
    calls: list[tuple[str, dict]] = []

    def runner(fal_id, args):
        calls.append((fal_id, args))
        return {"images": [{"url": "https://example.test/img.png"}]}

    def downloader(url, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"\x89PNG\r\n\x1a\n")  # png magic, enough for tests

    out = fal.generate("a cat", spec, runner=runner, downloader=downloader)
    assert out.exists()
    assert out.suffix == ".png"  # url ends in .png so filename does too
    assert out.name.startswith("draft_")
    assert calls == [(spec.fal_id, {"prompt": "a cat"})]


def test_generate_honors_url_extension():
    spec = resolve("draft")

    def runner(fal_id, args):
        return {"images": [{"url": "https://v2.fal.media/output/abc.jpg"}]}

    def downloader(url, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"x")

    out = fal.generate("a cat", spec, runner=runner, downloader=downloader)
    assert out.suffix == ".jpg"


def test_generate_surfaces_fal_error():
    spec = resolve("draft")

    def runner(fal_id, args):
        raise RuntimeError("fal: out of credits")

    with pytest.raises(RuntimeError, match="FAL call failed"):
        fal.generate("a cat", spec, runner=runner, downloader=lambda u, d: None)


def test_generate_missing_images_list():
    spec = resolve("draft")

    def runner(fal_id, args):
        return {"error": "model unavailable"}

    with pytest.raises(RuntimeError, match="missing 'images'"):
        fal.generate("a cat", spec, runner=runner, downloader=lambda u, d: None)


def test_generate_surfaces_download_error():
    spec = resolve("draft")

    def runner(fal_id, args):
        return {"images": [{"url": "https://example.test/img.png"}]}

    def downloader(url, dest):
        raise OSError("connection reset")

    with pytest.raises(RuntimeError, match="FAL download failed"):
        fal.generate("a cat", spec, runner=runner, downloader=downloader)


@pytest.mark.skipif(
    os.environ.get("PARALLAX_LIVE_FAL") != "1",
    reason="set PARALLAX_LIVE_FAL=1 and FAL_KEY to run the one live FAL call",
)
def test_live_draft_call_writes_png():
    # One real call against the cheapest model so CI stays ~free.
    spec = resolve("draft")
    out = fal.generate("a single red cube on a white background", spec)
    assert out.exists()
    assert out.stat().st_size > 1000
