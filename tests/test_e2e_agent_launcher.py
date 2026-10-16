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
    assert f"Parallax command: uv run --frozen --project {workspace} parallax" in result.stdout


def test_kickoff_prompt_teaches_the_cli_not_the_skill() -> None:
    prompt = (REPO_ROOT / "fixtures" / "e2e-agent" / "kickoff-prompt.md").read_text()
    assert "parallax --help" in prompt and "parallax guide" in prompt
    assert "Use the Parallax skill" not in prompt
    script = SCRIPT.read_text()
    assert "uv run --frozen --project" in script and "never an installed copy" in script


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


def test_copies_supplied_script_and_typed_references(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    script = tmp_path / "foot-script.md"
    character = tmp_path / "character.webp"
    product = tmp_path / "product.jpg"
    script.write_text('"A supplied testimonial."\n')
    character.write_bytes(b"character input")
    product.write_bytes(b"product input")
    env = os.environ | {
        "PARALLAX_E2E_DRY_RUN": "1",
        "PARALLAX_E2E_ROOT": str(workspace),
    }

    subprocess.run(
        [
            "bash",
            str(SCRIPT),
            "--script",
            str(script),
            "--character-ref",
            str(character),
            "--product-ref",
            str(product),
        ],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    project = workspace / "fixtures" / "e2e-agent" / "default-project"
    assert (project / "script.md").read_text() == script.read_text()
    assert (project / "references" / "character.webp").read_bytes() == character.read_bytes()
    assert (project / "references" / "product.jpg").read_bytes() == product.read_bytes()
    references = (project / "references.md").read_text()
    assert "character-ref" in references
    assert "product-ref" in references
    assert str(character) not in references
    assert str(product) not in references
