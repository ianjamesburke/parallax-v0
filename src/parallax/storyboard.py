"""Offline HTML review of an authored brief; no planning or generation calls."""
from __future__ import annotations

import base64
from html import escape
from io import BytesIO
from pathlib import Path
import math

import yaml
import re

from PIL import Image, ImageOps, UnidentifiedImageError

from .brief import Brief, ProvidedAsset
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


def _asset(folder: Path, raw_path: str, label: str, *, video: bool = False) -> str:
    path = Path(raw_path).expanduser()
    path = (path if path.is_absolute() else folder / path).resolve()
    caption = f"{escape(label)} · {escape(raw_path)}"
    if not path.is_file():
        return f'<figure class="asset missing"><strong>Missing asset</strong><figcaption>{caption}</figcaption></figure>'
    uri = escape(path.as_uri(), quote=True)
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


def _reference_groups(brief: Brief, folder: Path) -> str:
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
            preview = _asset(folder, asset.path, label)
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


def render_storyboard(brief: Brief, folder: str | Path, timeline: list[dict] | None = None) -> str:
    """Return self-contained layout and image previews; video links stay local."""
    root = Path(folder).expanduser().resolve()
    concept_name = brief.concept_name or root.name or "Storyboard"
    concept_title = f"{concept_name} · {brief.concept_id}" if brief.concept_id else concept_name
    supplied_timing = timeline is not None
    if timeline is None:
        timeline, timing_basis = estimate_timeline([scene.model_dump() for scene in brief.script.scenes])
    else:
        timing_basis = "supplied aligned timeline"
        if [s.get("index") for s in timeline] != [s.index for s in brief.script.scenes]:
            raise ValueError("storyboard timing scene indices/order must match the brief")
        previous_end = 0.0
        for scene in timeline:
            start, end = float(scene["start_s"]), float(scene["end_s"])
            if not math.isfinite(start) or not math.isfinite(end) or end <= start or abs(start - previous_end) > 0.001:
                raise ValueError("storyboard timing must contain finite, positive, contiguous scene intervals starting at zero")
            previous_end = end
    first_word_start = None
    if supplied_timing and timeline:
        opening_words = timeline[0].get("words")
        if isinstance(opening_words, list) and opening_words and isinstance(opening_words[0], dict):
            value = opening_words[0].get("start")
            if isinstance(value, (int, float)) and math.isfinite(value) and value >= 0:
                first_word_start = float(value)
    pacing = review_opening_pacing(timeline, timing_basis, first_word_start_s=first_word_start)
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
        media = []
        if scene.still_path:
            media.append(_asset(root, scene.still_path, "Supplied still"))
        if scene.clip_path:
            media.append(_asset(root, scene.clip_path, "Supplied clip", video=True))
        if float(timing["start_s"]) < 12:
            preview = (
                _asset(root, scene.still_path, "Supplied still") if scene.still_path else
                _asset(root, scene.clip_path, "Supplied clip", video=True) if scene.clip_path else
                '<p class="empty">No scene preview supplied. Review the proposed visual in the scene card.</p>'
            )
            prefix = '' if supplied_timing else '~'
            opening_shots.append(
                '<article class="opening-shot">'
                f'<strong>Scene {scene.index:02d}</strong><p class="meta">{prefix}{float(timing["start_s"]):.2f}s start · {prefix}{duration:.2f}s hold · {title}</p>'
                + preview + '</article>'
            )
        assets = ''.join(media) or '<p class="empty">No scene asset supplied. This card describes the proposed visual; it is not a generated still.</p>'
        refs = ''.join(_asset(root, path, "Scene reference") for path in scene.image_refs)
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
    provided = _reference_groups(brief, root)
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
{opening_sheet}
<section class="gate"><h2>Checkpoint 1 — Storyboard</h2><p>Review scene order, narration, visual direction, scene types, references, and on-screen copy before generating assets.</p><p>Next: review stills and motion-graphic frames → approve a motion sample → animate approved stills and render graphics → review the final edit.</p><p class="meta">This document records proposals, not approval. Share decisions in the conversation; rendering it does not authorize generation.</p></section>
{'<h2>Success criteria</h2><ul>' + criteria + '</ul>' if criteria else ''}
{headline}
{provided}
{''.join(cards)}
<footer>Offline review — no provider calls. Image previews are embedded; original files and video previews link to local assets.</footer>
</main></body></html>'''


def storyboard_from_brief(brief_path: str | Path, folder: str | Path, out_path: str | Path | None = None, timing_path: str | Path | None = None) -> Path:
    brief = Brief.from_yaml(Path(brief_path).expanduser())
    root = Path(folder).expanduser().resolve()
    destination = Path(out_path).expanduser().resolve() if out_path else root / "parallax" / "scratch" / "storyboard.html"
    timeline = None
    if timing_path is not None:
        payload = yaml.safe_load(Path(timing_path).expanduser().read_text(encoding="utf-8"))
        timeline = payload.get("scenes") if isinstance(payload, dict) else payload
        if not isinstance(timeline, list) or not all(isinstance(s, dict) and "start_s" in s and "end_s" in s for s in timeline):
            raise ValueError("storyboard --timing requires an aligned manifest with scenes containing index, start_s, end_s")
    html = render_storyboard(brief, root, timeline=timeline)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(html, encoding="utf-8")
    return destination
