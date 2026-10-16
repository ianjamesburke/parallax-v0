"""Run the CLI against freshly resolved dependencies, ignoring uv.lock.

`just install` and install.sh pin to uv.lock, so users get the tested set. This is the early warning
for the next typer/click release: it builds an isolated env without the lock. Needs `uv` and network
access for package resolution; skipped without `uv`.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(shutil.which("uv") is None, reason="needs uv")


def _parallax(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["uv", "run", "--no-project", "--isolated", "--python", "3.11", "--refresh-package", "parallax", "--with", str(REPO), "parallax", *args],
        capture_output=True, text=True, timeout=600,
    )


def test_guide_and_schema_work_on_unlocked_dependencies():
    guide = _parallax("guide")
    assert guide.returncode == 0, guide.stderr
    commands = {c["command"]: c for s in json.loads(guide.stdout)["stages"] for c in s["commands"]}
    assert {"--folder", "--brief"} <= {f["name"] for f in commands["parallax storyboard"]["flags"] if f["required"]}
    assert "--budget-usd" in {f["name"] for f in commands["parallax approve spend"]["flags"]}

    schema = _parallax("schema", "cli")
    assert schema.returncode == 0, schema.stderr
    assert "approve" in json.loads(schema.stdout)["commands"]


def test_usage_error_exits_2_on_unlocked_dependencies():
    result = _parallax("bogus")
    assert result.returncode == 2, result.stderr
