"""Offline HTML review of an authored brief; no planning or generation calls."""
from __future__ import annotations

import base64
from html import escape
from io import BytesIO
from pathlib import Path
import math
import os
from urllib.parse import quote

import re

from PIL import Image, ImageOps, UnidentifiedImageError

from .brief import Brief, ProvidedAsset
from .reference_gates import EDITORIAL, open_gates
from .pacing import estimate_timeline, review_opening_pacing


_STYLE = """
:root{color-scheme:dark;--ink:#edf2f0;--ink-soft:#c5d1cd;--muted:#889994;--paper:#0d1312;--card:#141d1b;--card-alt:#192522;--line:#263531;--accent:#8fd3c7;--warn:#f0b39e}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;-webkit-font-smoothing:antialiased}
main{max-width:1040px;margin:auto;padding:36px 24px 72px}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.page-head{border-bottom:1px solid var(--line);padding-bottom:22px;margin-bottom:22px}
.eyebrow{color:var(--muted);font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.14em}
h1{font:600 clamp(26px,3.6vw,40px)/1.15 system-ui,sans-serif;letter-spacing:-.02em;margin:6px 0 10px}
h2{font-size:16px;font-weight:600;margin:0}
h3{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin:0 0 8px}
p{margin:6px 0;white-space:pre-wrap}
.goal{color:var(--ink-soft);font-size:15px;max-width:780px;margin:4px 0 14px}
.meta{color:var(--muted);font-size:12px}
.facts{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
.facts span{background:var(--card-alt);border:1px solid var(--line);padding:4px 10px;border-radius:999px;font-size:12px;color:var(--ink-soft)}
.gate{padding:14px 18px;background:var(--card);border:1px solid var(--line);border-radius:10px;margin:14px 0}
.gate summary{cursor:pointer;font-weight:600;font-size:13px;color:var(--ink-soft)}
.gate ul{margin:8px 0 0;padding-left:18px;font-size:13px;color:var(--ink-soft)}
.headline-bar{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;padding:12px 16px;background:var(--card-alt);border:1px solid var(--line);border-radius:10px;margin:14px 0}
.headline-bar h2{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin:0}
.headline-bar blockquote{font-size:16px;font-weight:600;margin:0;border:0;padding:0}
.refs-panel{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 18px;margin:16px 0 24px}
.ref-groups{display:flex;gap:24px;flex-wrap:wrap}
.ref-group{min-width:180px}
.ref-list{display:flex;gap:10px;flex-wrap:wrap;align-items:flex-start}
.ref-card{width:112px;background:var(--paper);border:1px solid var(--line);border-radius:8px;padding:6px;font-size:11px}
.ref-card .asset{margin:0;max-width:none;min-width:0}
.ref-card .asset img{width:100%;height:76px;object-fit:contain;background:#090d0c;border-radius:5px;display:block}
.ref-card .asset figcaption{font-size:10px;line-height:1.25;margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ref-card .meta{font-size:10px;line-height:1.25;margin:2px 0 0}
.scene-list{display:flex;flex-direction:column;gap:14px}
.scene{display:grid;grid-template-columns:200px 1fr;gap:24px;align-items:start;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px;break-inside:avoid;scroll-margin-top:16px}
.scene-media{display:flex;flex-direction:column;gap:8px;min-width:0}
.scene-media:has(> :nth-child(2)){flex-direction:row}
.scene-media:has(> :nth-child(2)) .asset{flex:1}
.scene-media .asset{margin:0;width:100%;max-width:none;min-width:0}
.scene-media .asset img,.scene-media .asset video{width:100%;max-height:320px;object-fit:contain;background:#090d0c;border-radius:8px;border:1px solid var(--line);display:block}
.scene-media .asset figcaption{font-size:11px;color:var(--muted);margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.scene-body{min-width:0;display:flex;flex-direction:column;gap:10px}
.scene-head{display:flex;justify-content:space-between;gap:12px;align-items:center;border-bottom:1px solid var(--line);padding-bottom:8px}
.scene-title{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
.badge{border-radius:999px;padding:3px 10px;background:#23363d;font-size:11px;font-weight:600;letter-spacing:.02em;white-space:nowrap;color:#d6ece8}
.motion_graphic .badge{background:#433520;color:#f5dfc0}
.animation .badge{background:#332b42;color:#e3d8f8}
blockquote.vo{font:500 18px/1.42 Georgia,serif;margin:2px 0;padding-left:12px;border-left:2px solid var(--accent);color:var(--ink);white-space:pre-wrap}
.direction{color:var(--ink-soft);font-size:14px;line-height:1.55;margin:0}
.tag-list{display:flex;flex-direction:column;gap:4px;margin-top:2px}
.tag-row{display:flex;gap:8px;align-items:baseline;font-size:13px;color:var(--ink-soft);background:var(--card-alt);padding:5px 10px;border-radius:6px}
.tag-label{font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);min-width:68px;flex-shrink:0}
.scene-refs{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:4px;padding-top:6px;border-top:1px dashed var(--line)}
.scene-refs .asset{margin:0;display:inline-flex;align-items:center;gap:6px;background:var(--paper);border:1px solid var(--line);border-radius:6px;padding:3px 7px 3px 3px;max-width:200px;min-width:0}
.scene-refs .asset img{width:28px;height:28px;object-fit:cover;border-radius:4px;flex-shrink:0}
.scene-refs .asset figcaption{font-size:11px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.missing{color:var(--warn);border:1px dashed #9e6b59;padding:8px 10px;border-radius:6px;font-size:12px}
.empty{padding:24px 12px;background:#0f1715;border:1px dashed var(--line);border-radius:8px;color:var(--muted);font-size:12px;text-align:center}
footer{color:var(--muted);font-size:12px;margin-top:32px;padding-top:16px;border-top:1px solid var(--line)}
@media(max-width:680px){main{padding:20px 14px}.scene{grid-template-columns:1fr}.scene-media{max-width:220px}}
@media print{:root{color-scheme:light;--ink:#182c30;--ink-soft:#2c4246;--muted:#52696d;--paper:white;--card:white;--card-alt:#f3f6f5;--line:#d2d9d3}main{padding:0}.scene,.gate,.refs-panel,.empty{background:white;border-radius:0}.badge,.motion_graphic .badge,.animation .badge{background:#e3ecf1;color:#182c30}.gate{break-inside:avoid}a{color:#225e65}}
"""


def _asset(folder: Path, raw_path: str, label: str, *, video: bool = False, link_base: Path | None = None, thumb_px: int = 640) -> str:
    path = Path(raw_path).expanduser()
    path = (path if path.is_absolute() else folder / path).resolve()
    short_name = Path(raw_path).name
    caption_full = f"{escape(label)} · {escape(raw_path)}"
    caption_short = f"{escape(label)} · {escape(short_name)}"
    if not path.is_file():
        return f'<figure class="asset missing"><strong>Missing asset</strong><figcaption title="{caption_full}">{caption_full}</figcaption></figure>'
    uri = escape(quote(Path(os.path.relpath(path, link_base)).as_posix()) if link_base else path.as_uri(), quote=True)
    if video:
        media = f'<video controls preload="metadata" src="{uri}"></video>'
    else:
        try:
            with Image.open(path) as source:
                thumb = ImageOps.exif_transpose(source).convert("RGB")
                thumb.thumbnail((thumb_px, thumb_px))
                buf = BytesIO()
                thumb.save(buf, format="JPEG", quality=85)
            data = base64.b64encode(buf.getvalue()).decode("ascii")
            media = f'<img src="data:image/jpeg;base64,{data}" alt="{escape(label, quote=True)}">'
        except (OSError, ValueError, UnidentifiedImageError):
            media = '<p class="missing">Preview unavailable; open the supplied file to inspect it.</p>'
    return f'<figure class="asset">{media}<figcaption title="{caption_full}"><a href="{uri}">{caption_short}</a></figcaption></figure>'


def _reference_groups(brief: Brief, folder: Path, link_base: Path | None) -> str:
    """Group reference provenance into a compact thumbnail bar."""
    groups: dict[str, list[ProvidedAsset]] = {
        "Source style and supplied references": [],
        "Generated character and style references": [],
        "Official brand references": [],
    }
    titles = dict(zip(("supplied", "generated", "official"), groups))
    for asset in brief.assets.provided:
        groups[titles[asset.origin]].append(asset)
    sections = []
    for title, assets in groups.items():
        if not assets:
            continue
        cards = []
        for asset in assets:
            label = asset.description or asset.kind.replace("_", " ")
            preview = _asset(folder, asset.path, label, link_base=link_base, thumb_px=240)
            provenance = f'<p class="meta">{asset.origin.title()} · Review: {asset.review_status}</p>'
            source = (
                f'<p class="meta"><a href="{escape(asset.source_url, quote=True)}" rel="noopener noreferrer">Source</a></p>'
                if asset.source_url else ''
            )
            cards.append(f'<div class="ref-card">{preview}{provenance}{source}</div>')
        sections.append(f'<div class="ref-group"><h3>{title}</h3><div class="ref-list">{"".join(cards)}</div></div>')
    if not sections:
        return ''
    return f'<section class="refs-panel"><div class="ref-groups">{"".join(sections)}</div></section>'


def _variety_warnings(brief: Brief) -> list[dict]:
    """Visual-variety advisories: repetitive motion-graphic cards."""
    scenes = brief.script.scenes
    warnings: list[dict] = []
    graphic_count = sum(scene.resolved_scene_type == "motion_graphic" for scene in scenes)
    if scenes and graphic_count / len(scenes) > 0.4:
        warnings.append({"kind": "motion_graphic_share", "message": (
            f"{graphic_count} of {len(scenes)} scenes are motion graphics ({graphic_count / len(scenes):.0%}). Review the beat map for editorial/action alternatives before generating a repetitive card sequence.")})
    run: list[int] = []
    longest_run: list[int] = []
    for scene in scenes:
        if scene.resolved_scene_type == "motion_graphic":
            run.append(scene.index)
            if len(run) > len(longest_run):
                longest_run = run[:]
        else:
            run = []
    if len(longest_run) >= 3:
        warnings.append({"kind": "motion_graphic_run", "message": (
            "Consecutive motion-graphic run: " + ", ".join(f"{index:02d}" for index in longest_run) +
            ". Break it with an editorial, product, or action beat unless the sequence is deliberately one continuous explainer.")})
    return warnings


SAFE_LINE_CHARS = 18  # longest on_screen_text line judged safe at 9:16 title size; a heuristic, not a measured layout


def _scene_layout_and_composition_advisories(scene) -> list[dict]:
    """Catch likely overflow and multi-frame still directions before image work.

    Conservative: it identifies directions a reviewer must resolve by choosing one
    frame, splitting the scene, or selecting an explicit local multi-panel
    treatment (scene_type: motion_graphic). It never splits narration itself.
    """
    advisories: list[dict] = []
    copy_lines = scene.on_screen_text.splitlines() if scene.on_screen_text else []
    longest = max((len(line.strip()) for line in copy_lines), default=0)
    if longest > SAFE_LINE_CHARS:
        advisories.append({
            "kind": "unsafe_vertical_text",
            "message": f"On-screen copy has a line of {longest} characters; the 9:16 safe-layout heuristic is {SAFE_LINE_CHARS} per line. Wrap, shorten, or test it in the actual graphic template before approving.",
            "threshold": {"max_chars_per_line": SAFE_LINE_CHARS, "longest_line_chars": longest},
            "remedies": [
                {"id": "wrap", "how": "Put a newline in on_screen_text (YAML `|` block) so every line is at most the threshold. Each line is measured separately."},
                {"id": "shorten", "how": "Shorten the copy. on_screen_text is review copy, not narration, so editing it does not change the script."},
            ],
        })
    if scene.resolved_scene_type == "motion_graphic":
        return advisories
    direction = f"{scene.prompt} {scene.motion_prompt or ''}"
    compound = re.search(r"\b(?:diptych|split[ -]?screen|before[ /-]after|montage|collage)\b", direction, re.IGNORECASE)
    temporal_pair = re.search(r"\b(?:same .{0,80}\bthen\b|turns? .{0,80}\band\b .{0,80}\bwakes?)", direction, re.IGNORECASE)
    if compound or temporal_pair:
        advisories.append({
            "kind": "compound_direction",
            "message": "Compound visual direction. Choose one representative generated frame, split it into separate scenes, or specify a deliberate code-authored multi-panel graphic (scene_type: motion_graphic); do not ask a single image generation to depict multiple scenes or moments.",
            "remedies": [
                {"id": "representative_frame", "how": "Rewrite prompt as one composition, e.g. the single most telling moment. Narration is unchanged."},
                {"id": "split_scenes", "how": "Replace the scene with several scenes, each with one composition. Distribute the original vo_text across them in order at word boundaries; "
                                               "the concatenated vo_text must equal the original narration exactly. A beat with no narration cannot be its own brief scene: vo_text is a required string, an empty one is accepted by the schema but alignment gives it about 0.05s, and BriefScene has no duration field (plan.yaml can pin duration_s after planning, by hand).",
                 "narration": "distribute_verbatim"},
                {"id": "motion_graphic", "how": "Set scene_type: motion_graphic and supply a locally rendered clip_path before production. No AI generation is used; the panel layout itself is not defined by Parallax."},
            ],
        })
    return advisories


def _missing_clip_advisory(scene) -> dict:
    where = f"clip_path {scene.clip_path!r} does not exist" if scene.clip_path else "no clip_path is set"
    return {
        "kind": "motion_graphic_clip_missing",
        "message": f"Motion graphic: {where}. Parallax does not author or generate this clip; production refuses to run until it exists.",
        "remedies": [{
            "id": "supply_clip",
            "how": "Parallax does not author this clip. A human or another tool must render the clip locally and set clip_path in the brief. "
                   "Use the Remotion skill (`remotion-best-practices` / `create-remotion-geist`) if available in the agent environment, adapting the visual style to the project's supplied references rather than a default template theme. "
                   "Parallax enforces no clip contract: it only checks the file exists. "
                   "Convention (`parallax guide motion_graphic`, not validated): silent MP4 at the project output dimensions and frame rate, with the measured scene duration.",
        }],
    }


def _resolve_timeline(brief: Brief, timeline: list[dict] | None) -> tuple[list[dict], str]:
    if timeline is None:
        return estimate_timeline([scene.model_dump() for scene in brief.script.scenes])
    if [s.get("index") for s in timeline] != [s.index for s in brief.script.scenes]:
        raise ValueError("storyboard timing scene indices/order must match the brief")
    previous_end = 0.0
    for scene in timeline:
        start, end = float(scene["start_s"]), float(scene["end_s"])
        if not math.isfinite(start) or not math.isfinite(end) or end <= start or abs(start - previous_end) > 0.001:
            raise ValueError("storyboard timing must contain finite, positive, contiguous scene intervals starting at zero")
        previous_end = end
    return timeline, "supplied aligned timeline"


def _pacing_with_lock(pacing: dict, narration_locked: bool) -> dict:
    """Mark each warning with whether the brief declares the narration final. The warning still stands."""
    return {**pacing, "warnings": [{**w, "locked_by_narration": narration_locked} for w in pacing["warnings"]]}


def review_storyboard(brief: Brief, folder: str | Path, brief_path: str | Path, timeline: list[dict] | None = None) -> dict:
    """Structured pre-generation review: timing, pacing, reference gates, advisories.

    The HTML, QA notes, and handoff manifest all render from this one result.
    """
    supplied_timing = timeline is not None
    timeline, timing_basis = _resolve_timeline(brief, timeline)
    first_word_start = None
    if supplied_timing and timeline:
        opening_words = timeline[0].get("words")
        if isinstance(opening_words, list) and opening_words and isinstance(opening_words[0], dict):
            value = opening_words[0].get("start")
            if isinstance(value, (int, float)) and math.isfinite(value) and value >= 0:
                first_word_start = float(value)
    gates = open_gates(brief, folder, brief_path)
    scene_advisories: dict[int, list[dict]] = {scene.index: _scene_layout_and_composition_advisories(scene) for scene in brief.script.scenes}
    for scene in brief.script.scenes:
        if scene.resolved_scene_type == "motion_graphic" and not (scene.clip_path and (Path(folder) / scene.clip_path).is_file()):
            scene_advisories[scene.index].append(_missing_clip_advisory(scene))
    for gate in gates:
        message = ("No scene image reference — resolve the reference gate before generation."
                   if gate["id"] == EDITORIAL else "Brand/product reference missing.")
        for index in gate["scenes"]:
            if str(index) in gate.get("authorized_scenes", {}):
                scene_advisories[index].insert(0, {"kind": "generated_anchor_pending", "message": "Anchor authorized but not yet generated; attach it before `parallax produce`."})
            else:
                scene_advisories[index].insert(0, {"kind": gate["kind"], "message": message})
    return {
        "timeline": timeline,
        "timing_basis": timing_basis,
        "supplied_timing": supplied_timing,
        "pacing": _pacing_with_lock(
            review_opening_pacing(timeline, timing_basis, first_word_start_s=first_word_start), brief.narration_locked),
        "gates": gates,
        "variety": _variety_warnings(brief),
        "scene_advisories": scene_advisories,
    }


def _gates_html(review: dict) -> str:
    """Render open gates and variety warnings as one review section."""
    if not review["gates"] and not review["variety"]:
        return ''
    gates = ''.join(
        f'<li><strong>{escape(g["question"])}</strong><ul>'
        + ''.join(f'<li>{escape(c["id"])}: {escape(c["summary"])} <span class="meta">{escape(c["human_decision"])}</span><br><code>{escape(c["command"])}</code></li>' for c in g["choices"])
        + '</ul></li>' for g in review["gates"])
    variety = ''.join(f'<li>{escape(w["message"])}</li>' for w in review["variety"])
    return (
        '<section class="gate"><h2>Reference and visual-variety review</h2>'
        f'<ul>{gates}{variety}</ul></section>'
    )


def render_storyboard(brief: Brief, folder: str | Path, review: dict, *, link_base: str | Path | None = None) -> str:
    """Return self-contained layout and image previews.

    Local file links are absolute unless `link_base` (the HTML's directory) is
    given, which makes them relative so a package stays portable.
    """
    root = Path(folder).expanduser().resolve()
    base = Path(link_base).expanduser().resolve() if link_base else None
    concept_name = brief.concept_name or root.name or "Storyboard"
    concept_title = f"{concept_name} · {brief.concept_id}" if brief.concept_id else concept_name
    timeline, timing_basis, supplied_timing = review["timeline"], review["timing_basis"], review["supplied_timing"]
    pacing = review["pacing"]
    reference_variety_html = _gates_html(review)
    warning_items = ''.join(f'<li>{escape(w["message"])}</li>' for w in pacing["warnings"])
    review_items = ''.join(f'<li>{escape(item)}</li>' for item in pacing["visual_review_prompts"])
    open_attr = ' open' if pacing["warnings"] else ''
    pacing_html = (
        f'<details class="gate"{open_attr}><summary>Opening pacing review · {escape(timing_basis)} · {pacing["cuts_before_6s"]} cuts before 6s</summary>'
        + ('<ul>' + warning_items + '</ul>' if warning_items else '<p class="meta">No timing advisories for this timeline.</p>')
        + '<p class="meta">Visual review — requires looking at the shots:</p><ul>' + review_items + '</ul></details>'
    )
    cards: list[str] = []
    total = 0.0
    for position, scene in enumerate(brief.script.scenes, start=1):
        kind = scene.resolved_scene_type
        title = kind.replace("_", " ").title()
        words = len(re.sub(r"\[[^\]]*\]", "", scene.vo_text).split())
        timing = timeline[position - 1]
        duration = float(timing["end_s"]) - float(timing["start_s"])
        total += duration
        timing_label = f'{duration:.2f}s supplied timing' if supplied_timing else f'~{duration:.1f}s reading estimate'
        scene_advisory = '<p class="meta">Opening pacing: review this hold longer than 3s.</p>' if any(w.get("scene_index") == scene.index for w in pacing["warnings"]) else ''
        combined_advisory = " ".join(a["message"] for a in review["scene_advisories"][scene.index])
        if combined_advisory:
            scene_advisory += '<p class="missing">' + escape(combined_advisory) + '</p>'
        media = []
        if scene.still_path:
            media.append(_asset(root, scene.still_path, "Supplied still", link_base=base))
        if scene.clip_path:
            media.append(_asset(root, scene.clip_path, "Supplied clip", video=True, link_base=base))
        assets = ''.join(media) or '<p class="empty">No scene asset supplied (not a generated still).</p>'
        refs = ''.join(_asset(root, path, "Scene reference", link_base=base, thumb_px=96) for path in scene.image_refs)
        tags = []
        if scene.on_screen_text:
            tags.append(f'<div class="tag-row"><span class="tag-label">On-screen</span><span>{escape(scene.on_screen_text)}</span></div>')
        if scene.motion_prompt:
            tags.append(f'<div class="tag-row"><span class="tag-label">Motion</span><span>{escape(scene.motion_prompt)}</span></div>')
        tags_html = f'<div class="tag-list">{"".join(tags)}</div>' if tags else ''
        refs_html = f'<div class="scene-refs"><span class="tag-label">Refs</span>{refs}</div>' if refs else ''
        cards.append(f'''<article class="scene {kind}" id="scene-{position}">
<div class="scene-media">{assets}</div>
<div class="scene-body">
<div class="scene-head"><div class="scene-title"><h2>Scene {scene.index:02d}</h2><span class="meta">{escape(scene.shot_type)} · {words} words · {timing_label}</span></div><span class="badge">{title}</span></div>
<blockquote class="vo">{escape(scene.vo_text)}</blockquote>
<p class="direction">{escape(scene.prompt)}</p>{tags_html}{scene_advisory}{refs_html}
</div></article>''')
    provided = _reference_groups(brief, root, base)
    headline = (
        '<section class="headline-bar"><h2>Opening headline</h2>'
        f'<blockquote>{escape(brief.headline)}</blockquote>'
        '<span class="meta">Not spoken; excluded from the reading estimate.</span></section>'
        if brief.headline else ''
    )
    criteria = ''.join(f'<li>{escape(item)}</li>' for item in brief.success_criteria)
    counts = {kind: sum(scene.resolved_scene_type == kind for scene in brief.script.scenes)
              for kind in ("still", "animation", "motion_graphic")}
    type_summary = " · ".join(f"{count} {kind.replace('_', ' ')}{'s' if count != 1 else ''}"
                              for kind, count in counts.items() if count)
    timing_pill = (
        f'Supplied timeline: {total:.2f}s'
        if supplied_timing else f'Estimated reading time: {total:.1f}s (not production timing)'
    )
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(concept_title)} · Storyboard</title><style>{_STYLE}</style></head><body><main>
<header class="page-head">
<div class="eyebrow">Parallax · Storyboard</div><h1>{escape(concept_name)}</h1>
{'<p class="meta">Concept ID: ' + escape(brief.concept_id) + '</p>' if brief.concept_id else ''}
<p class="goal">{escape(brief.goal)}</p>
<div class="facts"><span>{len(brief.script.scenes)} scenes</span><span>{type_summary}</span><span>{escape(brief.aspect)}</span><span>voice: {escape(brief.voice)}</span><span>{timing_pill}</span></div>
</header>
{headline}
{provided}
{reference_variety_html}
{pacing_html}
{'<details class="gate"><summary>Success criteria</summary><ul>' + criteria + '</ul></details>' if criteria else ''}
<section class="scene-list">{''.join(cards)}</section>
<footer>Offline review — no provider calls. Image previews are embedded; original files and video previews link to local assets.</footer>
</main></body></html>'''
