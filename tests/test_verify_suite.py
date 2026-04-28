"""verify-suite runner tests.

Covers the schema branches, paid-gating, empty-suite handling, and the
deliberate-mutation path so we know the failure renderer reports the
specific assertion that broke.

Each test uses the smoke fixture (or a trimmed copy of it) and runs in
PARALLAX_TEST_MODE=1 — no network, no spend.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
import yaml

from parallax.settings import ProductionMode
from parallax.verify_suite import (
    CaseResult,
    cli_run,
    load_expected,
    run_case,
    run_suite,
)


SMOKE_DIR = Path(__file__).parent / "fixtures" / "verify_suite_smoke"
SMOKE_BASIC = SMOKE_DIR / "basic"


@pytest.fixture(autouse=True)
def _force_test_mode(monkeypatch):
    """Every test runs in mock mode — no chance of touching real APIs."""
    monkeypatch.setenv("PARALLAX_TEST_MODE", "1")


# --------------------------------------------------------------------------
# load_expected — schema validation
# --------------------------------------------------------------------------

def test_load_expected_smoke_fixture():
    data = load_expected(SMOKE_BASIC / "expected.yaml")
    assert data["name"] == "basic"
    assert "final" in data and "stages" in data and "manifest" in data and "run_log" in data


def test_load_expected_unknown_top_level_key_raises(tmp_path):
    bad = tmp_path / "expected.yaml"
    bad.write_text("totally_invented_key: 1\n")
    with pytest.raises(ValueError, match="unknown top-level keys"):
        load_expected(bad)


def test_load_expected_unknown_stage_key_raises(tmp_path):
    bad = tmp_path / "expected.yaml"
    bad.write_text("stages:\n  stills:\n    bogus: yes\n")
    with pytest.raises(ValueError, match="unknown keys in stages.stills"):
        load_expected(bad)


# --------------------------------------------------------------------------
# Empty / non-matching suites
# --------------------------------------------------------------------------

def test_run_suite_empty_dir_returns_no_results(tmp_path, capsys):
    rc = cli_run(tmp_path)
    captured = capsys.readouterr()
    assert "0 cases run" in captured.out
    assert rc == 0


def test_run_suite_skips_subdirs_missing_required_files(tmp_path):
    # Subdir with only a plan but no expected — should be skipped silently.
    incomplete = tmp_path / "incomplete"
    incomplete.mkdir()
    (incomplete / "plan.yaml").write_text("voice: Kore\nscenes: []\n")
    results = run_suite(tmp_path)
    assert results == []


# --------------------------------------------------------------------------
# Single passing case → exit 0
# --------------------------------------------------------------------------

def test_run_smoke_case_passes(capsys):
    rc = cli_run(SMOKE_DIR)
    captured = capsys.readouterr()
    assert "[PASS] basic" in captured.out
    assert rc == 0


def test_run_case_returns_passing_caseresult():
    result = run_case(SMOKE_BASIC, mode=ProductionMode.TEST)
    assert isinstance(result, CaseResult)
    assert result.passed is True, f"unexpected failures: {result.failures}"
    assert result.failures == []
    assert result.duration_s > 0
    # cost_usd_max=0.0 is enforced — test mode should always be free.
    assert result.cost_usd == 0.0


# --------------------------------------------------------------------------
# Mutation → specific failure rendered
# --------------------------------------------------------------------------

def _copy_smoke_to(tmp_path: Path) -> Path:
    """Copy the smoke suite into a tmp_path so each test can mutate freely."""
    dest = tmp_path / "verify_suite_smoke"
    shutil.copytree(SMOKE_DIR, dest)
    return dest


def _mutate_expected(case_dir: Path, mutator) -> None:
    """Round-trip expected.yaml through a mutator function."""
    path = case_dir / "expected.yaml"
    data = yaml.safe_load(path.read_text())
    mutator(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def test_mutated_resolution_fails_with_specific_message(tmp_path, capsys):
    suite = _copy_smoke_to(tmp_path)
    _mutate_expected(suite / "basic", lambda d: d["final"].update({"resolution": "9999x9999"}))
    rc = cli_run(suite)
    out = capsys.readouterr().out
    assert "[FAIL] basic" in out
    assert "final.resolution" in out
    assert "9999x9999" in out
    assert rc == 1


def test_mutated_scene_count_fails(tmp_path, capsys):
    suite = _copy_smoke_to(tmp_path)
    _mutate_expected(suite / "basic", lambda d: d["final"].update({"scene_count": 99}))
    rc = cli_run(suite)
    out = capsys.readouterr().out
    assert "final.scene_count" in out
    assert rc == 1


def test_mutated_manifest_keys_fails(tmp_path, capsys):
    suite = _copy_smoke_to(tmp_path)
    _mutate_expected(
        suite / "basic",
        lambda d: d["manifest"]["keys_required"].append("nonexistent_top_key"),
    )
    rc = cli_run(suite)
    out = capsys.readouterr().out
    assert "manifest.keys_required" in out
    assert "nonexistent_top_key" in out
    assert rc == 1


def test_mutated_run_log_must_contain_fails(tmp_path, capsys):
    suite = _copy_smoke_to(tmp_path)
    _mutate_expected(
        suite / "basic",
        lambda d: d["run_log"]["must_contain"].append("DEFINITELY_NOT_IN_LOG_xyz123"),
    )
    rc = cli_run(suite)
    out = capsys.readouterr().out
    assert "run_log.must_contain" in out
    assert "DEFINITELY_NOT_IN_LOG_xyz123" in out
    assert rc == 1


# --------------------------------------------------------------------------
# Paid-gating
# --------------------------------------------------------------------------

def test_paid_case_skipped_without_flag(tmp_path, capsys):
    suite = _copy_smoke_to(tmp_path)
    _mutate_expected(suite / "basic", lambda d: d.update({"paid": True}))
    rc = cli_run(suite, paid=False)
    out = capsys.readouterr().out
    assert "[SKIP] basic" in out
    assert "paid" in out
    assert rc == 0


def test_paid_case_runs_with_flag(tmp_path, capsys):
    suite = _copy_smoke_to(tmp_path)
    _mutate_expected(suite / "basic", lambda d: d.update({"paid": True}))
    rc = cli_run(suite, paid=True)
    out = capsys.readouterr().out
    assert "[PASS] basic" in out
    assert rc == 0


# --------------------------------------------------------------------------
# --case filter
# --------------------------------------------------------------------------

def test_case_filter_matching(tmp_path, capsys):
    suite = _copy_smoke_to(tmp_path)
    rc = cli_run(suite, case="basic")
    out = capsys.readouterr().out
    assert "[PASS] basic" in out
    assert rc == 0


def test_case_filter_no_match(tmp_path, capsys):
    suite = _copy_smoke_to(tmp_path)
    rc = cli_run(suite, case="does-not-exist")
    out = capsys.readouterr().out
    assert "0 cases run" in out
    assert rc == 0


# --------------------------------------------------------------------------
# Cost guardrail
# --------------------------------------------------------------------------

def test_cost_guardrail_zero_passes_in_test_mode(tmp_path):
    """cost_usd_max=0.0 is the canonical check that test mode stays free."""
    suite = _copy_smoke_to(tmp_path)
    result = run_case(suite / "basic", mode=ProductionMode.TEST)
    assert result.passed is True, result.failures
    assert result.cost_usd == 0.0
