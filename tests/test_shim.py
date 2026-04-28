from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from parallax.shim import is_test_mode, render_mock_image


@pytest.fixture(autouse=True)
def _isolate_output(tmp_path, monkeypatch):
    monkeypatch.setenv("PARALLAX_OUTPUT_DIR", str(tmp_path / "output"))
    yield


def test_is_test_mode_env_flag(monkeypatch):
    monkeypatch.delenv("PARALLAX_TEST_MODE", raising=False)
    assert is_test_mode() is False
    monkeypatch.setenv("PARALLAX_TEST_MODE", "1")
    assert is_test_mode() is True
    monkeypatch.setenv("PARALLAX_TEST_MODE", "true")
    assert is_test_mode() is True
    monkeypatch.setenv("PARALLAX_TEST_MODE", "0")
    assert is_test_mode() is False


def test_render_mock_image_writes_png(tmp_path):
    path = render_mock_image(prompt="a watercolor cat", model="flux-pro")
    assert Path(path).exists()
    assert Path(path).suffix == ".png"
    with Image.open(path) as img:
        assert img.format == "PNG"
        assert img.size == (1080, 1920)


def test_render_mock_image_is_deterministic_per_request():
    a = render_mock_image(prompt="same", model="flux-pro")
    b = render_mock_image(prompt="same", model="flux-pro")
    assert a == b  # stable filename keyed by (prompt, model)
    c = render_mock_image(prompt="different", model="flux-pro")
    assert c != a
