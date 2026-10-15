"""Contract tests for the isolated Codex E2E launcher."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "start-e2e-agent.sh"


def test_dry_run_creates_fixture_from_index_only(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    env = os.environ | {
        "PARALLAX_E2E_DRY_RUN": "1",
        "PARALLAX_E2E_ROOT": str(workspace),
    }

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    assert f"E2E workspace: {workspace}" in result.stdout
    assert (workspace / "fixtures" / "e2e-agent" / "default-project" / "PROJECT.md").is_file()
    assert (workspace / "skills" / "parallax-v0" / "SKILL.md").is_file()
    assert not (workspace / "runs").exists()


def test_refuses_nonempty_requested_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "old-run.txt").write_text("must not be mixed into the fixture")
    env = os.environ | {"PARALLAX_E2E_ROOT": str(workspace)}

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "must be empty" in result.stderr
