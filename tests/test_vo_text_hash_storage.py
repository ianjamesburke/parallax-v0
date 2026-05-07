"""Tests for _init_vo_text_hashes in produce.py."""
from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

from parallax.produce import _init_vo_text_hashes


def _write_plan(path: Path, data: dict) -> None:
    with path.open("w") as f:
        yaml.safe_dump(data, f, default_flow_style=False, allow_unicode=True,
                       sort_keys=False, width=10000)


def _read_plan(path: Path) -> dict:
    with path.open() as f:
        return yaml.safe_load(f) or {}


def test_hashes_written_on_first_lock(tmp_path):
    plan_path = tmp_path / "plan.yaml"
    plan = {
        "audio_path": "audio.mp3",
        "scenes": [
            {"index": 0, "vo_text": "Hello world"},
            {"index": 1, "vo_text": "Second scene"},
        ],
    }
    _write_plan(plan_path, plan)

    _init_vo_text_hashes(plan, plan_path)

    assert "vo_text_hashes" in plan
    assert plan["vo_text_hashes"]["0"] == hashlib.sha256(b"Hello world").hexdigest()[:16]
    assert plan["vo_text_hashes"]["1"] == hashlib.sha256(b"Second scene").hexdigest()[:16]

    disk = _read_plan(plan_path)
    assert disk["vo_text_hashes"]["0"] == plan["vo_text_hashes"]["0"]
    assert disk["vo_text_hashes"]["1"] == plan["vo_text_hashes"]["1"]


def test_hashes_not_overwritten_on_subsequent_run(tmp_path):
    plan_path = tmp_path / "plan.yaml"
    original_hash = hashlib.sha256(b"Original text").hexdigest()[:16]
    plan = {
        "audio_path": "audio.mp3",
        "vo_text_hashes": {"0": original_hash},
        "scenes": [{"index": 0, "vo_text": "Changed text"}],
    }
    _write_plan(plan_path, plan)

    _init_vo_text_hashes(plan, plan_path)

    assert plan["vo_text_hashes"]["0"] == original_hash


def test_no_hashes_written_when_no_audio_path(tmp_path):
    plan_path = tmp_path / "plan.yaml"
    plan = {
        "scenes": [{"index": 0, "vo_text": "Some text"}],
    }
    _write_plan(plan_path, plan)

    _init_vo_text_hashes(plan, plan_path)

    assert "vo_text_hashes" not in plan
    disk = _read_plan(plan_path)
    assert "vo_text_hashes" not in disk


def test_empty_vo_text_hashed_correctly(tmp_path):
    plan_path = tmp_path / "plan.yaml"
    plan = {
        "audio_path": "audio.mp3",
        "scenes": [{"index": 0}],  # no vo_text key
    }
    _write_plan(plan_path, plan)

    _init_vo_text_hashes(plan, plan_path)

    expected = hashlib.sha256(b"").hexdigest()[:16]
    assert plan["vo_text_hashes"]["0"] == expected
