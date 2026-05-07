"""Tests for the Phase 1.7 CLI wiring — `plan`, `ingest`, and `produce --brief`.

Layer: INTEGRATION — exercises full pipeline routing with monkeypatched callables.
Uses PARALLAX_TEST_MODE-free mocking. For pure CLI parsing, see test_cli_contract.py.

Each test exercises the Typer-routed entry point (`parallax.cli.main`) so
that argument parsing, dispatch, and error surfaces are all covered.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from parallax import cli, ingest as ingest_mod
from parallax import produce as produce_mod


# ---------------------------------------------------------------------------
# Fixtures
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


def _make_silent_wav(path: Path, duration_s: float = 1.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
            "-t", f"{duration_s}",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def fake_transcribe(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Replace transcribe_words so ingest tests don't need WhisperX."""
    fake_words = [{"word": "hi", "start": 0.0, "end": 0.4}]

    def _stub(input_path: str, out_path: str) -> list[dict]:
        Path(out_path).write_text(json.dumps({"words": fake_words}))
        return list(fake_words)

    monkeypatch.setattr(ingest_mod, "transcribe_words", _stub)
    return fake_words


# ---------------------------------------------------------------------------
# `parallax plan`
# ---------------------------------------------------------------------------

def test_plan_command_writes_plan(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    brief_path = tmp_path / "brief.yaml"
    _write_brief(brief_path, _brief_payload())
    _materialize_logo(tmp_path)

    rc = cli.main(["plan", "--folder", str(tmp_path), "--brief", str(brief_path)])

    assert rc == 0
    plan_path = tmp_path / "parallax" / "scratch" / "plan.yaml"
    assert plan_path.is_file()
    out = capsys.readouterr().out
    assert "Wrote plan.yaml" in out
    assert str(plan_path) in out


def test_plan_command_missing_asset_writes_questions(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    brief_path = tmp_path / "brief.yaml"
    _write_brief(brief_path, _brief_payload())
    # Intentionally skip _materialize_logo — asset is missing.

    rc = cli.main(["plan", "--folder", str(tmp_path), "--brief", str(brief_path)])

    assert rc == 1
    questions_path = tmp_path / "parallax" / "scratch" / "questions.yaml"
    assert questions_path.is_file()
    err = capsys.readouterr().err
    assert "missing" in err
    assert str(questions_path) in err


def test_plan_command_missing_brief_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    rc = cli.main(["plan", "--folder", str(tmp_path)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "brief not found" in err


# ---------------------------------------------------------------------------
# `parallax ingest`
# ---------------------------------------------------------------------------

def test_ingest_estimate_dir(
    tmp_path: Path, fake_transcribe: list[dict], capsys: pytest.CaptureFixture
) -> None:
    clip_dir = tmp_path / "clips"
    clip_dir.mkdir()
    _make_silent_wav(clip_dir / "a.wav", 1.0)
    _make_silent_wav(clip_dir / "b.wav", 1.0)

    rc = cli.main(["ingest", str(clip_dir), "--estimate"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "2 clips" in out
    assert "est cost" in out
    # No index.json should be written in estimate mode.
    assert not (clip_dir / "index.json").exists()


def test_ingest_writes_index(
    tmp_path: Path, fake_transcribe: list[dict], capsys: pytest.CaptureFixture
) -> None:
    clip_dir = tmp_path / "clips"
    clip_dir.mkdir()
    _make_silent_wav(clip_dir / "a.wav", 1.0)

    rc = cli.main(["ingest", str(clip_dir)])

    assert rc == 0
    index_path = clip_dir / "index.json"
    assert index_path.is_file()
    payload = json.loads(index_path.read_text())
    assert payload["version"] == 1
    assert len(payload["clips"]) == 1


def test_ingest_visual_flag_returns_error(
    tmp_path: Path, fake_transcribe: list[dict], capsys: pytest.CaptureFixture
) -> None:
    clip_dir = tmp_path / "clips"
    clip_dir.mkdir()
    _make_silent_wav(clip_dir / "a.wav", 1.0)

    rc = cli.main(["ingest", str(clip_dir), "--visual"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "--visual is not implemented" in err


def test_ingest_empty_dir_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    rc = cli.main(["ingest", str(empty)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "no recognized clip extensions" in err


# ---------------------------------------------------------------------------
# `parallax produce --brief`
# ---------------------------------------------------------------------------

def test_produce_brief_short_circuits_on_missing_assets(
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the brief is incomplete, --brief must exit 1 without producing."""
    brief_path = tmp_path / "brief.yaml"
    _write_brief(brief_path, _brief_payload())
    # No asset on disk → planner returns ok=False.

    # Ensure run_plan never gets called.
    sentinel = {"called": False}

    def _fake_run_plan(**kwargs):
        sentinel["called"] = True
        from parallax.produce import ProductionResult
        return ProductionResult(status="ok", run_id="test", output_dir=tmp_path,
                                final_video=tmp_path / "out.mp4", stills_dir=None,
                                cost_usd=0.0, error=None)

    monkeypatch.setattr(produce_mod, "run_plan", _fake_run_plan)

    rc = cli.main([
        "produce",
        "--folder", str(tmp_path),
        "--brief", str(brief_path),
    ])

    assert rc == 1
    assert sentinel["called"] is False
    err = capsys.readouterr().err
    assert "missing" in err


def test_produce_brief_runs_pipeline_when_valid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: --brief plans then produces from the materialized plan."""
    brief_path = tmp_path / "brief.yaml"
    _write_brief(brief_path, _brief_payload())
    _materialize_logo(tmp_path)

    captured: dict = {}

    def _fake_run_plan(**kwargs):
        captured.update(kwargs)
        from parallax.produce import ProductionResult
        return ProductionResult(status="ok", run_id="test", output_dir=tmp_path,
                                final_video=tmp_path / "out.mp4", stills_dir=None,
                                cost_usd=0.0, error=None)

    monkeypatch.setattr(produce_mod, "run_plan", _fake_run_plan)

    rc = cli.main([
        "produce",
        "--folder", str(tmp_path),
        "--brief", str(brief_path),
    ])

    assert rc == 0
    assert captured["folder"] == str(tmp_path)
    expected_plan = tmp_path / "parallax" / "scratch" / "plan.yaml"
    assert Path(captured["plan_path"]) == expected_plan
    assert expected_plan.is_file()


def test_produce_rejects_brief_and_plan_together(
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """must reject passing both --brief and --plan."""
    brief_path = tmp_path / "brief.yaml"
    plan_path = tmp_path / "plan.yaml"
    brief_path.write_text("goal: x\nscript:\n  scenes: []\n")
    plan_path.write_text("scenes: []\n")

    rc = cli.main([
        "produce",
        "--folder", str(tmp_path),
        "--brief", str(brief_path),
        "--plan", str(plan_path),
    ])
    assert rc == 2
