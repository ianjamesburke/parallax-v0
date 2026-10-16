"""One cost gate for every paid command: no provider call without a recorded human budget.

The approval is a visible file in the project, `<folder>/spend-approval.yaml`, written by
`parallax approve spend`. It holds the budget and who authorized it. Before each paid run the
gate reserves that run's estimate in the file's `reserved` list, so estimate plus everything
already reserved can never exceed the budget. `--yes` and a non-TTY stdout grant nothing.
PARALLAX_TEST_MODE stubs spend nothing, so the gate passes without an approval there.
"""
from __future__ import annotations

import json
import math
import os
import shlex
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

from .shim import is_test_mode

APPROVAL_FILE = "spend-approval.yaml"
_HEADER = "Spend approval. Written by `parallax approve spend`; only a human sets the budget.\n"


class SpendRefused(ValueError):
    """Paid work refused. The message is what/where/fix text followed by a one-line JSON payload."""


def approval_path(folder: str | Path) -> Path:
    return Path(folder).expanduser().resolve() / APPROVAL_FILE


def _yaml() -> YAML:
    yaml = YAML()
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


def _write(path: Path, doc) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            _yaml().dump(doc, fp)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _load(path: Path):
    """Return the validated approval document; raise SpendRefused naming what is wrong."""
    try:
        with path.open(encoding="utf-8") as fp:
            doc = _yaml().load(fp)
    except Exception as exc:
        raise ValueError(f"{path} is not readable YAML ({exc}). Fix it or re-run `parallax approve spend`.") from exc
    budget = (doc or {}).get("budget_usd")
    if isinstance(budget, bool) or not isinstance(budget, (int, float)) or budget <= 0:
        raise ValueError(f"{path}: budget_usd must be a number above 0, got {budget!r}. Re-run `parallax approve spend`.")
    if not str((doc or {}).get("authorized_by") or "").strip():
        raise ValueError(f"{path}: authorized_by is missing. Re-run `parallax approve spend --authorized-by <name>`.")
    reserved = (doc or {}).get("reserved") or []
    if not all(isinstance(r, dict) and isinstance(r.get("usd"), (int, float)) for r in reserved):
        raise ValueError(f"{path}: every reserved entry needs a numeric usd. Fix the file or re-run `parallax approve spend`.")
    return doc


def approve(folder: str | Path, budget_usd: float, authorized_by: str) -> dict:
    """Record a human's budget for this project. Replaces any earlier budget; reservations are kept."""
    if budget_usd <= 0:
        raise ValueError(f"--budget-usd must be above 0, got {budget_usd}")
    if not authorized_by.strip():
        raise ValueError("--authorized-by is required: name the human who authorized this spend")
    root = Path(folder).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"--folder is not a directory: {root}")
    path = approval_path(root)
    if path.is_file():
        doc = _load(path)
    else:
        doc = CommentedMap()
        doc.yaml_set_start_comment(_HEADER.strip())
    doc["budget_usd"] = budget_usd
    doc["authorized_by"] = authorized_by.strip()
    doc["approved_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _write(path, doc)
    reserved = sum(r["usd"] for r in doc.get("reserved") or [])
    return {"wrote": str(path), "budget_usd": budget_usd, "authorized_by": doc["authorized_by"],
            "reserved_usd": round(reserved, 6), "remaining_usd": round(budget_usd - reserved, 6)}


def _refuse(command: str, reason: str, fix: str, estimate: float, folder: str | Path | None,
            reserved: float | None = None, budget: float | None = None) -> SpendRefused:
    folder_arg = shlex.quote(str(folder)) if folder else "<folder>"
    needed = math.ceil((estimate + (reserved or 0.0)) * 100) / 100
    approve_cmd = f"parallax approve spend --folder {folder_arg} --budget-usd <at least {needed:.2f}> --authorized-by <authorizer>"
    payload = {"gate": "spend-approval", "status": "refused", "command": command,
               "estimate_usd": round(estimate, 6), "reserved_usd": reserved, "budget_usd": budget,
               "approval_file": str(approval_path(folder)) if folder else None, "approve_command": approve_cmd,
               "human_decision": "Authorize this spend and set the total budget in USD. Never answer it yourself."}
    return SpendRefused(f"{command} would spend about ${estimate:.4f} but {reason}\nFix: {fix}\n"
                        f"A human must run: {approve_cmd}\n{json.dumps(payload)}")


def require_approval(folder: str | Path | None, estimate_usd: float | None, command: str) -> None:
    """Refuse unless a recorded budget covers `estimate_usd` on top of earlier reservations; then reserve it.

    `folder` is where the approval lives. Free runs (test mode, or an estimate of $0) pass untouched.
    `estimate_usd=None` means the catalog has no price for this call; that is refused, never guessed.
    """
    if is_test_mode():
        return
    if estimate_usd is None:
        raise SpendRefused(f"{command} has no catalog price, so its cost cannot be bounded by a budget. "
                           "Use a model listed in `parallax models list`.")
    if estimate_usd <= 0:
        return
    if folder is None:
        raise _refuse(command, "no --folder says where the approval is recorded.",
                      "pass --folder <project folder>; the approval file lives there.", estimate_usd, None)
    path = approval_path(folder)
    if not path.is_file():
        raise _refuse(command, f"no approval is recorded at {path}.", "ask the human for a budget, then record it.",
                      estimate_usd, folder)
    doc = _load(path)
    reserved = sum(r["usd"] for r in doc.get("reserved") or [])
    budget = float(doc["budget_usd"])
    if reserved + estimate_usd > budget + 1e-9:
        raise _refuse(command, f"the approved budget ${budget:.2f} (authorized by {doc['authorized_by']}) has ${budget - reserved:.4f} left.",
                      "ask the human to raise the budget; approving again replaces the budget and keeps the ledger.",
                      estimate_usd, folder, reserved=round(reserved, 6), budget=budget)
    doc.setdefault("reserved", []).append({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                          "command": command, "usd": round(estimate_usd, 6)})
    _write(path, doc)
