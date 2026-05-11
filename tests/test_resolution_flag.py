"""Tests for --resolution flag on `parallax plan` and `parallax produce`.

Issue #176: inject resolution: into plan.yaml via CLI flag.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from parallax import cli
from parallax.cli._produce import _run_plan_command, _validate_resolution


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _brief_payload(**overrides) -> dict:
    base = {
        "goal": "Promote the new Lion energy drink",
        "aspect": "9:16",
        "voice": "nova",
        "voice_speed": 1.0,
        "assets": {
            "provided": [
                {"path": "brand/logo.png", "kind": "product_ref",
                 "description": "Lion can"},
            ],
        },
        "script": {
            "scenes": [
                {
                    "index": 0,
                    "shot_type": "broll",
                    "vo_text": "Lions don't apologize.",
                    "prompt": "Founder holding the can in golden hour...",
                },
            ],
        },
    }
    base.update(overrides)
    return base


def _write_brief(path: Path, payload: dict) -> None:
    path.write_text(yaml.safe_dump(payload, sort_keys=False))


def _materialize_logo(folder: Path) -> None:
    (folder / "brand").mkdir(exist_ok=True)
    (folder / "brand" / "logo.png").write_bytes(b"\x89PNG")


# ---------------------------------------------------------------------------
# _validate_resolution unit tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", ["480x854", "1080x1920", "720x1280", "1x1", "3840x2160"])
def test_validate_resolution_accepts_valid(value: str) -> None:
    # Should not raise
    _validate_resolution(value)


@pytest.mark.parametrize("bad", ["480p", "1080", "16:9", "480X854", "x854", "480x", "1080 x 1920", ""])
def test_validate_resolution_rejects_invalid(bad: str) -> None:
    with pytest.raises(ValueError, match=re.escape(bad) if bad else "invalid resolution"):
        _validate_resolution(bad)


def test_validate_resolution_none_is_noop() -> None:
    # None means flag not passed — should be a no-op
    _validate_resolution(None)


# ---------------------------------------------------------------------------
# `parallax plan --resolution` writes resolution into plan.yaml
# ---------------------------------------------------------------------------

def test_plan_resolution_written_to_yaml(tmp_path: Path) -> None:
    brief_path = tmp_path / "brief.yaml"
    _write_brief(brief_path, _brief_payload())
    _materialize_logo(tmp_path)

    rc = cli.main(["plan", "--folder", str(tmp_path), "--brief", str(brief_path), "--resolution", "480x854"])

    assert rc == 0
    plan_path = tmp_path / "parallax" / "scratch" / "plan.yaml"
    assert plan_path.is_file()
    plan_data = yaml.safe_load(plan_path.read_text())
    assert plan_data["resolution"] == "480x854"


def test_plan_without_resolution_flag_leaves_resolution_unset(tmp_path: Path) -> None:
    brief_path = tmp_path / "brief.yaml"
    _write_brief(brief_path, _brief_payload())
    _materialize_logo(tmp_path)

    rc = cli.main(["plan", "--folder", str(tmp_path), "--brief", str(brief_path)])

    assert rc == 0
    plan_path = tmp_path / "parallax" / "scratch" / "plan.yaml"
    plan_data = yaml.safe_load(plan_path.read_text())
    # resolution should be absent or null (not injected)
    assert plan_data.get("resolution") is None


def test_plan_resolution_invalid_format_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    brief_path = tmp_path / "brief.yaml"
    _write_brief(brief_path, _brief_payload())
    _materialize_logo(tmp_path)

    rc = cli.main(["plan", "--folder", str(tmp_path), "--brief", str(brief_path), "--resolution", "480p"])

    assert rc == 2
    err = capsys.readouterr().err
    assert "480p" in err
    assert "WxH" in err or "480x854" in err


# ---------------------------------------------------------------------------
# `_run_plan_command` directly — resolution injection
# ---------------------------------------------------------------------------

def test_run_plan_command_injects_resolution(tmp_path: Path) -> None:
    brief_path = tmp_path / "brief.yaml"
    _write_brief(brief_path, _brief_payload())
    _materialize_logo(tmp_path)

    rc = _run_plan_command(
        folder=str(tmp_path),
        brief=str(brief_path),
        out=None,
        model="mid",
        caption_style="anton",
        resolution="1080x1920",
    )

    assert rc == 0
    plan_path = tmp_path / "parallax" / "scratch" / "plan.yaml"
    plan_data = yaml.safe_load(plan_path.read_text())
    assert plan_data["resolution"] == "1080x1920"
