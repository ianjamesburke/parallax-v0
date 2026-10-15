"""Offline HTML review of an authored brief; no planning or generation calls."""
from __future__ import annotations

import base64
from html import escape
from io import BytesIO
from pathlib import Path
import re

from PIL import Image, ImageOps, UnidentifiedImageError

from .brief import Brief


_STYLE = """
:root{color-scheme:light;--ink:#182c30;--muted:#52696d;--paper:#f4f1e8;--line:#d2d9d3}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.6 system-ui,sans-serif}
main{max-width:1160px;margin:auto;padding:50px 28px}h1{font:500 clamp(32px,5vw,56px)/1.12 Georgia,serif;max-width:950px;margin:12px 0 24px}
h2{font-size:24px;margin:0 0 12px}h3{font-size:13px;text-transform:uppercase;letter-spacing:.09em;margin:20px 0 7px}
p{margin:8px 0;white-space:pre-wrap}.eyebrow,.meta{color:var(--muted);font-size:13px}.eyebrow{text-transform:uppercase;letter-spacing:.17em}
.summary,.gate{padding:20px 24px;background:#e2e9df;border-radius:12px;margin:22px 0}.gate{background:#fff;border:1px solid var(--line)}
nav{display:flex;gap:8px;flex-wrap:wrap;margin:25px 0}a{color:#225e65}nav a{padding:6px 12px;border:1px solid var(--line);border-radius:25px;text-decoration:none}
.scene{background:#fff;border:1px solid var(--line);border-radius:16px;margin:26px 0;padding:28px;break-inside:avoid;scroll-margin-top:20px}
.scene-head{display:flex;justify-content:space-between;gap:16px;align-items:center}.badge{border-radius:24px;padding:6px 14px;background:#e3ecf1;font-size:13px;white-space:nowrap}
.motion_graphic .badge{background:#f6e5c7}.animation .badge{background:#e8e2f5}.columns{display:grid;grid-template-columns:1fr 1fr;gap:30px}
blockquote{font:24px/1.45 Georgia,serif;margin:18px 0;padding-left:18px;border-left:3px solid #85a19b;white-space:pre-wrap}
.gallery{display:flex;gap:16px;flex-wrap:wrap}.asset{margin:12px 0;max-width:290px;min-width:140px;overflow-wrap:anywhere}.asset img,.asset video{width:100%;max-height:330px;object-fit:contain;background:#f2f3ef;border-radius:8px}.asset figcaption{font-size:12px;color:var(--muted)}
.missing{color:#913f2b;border:1px dashed #bd8d7c;padding:14px;border-radius:8px}.empty{padding:28px;background:#f3f5f2;border-radius:8px;color:var(--muted)}
footer{color:var(--muted);font-size:13px;margin-top:40px}@media(max-width:700px){main{padding:24px 16px}.columns{grid-template-columns:1fr}.scene{padding:20px}.scene-head{align-items:flex-start}}
@media print{body{background:white}main{padding:0}nav{display:none}.scene{border-radius:0}.gate{break-inside:avoid}}
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


def render_storyboard(brief: Brief, folder: str | Path) -> str:
    """Return self-contained layout and image previews; video links stay local."""
    root = Path(folder).expanduser().resolve()
    cards: list[str] = []
    navigation: list[str] = []
    total = 0.0
    for position, scene in enumerate(brief.script.scenes, start=1):
        kind = scene.resolved_scene_type
        title = kind.replace("_", " ").title()
        # A pacing discussion aid only. TTS/alignment determines actual boundaries.
        words = len(re.sub(r"\[[^\]]*\]", "", scene.vo_text).split())
        duration = words / 2.5
        total += duration
        navigation.append(f'<a href="#scene-{position}">{scene.index:02d} · {title}</a>')
        media = []
        if scene.still_path:
            media.append(_asset(root, scene.still_path, "Supplied still"))
        if scene.clip_path:
            media.append(_asset(root, scene.clip_path, "Supplied clip", video=True))
        assets = ''.join(media) or '<p class="empty">No scene asset supplied. This card describes the proposed visual; it is not a generated still.</p>'
        refs = ''.join(_asset(root, path, "Scene reference") for path in scene.image_refs)
        motion = f'<h3>Motion direction</h3><p>{escape(scene.motion_prompt)}</p>' if scene.motion_prompt else ''
        copy = f'<h3>On-screen copy</h3><p>{escape(scene.on_screen_text)}</p><p class="meta">Review copy only; must be implemented in the visual or overlay.</p>' if scene.on_screen_text else ''
        route = ''
        if kind == 'motion_graphic':
            route = '<p class="meta">Local motion graphic. Review a representative frame and motion sample, then supply a rendered clip_path before production.</p>'
        cards.append(f'''<article class="scene {kind}" id="scene-{position}">
<div class="scene-head"><h2>Scene {scene.index:02d}</h2><span class="badge">{title}</span></div>
<p class="meta">{escape(scene.shot_type)} · {escape(scene.aspect or brief.aspect)} · {words} words · ~{duration:.1f}s reading estimate</p>
<div class="columns"><div><h3>Voiceover</h3><blockquote>{escape(scene.vo_text)}</blockquote>
<h3>Visual direction</h3><p>{escape(scene.prompt)}</p>{motion}{copy}{route}</div>
<div><h3>Supplied scene assets</h3><div class="gallery">{assets}</div>{'<h3>Scene references</h3><div class="gallery">' + refs + '</div>' if refs else ''}</div></div></article>''')
    provided = ''.join(_asset(root, asset.path, asset.description or asset.kind.replace('_', ' ')) for asset in brief.assets.provided)
    criteria = ''.join(f'<li>{escape(item)}</li>' for item in brief.success_criteria)
    counts = {kind: sum(scene.resolved_scene_type == kind for scene in brief.script.scenes)
              for kind in ("still", "animation", "motion_graphic")}
    type_summary = " · ".join(f"{count} {kind.replace('_', ' ')}{'s' if count != 1 else ''}"
                              for kind, count in counts.items() if count)
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Parallax · Storyboard</title><style>{_STYLE}</style></head><body><main>
<div class="eyebrow">Parallax / Storyboard review</div><h1>Storyboard</h1>
<p>{escape(brief.goal)}</p>
<p class="meta">{len(brief.script.scenes)} scenes · {escape(brief.aspect)} · voice: {escape(brief.voice)}</p>
<p class="meta">{type_summary}</p>
<div class="summary"><strong>Estimated reading time: {total:.1f}s at 150 words/minute.</strong><p>These are pacing estimates, not production timing. Generated narration and alignment determine final scene durations.</p></div>
<section class="gate"><h2>Checkpoint 1 — Storyboard</h2><p>Review scene order, narration, visual direction, scene types, references, and on-screen copy before generating assets.</p><p>Next: review stills and motion-graphic frames → approve a motion sample → animate approved stills and render graphics → review the final edit.</p><p class="meta">This document records proposals, not approval. Share decisions in the conversation; rendering it does not authorize generation.</p></section>
{'<h2>Success criteria</h2><ul>' + criteria + '</ul>' if criteria else ''}
{'<h2>Provided references</h2><div class="gallery">' + provided + '</div><p class="meta">References guide the look; they are not generated scene assets. Assign scene image_refs explicitly for image generation.</p>' if provided else ''}
<nav aria-label="Scenes">{''.join(navigation)}</nav>{''.join(cards)}
<footer>Offline review — no provider calls. Image previews are embedded; original files and video previews link to local assets.</footer>
</main></body></html>'''


def storyboard_from_brief(brief_path: str | Path, folder: str | Path, out_path: str | Path | None = None) -> Path:
    brief = Brief.from_yaml(Path(brief_path).expanduser())
    root = Path(folder).expanduser().resolve()
    destination = Path(out_path).expanduser().resolve() if out_path else root / "parallax" / "scratch" / "storyboard.html"
    html = render_storyboard(brief, root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(html, encoding="utf-8")
    return destination
