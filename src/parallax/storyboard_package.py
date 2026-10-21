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
import hashlib
import re
from datetime import date

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
        os.environ.get("PARALLAX_CHROME"),
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
        try:
            os.link(source, destination)
        except OSError:
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


def _next_steps(gates: list[dict], folder: str, brief_path: str, rerun_command: str | None = None) -> list[dict]:
    if rerun_command:
        return [{"step": "human_review", "detail": "The human reviews storyboard.html and requests changes if needed."},
                {"step": "rerun_after_review", "command": rerun_command}]
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
              folder: str, brief_path: str, rerun_command: str | None = None) -> dict:
    timeline = review["timeline"]
    block = _produce_block(review, missing)
    return {
        "package_version": 1,
        "concept": brief.concept_name or brief.goal,
        "goal": brief.goal,
        "aspect": brief.aspect,
        "scene_count": len(brief.script.scenes),
        "status": "needs_reference_decision" if _blocking(review["gates"]) else "ready_for_review",
        "ready_for_plan": not _blocking(review["gates"]) and not block["produce_blocked"],
        "approved": False,
        "requires_parallax": False,
        "provider_calls": 0,
        "files": [{"path": name, "role": role} for name, role in files.items()],
        "asset_root": "assets",
        "contact_sheet_scenes": contact,
        "missing_assets": missing,
        "timing": {"basis": review["timing_basis"], "total_s": round(float(timeline[-1]["end_s"]), 3) if timeline else 0.0},
        "gates": review["gates"],
        **block,
        "advisories": _advisories(review),
        "next_steps": _next_steps(review["gates"], folder, brief_path, rerun_command),
    }


def write_package(brief_path: str | Path, folder: str | Path, package_dir: str | Path,
                  timing_path: str | Path | None = None, *, pdf: bool = True,
                  brief_override: Brief | None = None, frame_sources: list[tuple[str, Path]] | None = None,
                  video_metadata: dict | None = None, standalone_scenes: dict | None = None,
                  finished_cut: bool = False, rerun_command: str | None = None) -> dict:
    """Write the storyboard package and return its handoff manifest. Makes no provider calls."""
    brief_path = Path(brief_path).expanduser().resolve()
    root = Path(folder).expanduser().resolve()
    package = Path(package_dir).expanduser().resolve()
    if package.exists() and any(package.iterdir()) and not (package / "handoff.json").is_file():
        raise ValueError(f"{package} exists and is not a storyboard package (no handoff.json); choose an empty --package-dir")
    brief = brief_override or _load_brief(brief_path)
    supplied_timing = _load_timing(timing_path) if timing_path else None
    review = (review_storyboard(brief, root, brief_path, supplied_timing) if not finished_cut else {
        "timeline": supplied_timing or [], "timing_basis": "finished-cut video", "supplied_timing": True,
        "pacing": {"warnings": []}, "gates": [], "variety": [],
        "scene_advisories": {scene.index: [] for scene in brief.script.scenes},
    })
    package.mkdir(parents=True, exist_ok=True)
    # A finished-cut package has no authored brief or planning review. Remove
    # artifacts from an earlier brief-based run in this same package so its
    # visible contents tell the truth as well as its manifest does.
    if finished_cut:
        for name in ("brief.yaml", "script.md", "references.yaml", "qa-notes.md"):
            (package / name).unlink(missing_ok=True)
    assets = package / "assets"
    missing = _copy_assets(brief, root, assets)
    missing = [p for p in missing if p not in {relative for relative, _ in frame_sources or []}]
    for relative, source in frame_sources or []:
        target = assets / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    timeline = review["timeline"]
    contact = _contact_sheet(brief, assets, package / "contact-sheet.jpg")

    if brief_override and not finished_cut:
        (package / "brief.yaml").write_text(_dump(brief.model_dump(exclude_none=True)), encoding="utf-8")
    elif not brief_override:
        (package / "brief.yaml").write_bytes(brief_path.read_bytes())
    if not finished_cut:
        (package / "script.md").write_text(_script_md(brief), encoding="utf-8")
        (package / "references.yaml").write_text(_dump({
        "asset_root": "assets",
        "review_status_note": "review_status is set to approved by editing brief.yaml after the human accepts the asset. The CLI never approves.",
        "provided": [{**a.model_dump(exclude_none=True), "present": a.path not in missing} for a in brief.assets.provided],
        "decisions": [d.model_dump() for d in brief.reference_decisions],
        "open_gates": [g["id"] for g in review["gates"]],
        }), encoding="utf-8")
    scenes_document = standalone_scenes or {
        "asset_root": "assets",
        "timing_basis": review["timing_basis"],
        "scenes": [
            {**scene.model_dump(exclude_none=True), "scene_type": scene.resolved_scene_type,
             "start_s": round(float(t["start_s"]), 3), "end_s": round(float(t["end_s"]), 3)}
            for scene, t in zip(brief.script.scenes, timeline)
        ],
    }
    (package / "scenes.yaml").write_text(_dump(scenes_document), encoding="utf-8")

    # Files on disk carry placeholders instead of this machine's paths; the returned manifest has exact commands.
    portable = {**review, "gates": ([] if finished_cut else open_gates(brief, FOLDER_PLACEHOLDER, BRIEF_PLACEHOLDER))}
    (package / "storyboard.html").write_text(render_storyboard(brief, assets, portable, link_base=package, source_video=video_metadata), encoding="utf-8")
    pdf_created = False
    if pdf:
        pdf_created = render_pdf(package / "storyboard.html", package / "storyboard.pdf")
        if not pdf_created:
            raise ValueError("PDF export needs Playwright Chromium or Chrome/Chromium. Install with `uv pip install playwright && playwright install chromium`, or install Chrome.")
        if pdf_created and package == (root / "storyboard-package").resolve():
            shutil.copy2(package / "storyboard.pdf", root / "storyboard.pdf")

    files = {
        name: role for name, role in _FILES.items()
        if (name != "contact-sheet.jpg" or contact) and (name != "storyboard.pdf" or pdf_created)
        and (not finished_cut or name not in {"brief.yaml", "script.md", "references.yaml", "qa-notes.md"})
    }
    portable_rerun = rerun_command.replace(str(root), FOLDER_PLACEHOLDER) if rerun_command else None
    on_disk = _manifest(brief, portable, files, contact, missing, FOLDER_PLACEHOLDER, BRIEF_PLACEHOLDER, portable_rerun)
    if video_metadata:
        on_disk["source_video"] = video_metadata
    if not finished_cut or on_disk["gates"] or on_disk["advisories"] or missing:
        (package / "qa-notes.md").write_text(_qa_notes(on_disk), encoding="utf-8")
    (package / "handoff.json").write_text(json.dumps(on_disk, indent=2) + "\n", encoding="utf-8")
    result = _manifest(brief, review, files, contact, missing, str(root), str(brief_path), rerun_command)
    if video_metadata:
        result["source_video"] = video_metadata
    return result


