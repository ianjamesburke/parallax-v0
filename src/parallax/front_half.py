"""Small, offline project checks and external media import."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

MEDIA = {'.mp4', '.mov', '.webm', '.png', '.jpg', '.jpeg'}
CLEAN = re.compile(r'^Scene_(\d{2,})(?:_take\d{2,})?$', re.IGNORECASE)
INCOMING = re.compile(r'^Scene_(\d{2,})(?:_.*)?$', re.IGNORECASE)


def finding(id, severity, message, fix, blocked=False):
    return {"id": id, "severity": severity, "message": message, "fix": fix, "blocked": blocked}


def structure(c):
    for name, is_dir in (("brief.yaml", False), ("references", True), ("storyboard-package", True)):
        p = c["root"] / name
        if not (p.is_dir() if is_dir else p.is_file()):
            yield finding("missing_structure", "error", f"Missing {name}.", f"Create {p}.")


def schema(c):
    from .brief import Brief
    from .validate import validate_brief
    p = c["root"] / "brief.yaml"
    if not p.is_file(): return
    result = validate_brief(p, c["root"])
    for e in result["errors"]:
        yield finding("brief_invalid", "error", e["message"], f"Edit {p}: {e.get('field', 'brief')}.")
    if not result["errors"]: c["brief"] = Brief.from_yaml(p)


def script_coverage(c):
    brief, p = c.get("brief"), c["root"] / "script.txt"
    if not brief or not p.is_file(): return
    a = " ".join(p.read_text(encoding="utf-8").split())
    b = " ".join(" ".join(s.vo_text for s in brief.script.scenes).split())
    if a != b:
        pos = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
        yield finding("script_mismatch", "error", f"Narration differs at normalized character {pos}: script {a[pos:pos+30]!r}; scenes {b[pos:pos+30]!r}.", "Edit brief.yaml scene vo_text to match script.txt in order.")


def media_names(c):
    root = c["root"]; dest = root / "parallax/assets"; manifest = dest / "import-manifest.json"
    records = json.loads(manifest.read_text(encoding="utf-8")) if manifest.is_file() else []
    groups = {}; c["media"] = groups
    for directory in (root, dest):
        if not directory.is_dir(): continue
        for p in sorted(directory.iterdir()):
            if not p.is_file() or p.suffix.lower() not in MEDIA: continue
            m = CLEAN.fullmatch(p.stem)
            if not m:
                yield finding("invalid_media_name", "error", f"{p.relative_to(root)} has an invalid scene name.", f"Rename it to Scene_NN{p.suffix} or Scene_NN_take02{p.suffix}.")
            elif p.suffix.lower() in {".mp4", ".mov", ".webm"}:
                groups.setdefault(int(m.group(1)), []).append(p)
    for number, paths in groups.items():
        names = [p.name for p in paths]
        selected = [r for r in records if r.get("filename") in names and r.get("preferred") is True]
        brief = c.get("brief")
        if brief:
            selected += [t.filename for s in brief.script.scenes if s.scene_id == f"Scene_{number:02d}" for t in s.takes if t.preferred and t.filename in names]
        selected = {r if isinstance(r, str) else r["filename"] for r in selected}
        if len(paths) > 1 and len(selected) != 1:
            yield finding("ambiguous_take", "error", f"Scene_{number:02d} has unresolved takes: {', '.join(names)}.", "Choose one take and set preferred: true in import-manifest.json or brief.yaml; set the others to false.", True)


def references(c):
    from .reference_gates import EXTERNAL_METHODS, _referenced, open_gates, missing_brand_references, looks_branded
    brief = c.get("brief")
    if not brief: return
    paid = {s.index for s in brief.script.scenes if s.generation_method not in EXTERNAL_METHODS}
    for gate in open_gates(brief, c["root"], c["root"] / "brief.yaml"):
        if not paid.intersection(gate["scenes"]):
            continue
        kind = "product_ref" if gate["id"] == "product-reference" else "style_ref"
        yield finding("reference_gate", "error", gate["question"], f"Add an image under references/ and list it as {kind} in assets.provided and the affected scenes' image_refs.", True)
    for scene, detail in missing_brand_references(brief, c["root"]):
        display = scene.scene_id.replace("_", " ")
        fix = f"Add the file under references/official/, set status: supplied and list it in brand_ref.paths for {scene.scene_id} in brief.yaml."
        message = (f"{display} needs a brand reference ({detail}) but none is attached." if scene.brand_ref.status == "missing"
                   else f"{display} has a missing brand reference file: {detail}.")
        yield finding("brand_ref_missing" if scene.brand_ref.status == "missing" else "brand_ref_file", "warning" if scene.brand_ref.status == "missing" else "error", message, fix)
    for scene in brief.script.scenes:
        if scene.brand_ref.status == "not_needed" and looks_branded(brief, scene):
            yield finding("brand_ref_advisory", "warning", f"{scene.scene_id.replace('_', ' ')} may need a brand reference.", f"Set brand_ref deliberately for {scene.scene_id}: supplied with paths, missing with a note, or not_needed.")
    if any(s.generation_method in EXTERNAL_METHODS and not _referenced(brief, s) for s in brief.script.scenes):
        yield finding("external_style_reference", "warning", "No style reference: the external generator will choose the look; add images under references/style/ to anchor it.", f"Add images under {c['root'] / 'references/style'} and list them in assets.provided and the affected scenes’ image_refs in {c['root'] / 'brief.yaml'}.")


def timing(c):
    import subprocess
    from .pacing import estimate_timeline
    brief = c.get("brief")
    if not brief: return
    timeline, _ = estimate_timeline([s.model_dump() for s in brief.script.scenes], brief.timing.estimate_wps, brief.timing.speed)
    c["timing"] = {"basis": "estimated", "estimate_wps": brief.timing.estimate_wps, "speed": brief.timing.speed, "handle_s": brief.timing.handle_s}
    yield finding("timing_basis", "info", f"Timing is estimated at {brief.timing.estimate_wps:g} words/s × {brief.timing.speed:g}; footage needs a {brief.timing.handle_s:g}s handle.", "Set timing.estimate_wps and timing.speed in brief.yaml to match measured audio when available.")
    if c["stage"] != "media": return
    probe = shutil.which("ffprobe")
    if not probe:
        yield finding("ffprobe_unavailable", "warning", "Clip durations could not be checked: ffprobe is unavailable.", "Install ffprobe and rerun parallax check --folder PROJECT --stage media.")
    for scene, interval in zip(brief.script.scenes, timeline):
        m = re.fullmatch(r"Scene_(\d+)", scene.scene_id)
        paths = c["media"].get(int(m.group(1)), []) if m else []
        if not paths:
            yield finding("missing_clip", "error", f"{scene.scene_id} has no clip.", "Import a clip with parallax assets import --folder PROJECT --from MEDIA_DIR.")
        elif probe:
            minimum = interval["end_s"] - interval["start_s"] + brief.timing.handle_s
            for path in paths:
                proc = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)], capture_output=True, text=True)
                try: duration = float(proc.stdout.strip()) if proc.returncode == 0 else 0
                except ValueError: duration = 0
                if duration <= 0:
                    yield finding("clip_unreadable", "error", f"Cannot read duration of {path.name}.", f"Replace {path} with a decodable clip.")
                elif duration + .001 < minimum:
                    yield finding("clip_short", "error", f"{path.name} is {duration:.2f}s; {scene.scene_id} needs {minimum:.2f}s.", f"Replace {path} with footage at least {minimum:.2f}s long.")


CHECKS = (structure, schema, script_coverage, media_names, references, timing)


def check_folder(folder, stage="draft"):
    c = {"root": Path(folder).expanduser().resolve(), "stage": stage}
    findings = []
    for check in CHECKS: findings.extend(check(c))
    errors = [f for f in findings if f["severity"] == "error"]
    code = 2 if any(not e["blocked"] for e in errors) else 4 if errors else 0
    return {"check_version": 1, "stage": stage, "status": "invalid" if code == 2 else "blocked" if code == 4 else "ok", "valid": not errors, "provider_calls": 0, "findings": findings, "timing": c.get("timing"), "exit_code": code}

def import_media(folder: str, source: str) -> dict:
    root, src = Path(folder).expanduser().resolve(), Path(source).expanduser().resolve()
    if not src.is_dir():
        raise ValueError(f'Not a directory: {src}')
    dest = root / 'parallax' / 'assets'
    if not root.is_dir() or src == dest:
        raise ValueError("Project must exist and source must differ from the asset directory")
    incoming = [p for p in sorted(src.iterdir()) if p.is_file() and p.suffix.lower() in MEDIA]
    for path in incoming:
        if not INCOMING.fullmatch(path.stem):
            raise ValueError(f"Cannot identify scene in {path.name}")
    dest.mkdir(parents=True, exist_ok=True)
    manifest_path = dest / 'import-manifest.json'
    records = json.loads(manifest_path.read_text()) if manifest_path.exists() else []
    for path in incoming:
        match = INCOMING.fullmatch(path.stem)
        if not match:
            raise ValueError(f'Cannot identify scene in {path.name}')
        scene = f'Scene_{int(match.group(1)):02d}'
        n = 1
        while any(dest.glob(scene + (f'_take{n:02d}' if n > 1 else '') + '.*')):
            n += 1
        name = scene + (f'_take{n:02d}' if n > 1 else '') + path.suffix.lower()
        shutil.copy2(path, dest / name)
        records.append({'scene_id': scene, 'filename': name, 'source_filename': path.name, 'preferred': False})
    manifest_path.write_text(json.dumps(records, indent=2) + '\n')
    return {'imported': len(incoming), 'manifest': str(manifest_path), 'takes': records}
