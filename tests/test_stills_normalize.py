"""stills.{check_aspect,validate_aspect,normalize_aspect} characterization.

Locks in:
  - check_aspect reports mismatch_pct + within_tolerance correctly.
  - validate_aspect raises AspectMismatchError for >2% off-target stills.
  - normalize_aspect micro-trims within tolerance, raises beyond it.
  - Already-correct aspect returns unchanged.
  - Idempotent re-runs.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from parallax.stills import (
    AspectMismatchError,
    check_aspect,
    normalize_aspect,
    validate_aspect,
)


def _make_png(path: Path, w: int, h: int, color: str = "red") -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"color={color}:s={w}x{h}",
         "-frames:v", "1", str(path)],
        check=True, capture_output=True,
    )


def _probe_size(path: Path) -> tuple[int, int]:
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    w, h = p.stdout.strip().split(",")
    return int(w), int(h)


# ─── check_aspect ────────────────────────────────────────────────────────

def test_check_aspect_within_tolerance(tmp_path):
    """Gemini's natural 768x1376 → true 9:16 = 0.8% off, within 2% tolerance."""
    src = tmp_path / "near_portrait.png"
    _make_png(src, 768, 1376)
    check = check_aspect(src, "720x1280")
    assert check.within_tolerance
    assert check.mismatch_pct < 0.02


def test_check_aspect_landscape_when_portrait_expected(tmp_path):
    """1408x768 (16:9) vs 720x1280 (9:16) — way off, NOT within tolerance."""
    src = tmp_path / "landscape.png"
    _make_png(src, 1408, 768)
    check = check_aspect(src, "720x1280")
    assert not check.within_tolerance
    assert check.mismatch_pct > 0.5


def test_check_aspect_square_when_portrait_expected(tmp_path):
    """1024x1024 (1:1) vs 720x1280 (9:16) — large mismatch."""
    src = tmp_path / "square.png"
    _make_png(src, 1024, 1024)
    check = check_aspect(src, "720x1280")
    assert not check.within_tolerance


# ─── validate_aspect (raises) ────────────────────────────────────────────

def test_validate_aspect_raises_on_landscape(tmp_path):
    src = tmp_path / "landscape.png"
    _make_png(src, 1408, 768)
    with pytest.raises(AspectMismatchError, match="off"):
        validate_aspect(src, "720x1280")


def test_validate_aspect_raises_on_square(tmp_path):
    src = tmp_path / "square.png"
    _make_png(src, 1024, 1024)
    with pytest.raises(AspectMismatchError):
        validate_aspect(src, "720x1280")


def test_validate_aspect_passes_within_tolerance(tmp_path):
    """768x1376 is < 1% off true 9:16 — should NOT raise."""
    src = tmp_path / "near_portrait.png"
    _make_png(src, 768, 1376)
    check = validate_aspect(src, "720x1280")  # raises if bad
    assert check.within_tolerance


# ─── normalize_aspect ────────────────────────────────────────────────────

def test_normalize_aspect_raises_on_landscape(tmp_path):
    """Old behavior was to silently center-crop landscape → portrait, which
    discarded subject content. Now raises instead — caller must regenerate."""
    src = tmp_path / "landscape.png"
    _make_png(src, 1408, 768)
    with pytest.raises(AspectMismatchError):
        normalize_aspect(src, "720x1280")


def test_normalize_aspect_micro_trims_within_tolerance(tmp_path):
    """768x1376 (Gemini natural) → micro-trim to true 9:16, then resize
    to the requested project resolution (720x1280). The original source
    is removed; the `_n720x1280.png` sibling is the canonical output."""
    src = tmp_path / "near_portrait.png"
    _make_png(src, 768, 1376)
    out = normalize_aspect(src, "720x1280")
    assert out != src, "expected a micro-trimmed copy"
    w, h = _probe_size(out)
    assert (w, h) == (720, 1280), "should resize to exact target after trim"


def test_normalize_aspect_returns_unchanged_when_exact(tmp_path):
    src = tmp_path / "exact.png"
    _make_png(src, 720, 1280)
    out = normalize_aspect(src, "720x1280")
    assert out == src


def test_normalize_aspect_idempotent(tmp_path):
    src = tmp_path / "near_portrait.png"
    _make_png(src, 768, 1376)
    first = normalize_aspect(src, "720x1280")
    first_mtime = first.stat().st_mtime
    second = normalize_aspect(src, "720x1280")
    assert second == first
    assert second.stat().st_mtime == first_mtime