def _run(args: list[str]) -> str:
    try:
        return subprocess.run(args, check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout
    except FileNotFoundError as exc:
        raise ValueError(f"{args[0]} is required for --from-video; install ffmpeg.") from exc
    except subprocess.CalledProcessError as exc:
        raise ValueError(exc.stderr.strip() or f"{args[0]} failed") from exc


def _duration(video: Path) -> float:
    value = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(video)]).strip()
    try: return float(value)
    except ValueError as exc: raise ValueError("ffprobe could not read video duration") from exc


def _cuts(video: Path, threshold: float) -> list[float]:
    stderr = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(video), "-filter:v", f"select='gt(scene,{threshold})',showinfo", "-f", "null", "-"], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False).stderr
    if "No such file" in stderr: raise ValueError("ffmpeg is required for --from-video; install ffmpeg.")
    return [float(x) for x in re.findall(r"pts_time:([0-9.]+)", stderr)]


def _load_standalone_scenes(path: str | Path) -> dict:
    raw = yaml.safe_load(Path(path).expanduser().read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("scenes"), list) or not raw["scenes"]:
        raise ValueError("--scenes must be YAML or JSON with top-level title and a non-empty scenes list")
    for item in raw["scenes"]:
        if not isinstance(item, dict) or "id" not in item or "title" not in item:
            raise ValueError("each --scenes entry needs id and title")
    return raw


