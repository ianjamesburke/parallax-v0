"""verify init scaffolder tests.

Covers minimal-starter mode, --from copy mode, --resolution rewrite,
overwrite refusal, --force, bad inputs, and a roundtrip that the
scaffolded case actually passes verify suite.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from parallax.verify_suite import cli_init, cli_run, init_case
from parallax.ffmpeg_utils import _ffmpeg_has_drawtext

_requires_drawtext = pytest.mark.skipif(
    not _ffmpeg_has_drawtext(),
    reason="ffmpeg on PATH lacks drawtext filter (install ffmpeg-full or a freetype-enabled build)",
)


REF_CASE = Path(__file__).parent.parent / "tests" / "integration" / "res-720x1280"


# Resolve the on-disk path independent of where pytest is invoked from —
# tests/integration/res-720x1280/ relative to the repo root.
def _ref_case() -> Path:
    return Path(__file__).parent / "integration" / "res-720x1280"


# --------------------------------------------------------------------------
# Minimal starter (no --from)
# --------------------------------------------------------------------------

def test_init_minimal_starter_creates_three_files(tmp_path):
    target = tmp_path / "starter"
    init_case(target)
    assert (target / "plan.yaml").is_file()
    assert (target / "expected.yaml").is_file()
    assert (target / "README.md").is_file()


def test_init_minimal_starter_plan_has_one_scene(tmp_path):
    target = tmp_path / "starter"
    init_case(target)
    plan = yaml.safe_load((target / "plan.yaml").read_text())
    assert plan["resolution"] == "1080x1920"
    assert isinstance(plan.get("scenes"), list)
    assert len(plan["scenes"]) == 1


def test_init_minimal_starter_expected_has_name_and_final(tmp_path):
    target = tmp_path / "my-case"
    init_case(target)
    expected = yaml.safe_load((target / "expected.yaml").read_text())
    assert expected["name"] == "my-case"
    assert expected["paid"] is False
    assert expected["final"]["resolution"] == "1080x1920"


# --------------------------------------------------------------------------
# Copy mode (--from)
# --------------------------------------------------------------------------

def test_init_from_existing_copies_all_files(tmp_path):
    target = tmp_path / "copied"
    init_case(target, from_dir=_ref_case())
    assert (target / "plan.yaml").is_file()
    assert (target / "expected.yaml").is_file()
    assert (target / "README.md").is_file()
    # Identical content as source.
    src_plan = (_ref_case() / "plan.yaml").read_text()
    assert (target / "plan.yaml").read_text() == src_plan


def test_init_from_with_resolution_rewrites_both_files(tmp_path):
    target = tmp_path / "res-480"
    init_case(target, from_dir=_ref_case(), resolution="480x854")
    plan = yaml.safe_load((target / "plan.yaml").read_text())
    expected = yaml.safe_load((target / "expected.yaml").read_text())
    assert plan["resolution"] == "480x854"
    assert expected["final"]["resolution"] == "480x854"
    # Per-stage resolution assertions also get rewritten — otherwise the
    # scaffolded case would fail immediately on every run.
    assert expected["stages"]["assemble"]["resolution"] == "480x854"
    # name: refreshed to the new folder name so the [PASS] line is honest.
    assert expected["name"] == "res-480"


@_requires_drawtext
def test_init_from_with_resolution_roundtrips_to_passing_run(tmp_path, capsys, monkeypatch):
    """Scaffolding with --resolution produces a case that passes verify suite."""
    monkeypatch.setenv("PARALLAX_TEST_MODE", "1")
    target = tmp_path / "res-480"
    init_case(target, from_dir=_ref_case(), resolution="480x854")
    rc = cli_run(target)
    out = capsys.readouterr().out
    assert "[PASS] res-480" in out, out
    assert rc == 0


def test_init_from_invalid_source_raises(tmp_path):
    bogus = tmp_path / "not-a-case"
    bogus.mkdir()
    target = tmp_path / "out"
    with pytest.raises(ValueError, match="not a valid case folder"):
        init_case(target, from_dir=bogus)


def test_init_from_missing_source_raises(tmp_path):
    target = tmp_path / "out"
    with pytest.raises(FileNotFoundError, match="--from source not found"):
        init_case(target, from_dir=tmp_path / "does-not-exist")


# --------------------------------------------------------------------------
# Resolution validation
# --------------------------------------------------------------------------

def test_init_with_bad_resolution_raises(tmp_path):
    target = tmp_path / "bad"
    with pytest.raises(ValueError, match="--resolution"):
        init_case(target, resolution="not-a-resolution")


# --------------------------------------------------------------------------
# Overwrite protection
# --------------------------------------------------------------------------

def test_init_refuses_existing_target(tmp_path):
    target = tmp_path / "existing"
    target.mkdir()
    with pytest.raises(FileExistsError, match="already exists"):
        init_case(target)


def test_init_force_overwrites_existing(tmp_path):
    target = tmp_path / "existing"
    target.mkdir()
    (target / "junk.txt").write_text("old content")
    init_case(target, force=True)
    assert (target / "plan.yaml").is_file()
    assert not (target / "junk.txt").exists()


# --------------------------------------------------------------------------
# CLI wrapper exit codes
# --------------------------------------------------------------------------

def test_cli_init_returns_zero_on_success(tmp_path, capsys):
    rc = cli_init(tmp_path / "ok")
    out = capsys.readouterr().out
    assert rc == 0
    assert "Wrote case scaffold" in out


def test_cli_init_returns_nonzero_on_existing_target(tmp_path, capsys):
    target = tmp_path / "existing"
    target.mkdir()
    rc = cli_init(target)
    err = capsys.readouterr().err
    assert rc == 1
    assert "already exists" in err


# --------------------------------------------------------------------------
# Roundtrip — scaffolded case passes verify suite
# --------------------------------------------------------------------------

@_requires_drawtext
def test_roundtrip_scaffolded_case_passes_verify_suite(tmp_path, capsys, monkeypatch):
    """verify init --from <ref> | verify suite <new> → [PASS]."""
    monkeypatch.setenv("PARALLAX_TEST_MODE", "1")
    target = tmp_path / "roundtrip"
    init_case(target, from_dir=_ref_case())
    rc = cli_run(target)
    out = capsys.readouterr().out
    assert "[PASS]" in out, out
    assert rc == 0
