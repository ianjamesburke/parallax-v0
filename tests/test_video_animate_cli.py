"""Tests for `parallax video animate` CLI command."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from parallax import cli


@pytest.fixture(autouse=True)
def _test_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("PARALLAX_TEST_MODE", "1")
    monkeypatch.setenv("PARALLAX_OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("PARALLAX_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("PARALLAX_USAGE_LOG", str(tmp_path / "usage.ndjson"))


@pytest.fixture()
def mock_image(tmp_path) -> Path:
    p = tmp_path / "frame.png"
    p.write_bytes(b"PNG")
    return p


def _run(argv):
    return cli.main(argv)


def test_animate_start_frame(mock_image, tmp_path, capsys):
    fake_mp4 = tmp_path / "out.mp4"
    fake_mp4.write_bytes(b"MP4")
    with patch("parallax.openrouter.generate_video", return_value=fake_mp4) as mock_gen:
        rc = _run(["video", "animate",
                   "--prompt", "she leans in",
                   "--start", str(mock_image),
                   "--out", str(tmp_path)])
    assert rc == 0
    mock_gen.assert_called_once()
    call_kwargs = mock_gen.call_args
    assert call_kwargs.args[0] == "she leans in"
    assert call_kwargs.kwargs["image_path"] == mock_image
    assert call_kwargs.kwargs["end_image_path"] is None
    assert call_kwargs.kwargs["input_references"] is None
    out = capsys.readouterr().out.strip()
    assert out == str(fake_mp4)


def test_animate_start_and_end_frame(mock_image, tmp_path):
    end_img = tmp_path / "end.png"
    end_img.write_bytes(b"PNG")
    fake_mp4 = tmp_path / "out.mp4"
    fake_mp4.write_bytes(b"MP4")
    with patch("parallax.openrouter.generate_video", return_value=fake_mp4) as mock_gen:
        rc = _run(["video", "animate",
                   "--prompt", "slow zoom",
                   "--start", str(mock_image),
                   "--end", str(end_img),
                   "--out", str(tmp_path)])
    assert rc == 0
    kw = mock_gen.call_args.kwargs
    assert kw["image_path"] == mock_image
    assert kw["end_image_path"] == end_img
    assert kw["input_references"] is None


def test_animate_ref_images(mock_image, tmp_path):
    fake_mp4 = tmp_path / "out.mp4"
    fake_mp4.write_bytes(b"MP4")
    with patch("parallax.openrouter.generate_video", return_value=fake_mp4) as mock_gen:
        rc = _run(["video", "animate",
                   "--prompt", "she walks through fog",
                   "--ref", str(mock_image),
                   "--out", str(tmp_path)])
    assert rc == 0
    kw = mock_gen.call_args.kwargs
    assert kw["image_path"] is None
    assert kw["end_image_path"] is None
    assert kw["input_references"] == [mock_image]


def test_animate_end_without_start_errors(mock_image, tmp_path, capsys):
    rc = _run(["video", "animate",
               "--prompt", "test",
               "--end", str(mock_image),
               "--out", str(tmp_path)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "--end requires --start" in err


def test_animate_start_and_ref_mutually_exclusive(mock_image):
    rc = _run(["video", "animate",
               "--prompt", "test",
               "--start", str(mock_image),
               "--ref", str(mock_image)])
    assert rc == 2


def test_animate_duration_and_model(mock_image, tmp_path):
    fake_mp4 = tmp_path / "out.mp4"
    fake_mp4.write_bytes(b"MP4")
    with patch("parallax.openrouter.generate_video", return_value=fake_mp4) as mock_gen:
        rc = _run(["video", "animate",
                   "--prompt", "x",
                   "--start", str(mock_image),
                   "--model", "kling",
                   "--duration", "8",
                   "--out", str(tmp_path)])
    assert rc == 0
    args = mock_gen.call_args
    assert args.args[1] == "kling"
    assert args.kwargs["duration_s"] == 8.0
