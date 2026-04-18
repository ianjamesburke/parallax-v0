from __future__ import annotations

import os
from pathlib import Path

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


def test_generate_with_reference_images_routes_to_edit_endpoint(tmp_path):
    spec = resolve("nano-banana")
    assert spec.supports_reference and spec.edit_fal_id

    ref1 = tmp_path / "ref1.png"
    ref1.write_bytes(b"x")
    ref2 = tmp_path / "ref2.jpg"
    ref2.write_bytes(b"y")

    uploaded: list[Path] = []

    def uploader(p):
        uploaded.append(p)
        return f"https://v2.fal.media/uploaded/{p.name}"

    calls: list[tuple[str, dict]] = []

    def runner(fal_id, args):
        calls.append((fal_id, args))
        return {"images": [{"url": "https://v2.fal.media/output/edited.png"}]}

    def downloader(url, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"\x89PNG")

    out = fal.generate(
        "add a hat",
        spec,
        reference_images=[ref1, ref2],
        runner=runner,
        downloader=downloader,
        uploader=uploader,
    )
    assert out.exists()
    assert uploaded == [ref1, ref2]
    assert len(calls) == 1
    fal_id, args = calls[0]
    assert fal_id == spec.edit_fal_id
    assert args["prompt"] == "add a hat"
    assert args["image_urls"] == [
        "https://v2.fal.media/uploaded/ref1.png",
        "https://v2.fal.media/uploaded/ref2.jpg",
    ]


def test_generate_refs_on_unsupported_model_raises():
    spec = resolve("draft")  # flux schnell — no edit endpoint in v0
    with pytest.raises(RuntimeError, match="does not support reference_images"):
        fal.generate(
            "x",
            spec,
            reference_images=[Path("/does/not/matter.png")],
            runner=lambda f, a: {"images": [{"url": "x"}]},
            downloader=lambda u, d: None,
            uploader=lambda p: "x",
        )


def test_generate_upload_error_surfaces(tmp_path):
    spec = resolve("nano-banana")
    ref = tmp_path / "ref.png"
    ref.write_bytes(b"x")

    def uploader(p):
        raise OSError("upload timeout")

    with pytest.raises(RuntimeError, match="FAL upload failed"):
        fal.generate(
            "x",
            spec,
            reference_images=[ref],
            runner=lambda f, a: {"images": [{"url": "x"}]},
            downloader=lambda u, d: None,
            uploader=uploader,
        )


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