def write_package_from_video(video: str | Path, scenes_path: str | Path, folder: str | Path, package_dir: str | Path,
                             *, cut_threshold: float = .25, pdf: bool = True) -> dict:
    """Build an offline package from a finished cut and standalone scene metadata."""
    video_path, root, package = Path(video).expanduser().resolve(), Path(folder).expanduser().resolve(), Path(package_dir).expanduser().resolve()
    if not video_path.is_file(): raise ValueError(f"video not found: {video_path}")
    data, duration, cuts = _load_standalone_scenes(scenes_path), _duration(video_path), _cuts(video_path, cut_threshold)
    entries, explicit = data["scenes"], all("start" in s and "end" in s for s in data["scenes"])
    boundaries = [0.0] + cuts + [duration]
    qa: list[dict] = []
    if explicit:
        intervals = []
        for item in entries:
            start, end = float(item["start"]), float(item["end"])
            for name, value in (("start", start), ("end", end)):
                near = min(cuts, key=lambda x: abs(x-value), default=None)
                if near is not None and abs(near-value) <= .25: value = near
                elif value not in (0.0, duration): qa.append({"scene": item["id"], "kind": "boundary_no_nearby_cut", "boundary": name, "requested_s": value})
                if name == "start": start = value
                else: end = value
            if end <= start: raise ValueError(f"scene {item['id']}: end must be after start")
            intervals.append((start, end))
    else:
        if len(cuts) + 1 != len(entries):
            listed = ", ".join(f"{x:.3f}" for x in cuts)
            raise ValueError(f"detected {len(cuts)+1} shots for {len(entries)} scenes; detected cut times: {listed or '(none)'}. Add explicit start/end to --scenes.")
        intervals = list(zip(boundaries[:-1], boundaries[1:]))
    from .brief import Brief
    generated = []
    temp = package.parent / ".parallax-storyboard-frames"
    temp.mkdir(parents=True, exist_ok=True)
    sources: list[tuple[str, Path]] = []
    for number, (item, (start, end)) in enumerate(zip(entries, intervals), 1):
        ident = str(item["id"])
        relative, target = f"frames/scene-{ident}.jpg", temp / f"scene-{ident}.jpg"
        at = float(item.get("frame_at", (start + end) / 2))
        _run(["ffmpeg", "-y", "-ss", f"{at:.4f}", "-i", str(video_path), "-frames:v", "1", "-q:v", "2", str(target)])
        sources.append((relative, target))
        generated.append({"index": number, "shot_type": "broll", "scene_type": "still", "vo_text": item.get("narration", ""), "prompt": item.get("treatment", item["title"]), "motion_prompt": item.get("motion"), "on_screen_text": item.get("on_screen_text"), "still_path": relative, "storyboard_id": ident, "storyboard_title": str(item["title"]), "storyboard_kicker": item.get("kicker"), "storyboard_treatment": item.get("treatment", item["title"])})
    brief = Brief.model_validate({"goal": data.get("title", "Finished video"), "concept_name": data.get("title", "Finished video"), "concept_id": data.get("version"), "reference_decisions": [{"gate": "editorial-reference", "choice": "no_reference", "scenes": list(range(1, len(generated) + 1)), "note": "Frames extracted from finished video"}], "script": {"scenes": generated}})
    timeline = [{"index": i, "start_s": start, "end_s": end} for i, (start, end) in enumerate(intervals, 1)]
    timing_file = temp / "timing.yaml"; timing_file.write_text(_dump({"scenes": timeline}), encoding="utf-8")
    digest = hashlib.sha256(video_path.read_bytes()).hexdigest()
    meta = {"duration_s": round(duration, 3), "sha256": digest, "size": video_path.stat().st_size, "cut_threshold": cut_threshold, "detected_cuts_s": cuts, "boundary_notes": qa}
    normalized = {key: value for key, value in data.items() if key != "scenes"}
    normalized["source_video"] = {"duration_s": round(duration, 3), "cut_threshold": cut_threshold}
    normalized["scenes"] = [
        {**item, "id": str(item["id"]), "start": round(start, 3), "end": round(end, 3),
         "frame": relative}
        for item, (start, end), (relative, _) in zip(entries, intervals, sources)
    ]
    rerun = f"parallax storyboard --folder {root} --from-video {video_path} --scenes {Path(scenes_path).expanduser().resolve()} --cut-threshold {cut_threshold} --pdf"
    try:
        return write_package(video_path, root, package, timing_file, pdf=pdf, brief_override=brief, frame_sources=sources,
                             video_metadata=meta, standalone_scenes=normalized, finished_cut=True, rerun_command=rerun)
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def check_storyboard(brief_path: str | Path, folder: str | Path, timing_path: str | Path | None = None) -> dict:
    """Same manifest as write_package (status, gates, advisories) without writing anything."""
    brief_path = Path(brief_path).expanduser().resolve()
    root = Path(folder).expanduser().resolve()
    brief = _load_brief(brief_path)
    review = review_storyboard(brief, root, brief_path, _load_timing(timing_path) if timing_path else None)
    missing = [raw for raw in _referenced_paths(brief) if not (root / raw).is_file()]
    return {"checked_only": True, **_manifest(brief, review, {}, [], missing, str(root), str(brief_path))}
