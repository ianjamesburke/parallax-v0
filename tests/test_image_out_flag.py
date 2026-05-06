"""Tests for --out flag smart file/directory detection in image generate."""
from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import patch

import pytest


def _make_args(**kwargs) -> argparse.Namespace:
    defaults = {
        "image_command": "generate",
        "prompt": "a red apple",
        "model": "mid",
        "aspect": None,
        "size": None,
        "refs": None,
        "out": None,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


@pytest.fixture(autouse=True)
def _test_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("PARALLAX_TEST_MODE", "1")
    monkeypatch.setenv("PARALLAX_OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("PARALLAX_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("PARALLAX_USAGE_LOG", str(tmp_path / "usage.ndjson"))
    from parallax import runlog
    runlog.start_run("test-run")
    yield
    runlog.end_run()


def test_out_with_image_extension_passes_out_file(tmp_path):
    """--out foo.png → out_file set, out_dir None."""
    target = tmp_path / "result.png"
    captured: dict = {}

    def fake_generate(prompt, model, *, reference_images, out_dir, out_file, size, aspect_ratio):
        captured["out_file"] = out_file
        captured["out_dir"] = out_dir
        return target

    from parallax.cli._image import _run_generate
    with patch("parallax.openrouter.generate_image", side_effect=fake_generate):
        _run_generate(_make_args(out=str(target)))

    assert captured["out_file"] == target
    assert captured["out_dir"] is None


@pytest.mark.parametrize("ext", [".png", ".jpg", ".jpeg", ".webp"])
def test_recognized_extensions_treated_as_file(tmp_path, ext):
    target = tmp_path / f"output{ext}"
    captured: dict = {}

    def fake_generate(prompt, model, *, reference_images, out_dir, out_file, size, aspect_ratio):
        captured["out_file"] = out_file
        captured["out_dir"] = out_dir
        return target

    from parallax.cli._image import _run_generate
    with patch("parallax.openrouter.generate_image", side_effect=fake_generate):
        _run_generate(_make_args(out=str(target)))

    assert captured["out_file"] == target
    assert captured["out_dir"] is None


def test_out_without_extension_treated_as_directory(tmp_path):
    """--out ./frames/ → out_dir set, out_file None."""
    out_dir = tmp_path / "frames"
    captured: dict = {}

    def fake_generate(prompt, model, *, reference_images, out_dir, out_file, size, aspect_ratio):
        captured["out_file"] = out_file
        captured["out_dir"] = out_dir
        return out_dir / "mock.png"

    from parallax.cli._image import _run_generate
    with patch("parallax.openrouter.generate_image", side_effect=fake_generate):
        _run_generate(_make_args(out=str(out_dir)))

    assert captured["out_file"] is None
    assert captured["out_dir"] == out_dir


def test_no_out_uses_cwd(monkeypatch, tmp_path):
    """No --out → out_dir = cwd, out_file None."""
    monkeypatch.chdir(tmp_path)
    captured: dict = {}

    def fake_generate(prompt, model, *, reference_images, out_dir, out_file, size, aspect_ratio):
        captured["out_file"] = out_file
        captured["out_dir"] = out_dir
        return tmp_path / "mock.png"

    from parallax.cli._image import _run_generate
    with patch("parallax.openrouter.generate_image", side_effect=fake_generate):
        _run_generate(_make_args(out=None))

    assert captured["out_file"] is None
    assert captured["out_dir"] == tmp_path


def test_out_file_written_to_exact_path(tmp_path):
    """End-to-end in test mode: image written to exact --out path."""
    target = tmp_path / "scene_00_still.png"
    from parallax import openrouter
    path = openrouter.generate_image(
        "a red apple", "mid",
        out_file=target,
    )
    assert path == target
    assert target.exists()
    assert target.is_file()


def test_generate_image_out_file_takes_precedence_over_out_dir(tmp_path):
    """When out_file is given, the image lands at that exact path."""
    target = tmp_path / "explicit.png"
    out_dir = tmp_path / "dir"
    from parallax import openrouter
    path = openrouter.generate_image(
        "test prompt", "mid",
        out_dir=out_dir,
        out_file=target,
    )
    assert path == target
    assert target.exists()
    assert not out_dir.exists()
