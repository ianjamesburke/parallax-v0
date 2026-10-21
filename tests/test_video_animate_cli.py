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
                   "--model", "draft",
                   "--out", str(tmp_path)])
    assert rc == 0
    kw = mock_gen.call_args.kwargs
    assert kw["image_path"] == mock_image
    assert kw["end_image_path"] == end_img
    assert kw["input_references"] is None


def test_animate_end_frame_unsupported_model_names_supported_models(mock_image, tmp_path, capsys):
    rc = _run(["video", "animate", "--prompt", "slow zoom", "--start", str(mock_image),
               "--end", str(mock_image), "--model", "mid", "--out", str(tmp_path)])
    assert rc == 2
    assert "does not support end frames" in capsys.readouterr().err


def test_animate_out_file_is_written_exactly(mock_image, tmp_path, capsys):
    generated = tmp_path / "scratch" / "provider.mp4"
    generated.parent.mkdir()
    generated.write_bytes(b"MP4")
    wanted = tmp_path / "nested" / "shot.mp4"
    with patch("parallax.openrouter.generate_video", return_value=generated):
        assert _run(["video", "animate", "--prompt", "x", "--start", str(mock_image),
                     "--out", str(wanted)]) == 0
    assert wanted.read_bytes() == b"MP4"
    assert capsys.readouterr().out.strip() == str(wanted)


def test_animate_image_conditioned_estimate_uses_catalog_multiplier(monkeypatch, mock_image, tmp_path, capsys):
    end = tmp_path / "end.png"
    end.write_bytes(b"PNG")
    generated = tmp_path / "provider.mp4"
    generated.write_bytes(b"MP4")
    monkeypatch.setattr("parallax.shim.is_test_mode", lambda: False)
    with patch("parallax.openrouter.generate_video", return_value=generated):
        assert _run(["video", "animate", "--prompt", "x", "--model", "draft", "--duration", "5",
                     "--start", str(mock_image), "--end", str(end), "--out", str(tmp_path)]) == 0
    assert "~$0.4574" in capsys.readouterr().err


def test_animate_start_frame_only_estimate_uses_catalog_multiplier(monkeypatch, mock_image, tmp_path, capsys):
    generated = tmp_path / "provider.mp4"
    generated.write_bytes(b"MP4")
    monkeypatch.setattr("parallax.shim.is_test_mode", lambda: False)
    with patch("parallax.openrouter.generate_video", return_value=generated):
        assert _run(["video", "animate", "--prompt", "x", "--model", "draft", "--duration", "4",
                     "--start", str(mock_image), "--out", str(tmp_path)]) == 0
    assert "~$0.3659" in capsys.readouterr().err


def test_animate_privacy_rejection_has_hint_but_no_false_recovery(mock_image, tmp_path, capsys):
    error = RuntimeError("400 InputImageSensitiveContentDetected.PrivacyInformation")
    with patch("parallax.openrouter.generate_video", side_effect=error):
        assert _run(["video", "animate", "--prompt", "x", "--start", str(mock_image),
                     "--out", str(tmp_path / "shot.mp4")]) == 1
    err = capsys.readouterr().err
    assert "Paid output is saved" not in err
    assert "hide faces / stylize" in err


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
