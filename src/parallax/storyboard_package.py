"""Portable storyboard package: visible files an editor can open without Parallax.

Layout (all plain files, no hidden state):

    brief.yaml          authored brief, verbatim
    script.md           headline and per-scene narration
    references.yaml     reference inventory, gate decisions, open gates
    scenes.yaml         scene contract with timing
    storyboard.html     offline review document
    contact-sheet.jpg   supplied stills in scene order (only when stills exist)
    qa-notes.md         open gates and review advisories
    handoff.json        editor-facing manifest (also the CLI --json output)
    assets/             every referenced file that exists, at its brief-relative path
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import yaml
from PIL import Image, ImageOps

from .brief import Brief
from .storyboard import render_storyboard, review_storyboard

_FILES = {
    "brief.yaml": "Authored brief, verbatim. Source of truth for every other file.",
    "script.md": "Opening headline and per-scene narration.",
    "references.yaml": "Reference inventory with provenance, gate decisions, and open gates.",
    "scenes.yaml": "Scene contract: type, narration, direction, references, locked assets, timing.",
    "storyboard.html": "Offline review document. Open in any browser.",
    "contact-sheet.jpg": "Supplied stills in scene order.",
    "qa-notes.md": "Open reference gates and review advisories.",
    "handoff.json": "This manifest.",
}
_CELL, _COLUMNS = 320, 4


def _load_timing(path: str | Path) -> list[dict]:
    payload = yaml.safe_load(Path(path).expanduser().read_text(encoding="utf-8"))
    timeline = payload.get("scenes") if isinstance(payload, dict) else payload
    if not isinstance(timeline, list) or not all(isinstance(s, dict) and "start_s" in s and "end_s" in s for s in timeline):
        raise ValueError("storyboard --timing requires an aligned manifest with scenes containing index, start_s, end_s")
    return timeline


def _referenced_paths(brief: Brief) -> list[str]:
    paths = [a.path for a in brief.assets.provided]
    for scene in brief.script.scenes:
        paths += [p for p in (scene.still_path, scene.clip_path, *scene.image_refs) if p]
    return list(dict.fromkeys(paths))


def _copy_assets(brief: Brief, root: Path, assets: Path) -> list[str]:
    """Copy referenced files into the package; return referenced paths that do not exist."""
    shutil.rmtree(assets, ignore_errors=True)
    missing = []
    for raw in _referenced_paths(brief):
        relative = Path(raw)
        if relative.is_absolute() or not (root / relative).resolve().is_relative_to(root):
            raise ValueError(
                f"asset {raw!r} is outside the project folder {root}; a portable package needs every asset "
                "under --folder. Move or copy the file into the project and update the brief path."
            )
        source = root / relative
        if not source.is_file():
            missing.append(raw)
            continue
        destination = assets / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    return missing


def _contact_sheet(brief: Brief, assets: Path, destination: Path) -> list[int]:
    """Write a grid of supplied stills; return the scene indices it contains."""
    tiles, indexes = [], []
    for scene in brief.script.scenes:
        if scene.still_path and (assets / scene.still_path).is_file():
            try:
                with Image.open(assets / scene.still_path) as source:
                    tiles.append(ImageOps.pad(ImageOps.exif_transpose(source).convert("RGB"), (_CELL, _CELL), color="#101817"))
            except OSError as exc:
                raise ValueError(f"scene {scene.index}: still {scene.still_path!r} is not a readable image: {exc}") from exc
            indexes.append(scene.index)
    if not tiles:
        destination.unlink(missing_ok=True)
        return []
    rows = -(-len(tiles) // _COLUMNS)
    sheet = Image.new("RGB", (min(len(tiles), _COLUMNS) * _CELL, rows * _CELL), "#101817")
    for position, tile in enumerate(tiles):
        sheet.paste(tile, (position % _COLUMNS * _CELL, position // _COLUMNS * _CELL))
    sheet.save(destination, format="JPEG", quality=85)
    return indexes


def _dump(data: dict) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def _script_md(brief: Brief) -> str:
    lines = [f"# {brief.concept_name or brief.goal}", ""]
    if brief.headline:
        lines += ["## Opening headline", "", "On-screen overlay during the first scene. Not spoken.", "", brief.headline, ""]
    lines += ["## Narration", ""]
    for scene in brief.script.scenes:
        lines += [f"### Scene {scene.index:02d} ({scene.resolved_scene_type})", "", scene.vo_text, ""]
    return "\n".join(lines)


def _status(gates: list[dict]) -> str:
    if not gates:
        return "ready_for_review"
    return "awaiting_anchor_generation" if all(g["state"] == "authorized_pending_generation" for g in gates) else "needs_reference_decision"


def _qa_notes(manifest: dict) -> str:
    lines = ["# QA notes", "", "These are proposals, not approval. A human reviews the storyboard before generation.", ""]
    lines += ["## Open reference gates", ""]
    for gate in manifest["gates"]:
        lines += [f"- **{gate['id']}** [{gate['state']}]: {gate['question']}"]
        lines += [f"  - Scene {int(i):02d}: anchor {note}, not yet generated." for i, note in gate.get("authorized_scenes", {}).items()]
        lines += [f"  - `{c['id']}`: {c['summary']} ({c['human_decision']})" for c in gate["choices"]]
    if not manifest["gates"]:
        lines += ["None."]
    lines += ["", "## Scene advisories", ""]
    for a in manifest["advisories"]:
        lines += [f"- Scene {a['scene']:02d} [{a['kind']}]: {a['message']}"]
        lines += [f"  - {r['id']}: {r['how']}" for r in a.get("remedies", [])]
    lines += [] if manifest["advisories"] else ["None."]
    lines += ["", "## Visual variety", ""]
    lines += [f"- {w['message']}" for w in manifest["variety"]] or ["None."]
    lines += ["", "## Opening pacing", ""]
    for w in manifest["pacing_warnings"]:
        lines += [f"- {w['message']}{' (acknowledged: narration is locked)' if w['acknowledged'] else ''}", f"  - remedy: {w['remedy']}"]
    lines += [] if manifest["pacing_warnings"] else ["None."]
    lines += ["", "## Missing assets", ""]
    lines += [f"- {path}" for path in manifest["missing_assets"]] or ["None."]
    return "\n".join(lines) + "\n"


def write_package(brief_path: str | Path, folder: str | Path, package_dir: str | Path,
                  timing_path: str | Path | None = None) -> dict:
    """Write the storyboard package and return its handoff manifest. Makes no provider calls."""
    brief_path = Path(brief_path).expanduser().resolve()
    root = Path(folder).expanduser().resolve()
    package = Path(package_dir).expanduser().resolve()
    if package.exists() and any(package.iterdir()) and not (package / "handoff.json").is_file():
        raise ValueError(f"{package} exists and is not a storyboard package (no handoff.json); choose an empty --package-dir")
    brief = Brief.from_yaml(brief_path)
    review = review_storyboard(brief, root, brief_path, _load_timing(timing_path) if timing_path else None)
    package.mkdir(parents=True, exist_ok=True)
    assets = package / "assets"
    missing = _copy_assets(brief, root, assets)
    timeline = review["timeline"]
    contact = _contact_sheet(brief, assets, package / "contact-sheet.jpg")

    (package / "brief.yaml").write_bytes(brief_path.read_bytes())
    (package / "script.md").write_text(_script_md(brief), encoding="utf-8")
    (package / "storyboard.html").write_text(render_storyboard(brief, assets, review, link_base=package), encoding="utf-8")
    (package / "references.yaml").write_text(_dump({
        "asset_root": "assets",
        "review_status_note": "review_status is set to approved by editing brief.yaml after the human accepts the asset. The CLI never approves.",
        "provided": [{**a.model_dump(exclude_none=True), "present": a.path not in missing} for a in brief.assets.provided],
        "decisions": [d.model_dump() for d in brief.reference_decisions],
        "open_gates": [g["id"] for g in review["gates"]],
    }), encoding="utf-8")
    (package / "scenes.yaml").write_text(_dump({
        "asset_root": "assets",
        "timing_basis": review["timing_basis"],
        "scenes": [
            {**scene.model_dump(exclude_none=True), "scene_type": scene.resolved_scene_type,
             "start_s": round(float(t["start_s"]), 3), "end_s": round(float(t["end_s"]), 3)}
            for scene, t in zip(brief.script.scenes, timeline)
        ],
    }), encoding="utf-8")

    files = {name: role for name, role in _FILES.items() if name != "contact-sheet.jpg" or contact}
    manifest = {
        "package_version": 1,
        "concept": brief.concept_name or brief.goal,
        "goal": brief.goal,
        "aspect": brief.aspect,
        "scene_count": len(brief.script.scenes),
        "status": _status(review["gates"]),
        "approved": False,
        "requires_parallax": False,
        "provider_calls": 0,
        "files": [{"path": name, "role": role} for name, role in files.items()],
        "asset_root": "assets",
        "contact_sheet_scenes": contact,
        "missing_assets": missing,
        "timing": {"basis": review["timing_basis"], "total_s": round(float(timeline[-1]["end_s"]), 3) if timeline else 0.0},
        "gates": review["gates"],
        "advisories": [{"scene": index, **a} for index, items in review["scene_advisories"].items() for a in items],
        "variety": review["variety"],
        "pacing_warnings": review["pacing"]["warnings"],
        "next_steps": (
            [{"step": "answer_gates", "detail": "Ask the human each gate question, run the chosen choice command, then re-run `parallax storyboard`. A gate in state authorized_pending_generation needs its anchor generated and attached."}]
            if review["gates"] else
            [{"step": "human_review", "detail": "The human reviews storyboard.html and approves or requests changes. Approval is not recorded by the CLI."},
             {"step": "plan", "command": f"parallax plan --folder {root} --brief {brief_path}"}]
        ),
    }
    (package / "qa-notes.md").write_text(_qa_notes(manifest), encoding="utf-8")
    (package / "handoff.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest
