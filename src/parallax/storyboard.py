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
:root{color-scheme:dark;--ink:#e5ece9;--muted:#a9bab5;--paper:#101817;--line:#354540}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.6 system-ui,sans-serif}
main{max-width:1160px;margin:auto;padding:50px 28px}h1{font:500 clamp(32px,5vw,56px)/1.12 Georgia,serif;max-width:950px;margin:12px 0 24px}
h2{font-size:24px;margin:0 0 12px}h3{font-size:13px;text-transform:uppercase;letter-spacing:.09em;margin:20px 0 7px}
p{margin:8px 0;white-space:pre-wrap}.eyebrow,.meta{color:var(--muted);font-size:13px}.eyebrow{text-transform:uppercase;letter-spacing:.17em}
.summary,.gate{padding:20px 24px;background:#223831;border-radius:12px;margin:22px 0}.gate{background:#192420;border:1px solid var(--line)}
a{color:#a1d7cd}
.scene{background:#192420;border:1px solid var(--line);border-radius:16px;margin:26px 0;padding:28px;break-inside:avoid;scroll-margin-top:20px}
.scene-head{display:flex;justify-content:space-between;gap:16px;align-items:center}.badge{border-radius:24px;padding:6px 14px;background:#2c424b;font-size:13px;white-space:nowrap}
.motion_graphic .badge{background:#514127}.animation .badge{background:#403650}.columns{display:grid;grid-template-columns:1fr 1fr;gap:30px}
blockquote{font:24px/1.45 Georgia,serif;margin:18px 0;padding-left:18px;border-left:3px solid #85a19b;white-space:pre-wrap}
.gallery{display:flex;gap:16px;flex-wrap:wrap}.asset{margin:12px 0;max-width:290px;min-width:140px;overflow-wrap:anywhere}.asset img,.asset video{width:100%;max-height:330px;object-fit:contain;background:#101817;border-radius:8px}.asset figcaption{font-size:12px;color:var(--muted)}
.missing{color:#efb5a1;border:1px dashed #bd8d7c;padding:14px;border-radius:8px}.empty{padding:28px;background:#121d19;border-radius:8px;color:var(--muted)}
.opening-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:16px}.opening-shot{min-width:0}.opening-shot .asset{width:100%;min-width:0;margin:8px 0}.opening-shot .asset img,.opening-shot .asset video{height:180px}.opening-shot .empty{height:180px;padding:18px;font-size:13px;display:flex;align-items:center}.opening-shot p{font-size:13px}
.scene .asset img,.scene .asset video{height:280px}.scene .empty{min-height:280px;width:100%}
footer{color:var(--muted);font-size:13px;margin-top:40px}@media(max-width:700px){main{padding:24px 16px}.columns{grid-template-columns:1fr}.scene{padding:20px}.scene-head{align-items:flex-start}}
@media print{:root{color-scheme:light;--ink:#182c30;--muted:#52696d;--paper:white;--line:#d2d9d3}main{padding:0}.scene,.gate,.summary,.empty{background:white;border-radius:0}.badge,.motion_graphic .badge,.animation .badge{background:#e3ecf1}.gate{break-inside:avoid}a{color:#225e65}}
"""


def _asset(folder: Path, raw_path: str, label: str, *, video: bool = False, link_base: Path | None = None) -> str:
    path = Path(raw_path).expanduser()
    path = (path if path.is_absolute() else folder / path).resolve()
    caption = f"{escape(label)} · {escape(raw_path)}"
    if not path.is_file():
        return f'<figure class="asset missing"><strong>Missing asset</strong><figcaption>{caption}</figcaption></figure>'
    uri = escape(quote(Path(os.path.relpath(path, link_base)).as_posix()) if link_base else path.as_uri(), quote=True)
    if video:
        media = f'<video controls preload="metadata" src="{uri}"></video>'
    else:
        try:
            with Image.open(path) as source:
                thumb = ImageOps.exif_transpose(source).convert("RGB")
                thumb.thumbnail((640, 640))
                buf = BytesIO()
                thumb.save(buf, format="JPEG", quality=85)
            data = base64.b64encode(buf.getvalue()).decode("ascii")
            media = f'<img src="data:image/jpeg;base64,{data}" alt="{escape(label, quote=True)}">'
        except (OSError, ValueError, UnidentifiedImageError):
            media = '<p class="missing">Preview unavailable; open the supplied file to inspect it.</p>'
    return f'<figure class="asset">{media}<figcaption><a href="{uri}">{caption}</a></figcaption></figure>'


def _reference_groups(brief: Brief, folder: Path, link_base: Path | None) -> str:
    """Group reference provenance without treating pending assets as approved."""
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
            preview = _asset(folder, asset.path, label, link_base=link_base)
            provenance = f'<p class="meta">{asset.origin.title()} · Review: {asset.review_status}</p>'
            source = (
                f'<p class="meta"><a href="{escape(asset.source_url, quote=True)}" rel="noopener noreferrer">Source</a></p>'
                if asset.source_url else ''
            )
            cards.append(f'<div class="asset">{preview}{provenance}{source}</div>')
        sections.append(f'<section><h2>{title}</h2><div class="gallery">{"".join(cards)}</div></section>')
    if not sections:
        return ''
    return ''.join(sections) + '<p class="meta">References guide the look; they are not scene assets. Review labels record the authored status. Assign scene image_refs explicitly for image generation.</p>'


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
                                               "the concatenated vo_text must equal the original narration exactly. Sharing one narration span between scenes (empty vo_text) is not supported in a brief: BriefScene has no duration field and alignment assigns scene boundaries from vo_text words.",
                 "narration": "distribute_verbatim"},
                {"id": "motion_graphic", "how": "Set scene_type: motion_graphic and supply a locally rendered clip_path before production. No AI generation is used; the panel layout itself is not defined by Parallax."},
            ],
        })
    return advisories


_PACING_REMEDIES = {
    "opening_long_hold": "Split the scene at a word boundary into shots of at most 3s, distributing vo_text verbatim; or keep it and show a clear change of action inside the shot.",
    "opening_few_cuts": "Add scene boundaries inside the first 6s by distributing vo_text across more scenes at word boundaries; or accept it when the opening is one deliberate continuous action.",
    "opening_delayed_first_word": "After narration exists, remove verified leading quiet with `parallax audio trim-leading-quiet --first-word-by 0.1`.",
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


def _pacing_with_remedies(pacing: dict, narration_locked: bool) -> dict:
    """Attach a remedy to each warning; with locked narration they are acknowledged, not action items."""
    warnings = [{**w, "remedy": _PACING_REMEDIES[w["code"]], "acknowledged": narration_locked} for w in pacing["warnings"]]
    return {**pacing, "warnings": warnings}


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
    for gate in gates:
        message = ("No scene image reference — resolve the reference gate before generation."
                   if gate["id"] == EDITORIAL else "Brand/product reference missing.")
        for index in gate["scenes"]:
            scene_advisories[index].insert(0, {"kind": gate["kind"], "message": message})
    return {
        "timeline": timeline,
        "timing_basis": timing_basis,
        "supplied_timing": supplied_timing,
        "pacing": _pacing_with_remedies(
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
        '<p class="meta">Pre-generation gates. A human answers each open gate before paid still work.</p>'
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
    pacing_html = (
        '<section class="gate"><h2>Opening pacing review</h2>'
        f'<p class="meta">Social-video heuristic · {escape(timing_basis)} · {pacing["cuts_before_6s"]} scene cuts before 6s</p>'
        + ('<ul>' + warning_items + '</ul>' if warning_items else '<p>No timing advisories for this timeline.</p>')
        + '<h3>Visual review — requires looking at the shots</h3><ul>' + review_items + '</ul></section>'
    )
    opening_shots: list[str] = []
    cards: list[str] = []
    total = 0.0
    for position, scene in enumerate(brief.script.scenes, start=1):
        kind = scene.resolved_scene_type
        title = kind.replace("_", " ").title()
        # A pacing discussion aid only. TTS/alignment determines actual boundaries.
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
        if float(timing["start_s"]) < 12:
            preview = (
                _asset(root, scene.still_path, "Supplied still", link_base=base) if scene.still_path else
                _asset(root, scene.clip_path, "Supplied clip", video=True, link_base=base) if scene.clip_path else
                '<p class="empty">No scene preview supplied. Review the proposed visual in the scene card.</p>'
            )
            prefix = '' if supplied_timing else '~'
            opening_shots.append(
                '<article class="opening-shot">'
                f'<strong>Scene {scene.index:02d}</strong><p class="meta">{prefix}{float(timing["start_s"]):.2f}s start · {prefix}{duration:.2f}s hold · {title}</p>'
                + preview + '</article>'
            )
        assets = ''.join(media) or '<p class="empty">No scene asset supplied. This card describes the proposed visual; it is not a generated still.</p>'
        refs = ''.join(_asset(root, path, "Scene reference", link_base=base) for path in scene.image_refs)
        motion = f'<h3>Motion direction</h3><p>{escape(scene.motion_prompt)}</p>' if scene.motion_prompt else ''
        copy = f'<h3>On-screen copy</h3><p>{escape(scene.on_screen_text)}</p><p class="meta">Review copy only; must be implemented in the visual or overlay.</p>' if scene.on_screen_text else ''
        route = ''
        if kind == 'motion_graphic':
            route = '<p class="meta">Local motion graphic. Review a representative frame and motion sample, then supply a rendered clip_path before production.</p>'
        cards.append(f'''<article class="scene {kind}" id="scene-{position}">
<div class="scene-head"><h2>Scene {scene.index:02d}</h2><span class="badge">{title}</span></div>
<p class="meta">{escape(scene.shot_type)} · {escape(scene.aspect or brief.aspect)} · {words} words · {timing_label}</p>{scene_advisory}
<div class="columns"><div><h3>Voiceover</h3><blockquote>{escape(scene.vo_text)}</blockquote>
<h3>Visual direction</h3><p>{escape(scene.prompt)}</p>{motion}{copy}{route}</div>
<div><h3>Supplied scene assets</h3><div class="gallery">{assets}</div>{'<h3>Scene references</h3><div class="gallery">' + refs + '</div>' if refs else ''}</div></div></article>''')
    provided = _reference_groups(brief, root, base)
    headline = (
        '<section class="gate"><h2>Opening headline</h2>'
        f'<blockquote>{escape(brief.headline)}</blockquote>'
        '<p class="meta">On-screen overlay during the first scene. Not spoken; excluded from the reading estimate.</p></section>'
        if brief.headline else ''
    )
    criteria = ''.join(f'<li>{escape(item)}</li>' for item in brief.success_criteria)
    counts = {kind: sum(scene.resolved_scene_type == kind for scene in brief.script.scenes)
              for kind in ("still", "animation", "motion_graphic")}
    type_summary = " · ".join(f"{count} {kind.replace('_', ' ')}{'s' if count != 1 else ''}"
                              for kind, count in counts.items() if count)
    opening_sheet = (
        '<section class="gate"><h2>Opening shots — first 12 seconds</h2>'
        '<p class="meta">Compare framing, subject scale, and visible action side by side. Supplied previews only; no automated visual-quality score.</p>'
        f'<div class="opening-grid">{"".join(opening_shots)}</div></section>'
        if opening_shots else ''
    )
    timing_summary = (
        f'<strong>Supplied timeline: {total:.2f}s.</strong><p>Timing comes from the supplied alignment file. Recheck it after changing narration or scene order.</p>'
        if supplied_timing else f'<strong>Estimated reading time: {total:.1f}s at 150 words/minute.</strong><p>These are pacing estimates, not production timing. Generated narration and alignment determine final scene durations.</p>'
    )
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(concept_title)} · Storyboard</title><style>{_STYLE}</style></head><body><main>
<div class="eyebrow">Parallax / Storyboard review</div><h1>{escape(concept_name)}</h1>
{'<p class="meta">Concept ID: ' + escape(brief.concept_id) + '</p>' if brief.concept_id else ''}
<p>{escape(brief.goal)}</p>
<p class="meta">{len(brief.script.scenes)} scenes · {escape(brief.aspect)} · voice: {escape(brief.voice)}</p>
<p class="meta">{type_summary}</p>
<div class="summary">{timing_summary}</div>
{pacing_html}
{reference_variety_html}
{opening_sheet}
<section class="gate"><h2>Checkpoint 1 — Storyboard</h2><p>Review scene order, narration, visual direction, scene types, references, and on-screen copy before generating assets.</p><p>Next: review stills and motion-graphic frames → approve a motion sample → animate approved stills and render graphics → review the final edit.</p><p class="meta">This document records proposals, not approval. Share decisions in the conversation; rendering it does not authorize generation.</p></section>
{'<h2>Success criteria</h2><ul>' + criteria + '</ul>' if criteria else ''}
{headline}
{provided}
{''.join(cards)}
<footer>Offline review — no provider calls. Image previews are embedded; original files and video previews link to local assets.</footer>
</main></body></html>'''
