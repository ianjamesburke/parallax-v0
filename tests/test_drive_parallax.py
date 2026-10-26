"""Simulated operator drives the local checkout through the storyboard checkpoint with no skill and no spend."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "fixtures" / "drive-parallax"


def _operator_module():
    spec = importlib.util.spec_from_file_location("drive_parallax_operator", FIXTURE / "operator.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _human(folder: Path):
    """Stand-in for the person: supplies a style reference, declines a product reference."""
    def answer(gate: dict) -> dict:
        if gate["id"] == "editorial-reference":
            (folder / "references").mkdir(exist_ok=True)
            Image.new("RGB", (64, 64), "#445566").save(folder / "references/style.png")
            return {"choice": "supplied", "fill": {"<asset>": "references/style.png"}}
        return {"choice": "no_reference", "fill": {"<reason>": "generic placeholder; no official UI yet"}}
    return answer


def test_simulated_operator_reaches_storyboard_checkpoint_without_spending(tmp_path):
    operator = _operator_module()
    project = tmp_path / "project"
    project.mkdir()
    shutil.copy(FIXTURE / "script.md", project / "script.md")
    # Any provider call would fail twice over: no API key, and a dead proxy.
    env = {k: v for k, v in os.environ.items() if k not in {"OPENROUTER_API_KEY", "PARALLAX_TEST_MODE"}}
    env |= {"HTTPS_PROXY": "http://127.0.0.1:9", "HTTP_PROXY": "http://127.0.0.1:9", "NO_PROXY": ""}

    drive = operator.Operator(project, _human(project), env)
    assert drive.parallax[:2] == ["uv", "run"] and str(REPO) in drive.parallax, "must run the local checkout, not a global install"
    outcome = drive.drive()

    assert outcome["first_status"] == "needs_reference_decision"
    assert [a["gate"] for a in outcome["answers"]] == ["editorial-reference", "product-reference"]
    assert outcome["final_status"] == "ready_for_review"
    assert outcome["produce_refused_while_gates_open"] is True
    assert outcome["provider_calls"] == 0
    package = Path(outcome["package_dir"])
    assert (package / "storyboard.html").is_file()
    assert json.loads((package / "handoff.json").read_text())["status"] == "ready_for_review"
    # No generation artifacts, run logs, or cost records anywhere in the project.
    assert not (project / "parallax").exists()
    assert not list(project.rglob("cost.json")) and not list(project.rglob("run.log"))
    assert not [t for t in drive.transcript if t["argv"][:1] == ["produce"] and t["rc"] == 0]
