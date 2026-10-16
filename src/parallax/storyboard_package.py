"""Portable storyboard package: visible files an editor can open without Parallax.

Layout (all plain files, no hidden state):

    brief.yaml          authored brief, verbatim
    script.md           headline and per-scene narration
    references.yaml     reference inventory, gate decisions, open gates
    scenes.yaml         scene contract with timing
    storyboard.html     offline review document
    storyboard.pdf      printable review document (when renderer is available)
    contact-sheet.jpg   supplied stills in scene order (only when stills exist)
    qa-notes.md         open gates and review advisories
    handoff.json        editor-facing manifest (also the CLI --json output)
    assets/             every referenced file that exists, at its brief-relative path
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import yaml
from PIL import Image, ImageOps

from .brief import Brief
from .reference_gates import _arg, open_gates, undeclared_image_refs, undeclared_message
from .storyboard import render_storyboard, review_storyboard

_FILES = {
    "brief.yaml": "Authored brief, verbatim. Source of truth for every other file.",
    "script.md": "Opening headline and per-scene narration.",
    "references.yaml": "Reference inventory with provenance, gate decisions, and open gates.",
    "scenes.yaml": "Scene contract: type, narration, direction, references, locked assets, timing.",
    "storyboard.html": "Offline review document. Open in any browser.",
    "storyboard.pdf": "Printable offline review document.",
    "contact-sheet.jpg": "Supplied stills in scene order.",
    "qa-notes.md": "Open reference gates and review advisories.",
    "handoff.json": "This manifest.",
}
_CELL, _COLUMNS = 320, 4
FOLDER_PLACEHOLDER, BRIEF_PLACEHOLDER = "<project-folder>", "<brief.yaml>"


def find_pdf_renderer() -> list[str] | None:
    """Find a command template for headless PDF rendering.

    Prefers Chrome / Chromium / Edge / Brave for faithful CSS print rendering.
    Falls back to wkhtmltopdf or weasyprint.
    """
    candidates = [
        shutil.which("google-chrome"),
        shutil.which("google-chrome-stable"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        shutil.which("microsoft-edge"),
        shutil.which("brave"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    ]
    for c in candidates:
        if c and Path(c).is_file() and os.access(c, os.X_OK):
            return [str(c), "--headless", "--disable-gpu", "--no-pdf-header-footer", "--print-to-pdf={output}", "{input}"]

    if wk := shutil.which("wkhtmltopdf"):
        return [wk, "--enable-local-file-access", "--quiet", "{input}", "{output}"]
    if wp := shutil.which("weasyprint"):
        return [wp, "--quiet", "{input}", "{output}"]
    return None


def render_pdf(html_path: str | Path, pdf_path: str | Path, *, renderer: list[str] | None = None) -> bool:
    """Render an HTML file to PDF using an available headless renderer. Returns True on success."""
    cmd_template = renderer or find_pdf_renderer()
    if not cmd_template:
        return False
    html_target = Path(html_path).expanduser().resolve()
    pdf_target = Path(pdf_path).expanduser().resolve()
    if not html_target.is_file():
        return False
    cmd = [arg.replace("{input}", str(html_target)).replace("{output}", str(pdf_target)) for arg in cmd_template]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=45)
        return res.returncode == 0 and pdf_target.is_file() and pdf_target.stat().st_size > 0
    except (subprocess.SubprocessError, OSError):
        return False


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


def _blocking(gates: list[dict]) -> list[dict]:
    """Gates that need a human answer before review. An authorized, ungenerated anchor does not: review precedes generation."""
    return [g for g in gates if g["state"] == "open"]


def _qa_notes(manifest: dict) -> str:
    lines = ["# QA notes", "", "These are proposals, not approval. A human reviews the storyboard before generation.", ""]
    lines += ["## Open reference gates", ""]
    for gate in manifest["gates"]:
        lines += [f"- **{gate['id']}** [{gate['state']}]: {gate['question']}"]
        lines += [f"  - Scene {int(i):02d}: anchor {note}, not yet generated." for i, note in gate.get("authorized_scenes", {}).items()]
        lines += [f"  - `{c['id']}`: {c['summary']} ({c['human_decision']})" for c in gate["choices"]]
    if not manifest["gates"]:
        lines += ["None."]
    lines += ["", "## Advisories (non-blocking; a human decides)", ""]
    for a in manifest["advisories"]:
        where = f"Scene {a['scene']:02d}" if a["scene"] is not None else "Storyboard"
        lines += [f"- {where} [{a['kind']}]: {a['message']}{' (locked_by_narration: narration is final, so the fix is visual; the advisory still stands)' if a.get('locked_by_narration') else ''}"]
        lines += [f"  - {r['id']}: {r['how']}" for r in a.get("remedies", [])]
    lines += [] if manifest["advisories"] else ["None."]
    lines += ["", "## Missing assets", ""]
    lines += [f"- {path}" for path in manifest["missing_assets"]] or ["None."]
    return "\n".join(lines) + "\n"


def _advisories(review: dict) -> list[dict]:
    """One vocabulary for everything non-blocking: scene advisories, pacing, and visual variety."""
    scene = [{"scene": index, **a} for index, items in review["scene_advisories"].items() for a in items]
    pacing = [{"scene": w.get("scene_index"), "kind": w["code"], "message": w["message"], "remedies": w["remedies"],
               "locked_by_narration": w["locked_by_narration"]} for w in review["pacing"]["warnings"]]
    variety = [{"scene": None, **w} for w in review["variety"]]
    return scene + pacing + variety


def _produce_block(review: dict, missing: list[str]) -> dict:
    """State plainly whether `parallax produce` would refuse right now, and why. Status only describes review readiness."""
    blocked_by = [{"kind": "reference_gate", "id": g["id"], "state": g["state"], "scenes": g["scenes"]} for g in review["gates"]]
    clips = [i for i, items in review["scene_advisories"].items() if any(a["kind"] == "motion_graphic_clip_missing" for a in items)]
    if clips:
        blocked_by.append({"kind": "motion_graphic_clip_missing", "scenes": clips})
    if missing:
        blocked_by.append({"kind": "missing_asset", "paths": missing})
    return {"produce_blocked": bool(blocked_by), "blocked_by": blocked_by}


def _next_steps(gates: list[dict], folder: str, brief_path: str) -> list[dict]:
    if _blocking(gates):
        return [{"step": "answer_gates", "detail": "Ask the human each open gate question, run the chosen choice command, then re-run `parallax storyboard`."}]
    steps = [{"step": "human_review", "detail": "The human reviews storyboard.html and approves or requests changes. Approval is not recorded by the CLI."}]
    if gates:
        steps.append({"step": "attach_authorized_anchor", "detail": "A generated anchor is authorized but not generated. Before any `parallax produce`, generate it and attach it with the gate's choice command; produce refuses until then."})
    return steps + [{"step": "plan", "command": f"parallax plan --folder {_arg(folder)} --brief {_arg(brief_path)}"}]


def _load_brief(brief_path: Path) -> Brief:
    brief = Brief.from_yaml(brief_path)
    if missing := undeclared_image_refs(brief):
        raise ValueError("; ".join(undeclared_message(scene, ref) for scene, ref in missing))
    return brief


def _manifest(brief: Brief, review: dict, files: dict[str, str], contact: list[int], missing: list[str],
              folder: str, brief_path: str) -> dict:
    timeline = review["timeline"]
    return {
        "package_version": 1,
        "concept": brief.concept_name or brief.goal,
        "goal": brief.goal,
        "aspect": brief.aspect,
        "scene_count": len(brief.script.scenes),
        "status": "needs_reference_decision" if _blocking(review["gates"]) else "ready_for_review",
        "approved": False,
        "requires_parallax": False,
        "provider_calls": 0,
        "files": [{"path": name, "role": role} for name, role in files.items()],
        "asset_root": "assets",
        "contact_sheet_scenes": contact,
        "missing_assets": missing,
        "timing": {"basis": review["timing_basis"], "total_s": round(float(timeline[-1]["end_s"]), 3) if timeline else 0.0},
        "gates": review["gates"],
        **_produce_block(review, missing),
        "advisories": _advisories(review),
        "next_steps": _next_steps(review["gates"], folder, brief_path),
    }


def write_package(brief_path: str | Path, folder: str | Path, package_dir: str | Path,
                  timing_path: str | Path | None = None, *, pdf: bool = True) -> dict:
    """Write the storyboard package and return its handoff manifest. Makes no provider calls."""
    brief_path = Path(brief_path).expanduser().resolve()
    root = Path(folder).expanduser().resolve()
    package = Path(package_dir).expanduser().resolve()
    if package.exists() and any(package.iterdir()) and not (package / "handoff.json").is_file():
        raise ValueError(f"{package} exists and is not a storyboard package (no handoff.json); choose an empty --package-dir")
    brief = _load_brief(brief_path)
    review = review_storyboard(brief, root, brief_path, _load_timing(timing_path) if timing_path else None)
    package.mkdir(parents=True, exist_ok=True)
    assets = package / "assets"
    missing = _copy_assets(brief, root, assets)
    timeline = review["timeline"]
    contact = _contact_sheet(brief, assets, package / "contact-sheet.jpg")

    (package / "brief.yaml").write_bytes(brief_path.read_bytes())
    (package / "script.md").write_text(_script_md(brief), encoding="utf-8")
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

    # Files on disk carry placeholders instead of this machine's paths; the returned manifest has exact commands.
    portable = {**review, "gates": open_gates(brief, FOLDER_PLACEHOLDER, BRIEF_PLACEHOLDER)}
    (package / "storyboard.html").write_text(render_storyboard(brief, assets, portable, link_base=package), encoding="utf-8")
    pdf_created = False
    if pdf:
        pdf_created = render_pdf(package / "storyboard.html", package / "storyboard.pdf")

    files = {
        name: role for name, role in _FILES.items()
        if (name != "contact-sheet.jpg" or contact) and (name != "storyboard.pdf" or pdf_created)
    }
    on_disk = _manifest(brief, portable, files, contact, missing, FOLDER_PLACEHOLDER, BRIEF_PLACEHOLDER)
    (package / "qa-notes.md").write_text(_qa_notes(on_disk), encoding="utf-8")
    (package / "handoff.json").write_text(json.dumps(on_disk, indent=2) + "\n", encoding="utf-8")
    return _manifest(brief, review, files, contact, missing, str(root), str(brief_path))


def check_storyboard(brief_path: str | Path, folder: str | Path, timing_path: str | Path | None = None) -> dict:
    """Same manifest as write_package (status, gates, advisories) without writing anything."""
    brief_path = Path(brief_path).expanduser().resolve()
    root = Path(folder).expanduser().resolve()
    brief = _load_brief(brief_path)
    review = review_storyboard(brief, root, brief_path, _load_timing(timing_path) if timing_path else None)
    missing = [raw for raw in _referenced_paths(brief) if not (root / raw).is_file()]
    return {"checked_only": True, **_manifest(brief, review, {}, [], missing, str(root), str(brief_path))}
