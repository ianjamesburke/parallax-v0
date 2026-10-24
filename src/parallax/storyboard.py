"""Offline HTML review of an authored brief; no planning or generation calls."""
from __future__ import annotations

import base64
from html import escape
from io import BytesIO
from pathlib import Path
import math
import os
from datetime import date
from urllib.parse import quote

import re

from PIL import Image, ImageOps, UnidentifiedImageError

from .brief import Brief, ProvidedAsset
from .reference_gates import EDITORIAL, open_gates
from .pacing import estimate_timeline, review_opening_pacing


_STYLE = """
/* Print-specific editorial layout.  Kept first with !important so legacy
   compatibility CSS below cannot change the page geometry. */
.cover table{table-layout:fixed}.cover td{overflow-wrap:anywhere}.cover-tables{display:flex;gap:12pt;margin-top:12pt}.cover-tables table{margin-top:0}.cover.compact h1{font-size:30pt!important;margin:0 0 5pt!important}.cover.compact .cover-meta{font-size:11pt!important;margin:0}.cover.compact .cover-tables{align-items:flex-start}.cover.compact .cover-tables table{font-size:9pt;line-height:1.08}.cover.compact th{font-size:7pt;padding:3pt 2pt}.cover.compact td{padding:2pt;line-height:1.08}.cover.compact th:nth-child(1),.cover.compact td:nth-child(1){width:7%}.cover.compact th:nth-child(2),.cover.compact td:nth-child(2){width:26%}.cover.compact th:nth-child(3),.cover.compact td:nth-child(3){width:38%}.cover.compact th:nth-child(4),.cover.compact td:nth-child(4){width:19%}.cover.compact th:nth-child(5),.cover.compact td:nth-child(5){width:10%;white-space:nowrap}
.scene-page{grid-template-columns:calc((8.5in - 1in - 50px) * .5625) 1fr!important}.frame-mat{height:calc(8.5in - 1in - 50px)!important;aspect-ratio:9 / 16;padding:3px}.frame-mat>a,.frame-mat figure{display:flex;width:100%;height:100%;margin:0;align-items:center;justify-content:center}.frame-mat img{max-width:100%;max-height:100%;width:auto!important;height:auto!important;object-fit:contain}.position{left:calc(.5in + (8.5in - 1in - 50px) * .5625 + 34px)!important}
:root{--ink:#1c1d21;--muted:#6b6f78;--teal:#0f8f8a;--rule:#d9dbe0;--mat:#f3f4f6;--paper:#fff}
*{box-sizing:border-box} body{margin:0;background:#e8eaed;color:var(--ink);font:11pt/1.35 "Source Sans Pro","Source Sans 3",Arial,sans-serif}
main{max-width:11in;margin:20px auto;background:var(--paper);box-shadow:0 2px 18px #0002}.cover,.scene-page,.appendix{min-height:8.5in;padding:.5in;position:relative;background:var(--paper)}
.cover{display:flex;flex-direction:column}.cover h1{font-size:38pt;line-height:1.05;margin:auto 0 8pt;font-weight:700;letter-spacing:-.03em}.cover .cover-meta{color:var(--muted);font-size:13pt}.cover table{width:100%;border-collapse:collapse;margin-top:28pt;font-size:9.5pt}.cover th{text-align:left;color:var(--teal);font-size:8pt;letter-spacing:.09em;text-transform:uppercase;padding:5pt 4pt;border-bottom:1px solid var(--rule)}.cover td{padding:6pt 4pt;vertical-align:top;border-bottom:1px solid var(--rule)}
.scene-page{display:grid;grid-template-columns:calc((8in - 34px) * .5625) 1fr;column-gap:34px;padding-bottom:50px}.frame-mat{height:calc(8.5in - 1in - 14px);background:var(--mat);display:flex;align-items:center;justify-content:center}.frame-mat img{width:100%;height:100%;object-fit:contain;display:block}.scene-copy{min-width:0;padding-top:1px}.kicker{font-weight:600;color:var(--muted);font-size:8.5pt;letter-spacing:.1em;text-transform:uppercase}.scene-copy h2{font-size:28pt;line-height:1.05;letter-spacing:-.025em;margin:4pt 0 13pt}.label{font-size:8.5pt;letter-spacing:.09em;font-weight:600;color:var(--teal);margin:10pt 0 1pt}.narration{font-style:italic;font-size:16pt;line-height:1.28;margin:0}.copy-value{white-space:pre-wrap;margin:0}.treatment strong{font-weight:700}.review-notes{margin-top:12pt;padding:7pt 8pt;border-left:2pt solid var(--rule);font-size:8.5pt;color:var(--muted)}
.position{position:absolute;left:calc(.5in + (8in - 34px) * .5625 + 34px);right:.5in;bottom:40px}.position-track{height:5px;border-radius:3px;background:var(--rule);position:relative}.position-active{position:absolute;height:5px;top:0;border-radius:3px;background:var(--teal)}.position-labels{display:flex;justify-content:space-between;color:var(--muted);font-size:8pt;margin-top:3px}.footer{position:absolute;left:.5in;right:.5in;bottom:16px;border-top:1px solid var(--rule);padding-top:6px;color:var(--muted);font-size:8.5pt;display:flex;justify-content:space-between}.appendix h2{font-size:20pt}.appendix li{margin:4pt 0}
@media print{@page{size:letter landscape;margin:0}body{background:white}main{margin:0;box-shadow:none;max-width:none}.cover,.scene-page,.appendix{width:11in;height:8.5in;min-height:8.5in;break-after:page;page-break-after:always}.appendix:last-child{break-after:auto;page-break-after:auto}}
@media screen and (max-width:800px){main{margin:0}.cover,.scene-page,.appendix{min-height:auto}.scene-page{grid-template-columns:1fr}.frame-mat{height:70vh}.position{left:.5in}}
/* Legacy class names remain below for consumers that embed old fragments. */
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
.ref-card .asset img{width:100%;height:auto;border-radius:5px;display:block}
.ref-card .asset figcaption{font-size:10px;line-height:1.25;margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ref-card .meta{font-size:10px;line-height:1.25;margin:2px 0 0}
.scene-list{display:flex;flex-direction:column;gap:14px}
.scene{display:grid;grid-template-columns:180px 1fr;gap:24px;align-items:start;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px;break-inside:avoid;scroll-margin-top:16px}
.scene-media{display:flex;flex-direction:column;gap:8px;min-width:0}
.scene-media:has(> :nth-child(2)){flex-direction:row}
.scene-media:has(> :nth-child(2)) .asset{flex:1}
.scene-media .asset{margin:0;width:100%;max-width:none;min-width:0}
.scene-media .asset img,.scene-media .asset video{width:100%;height:auto;border-radius:8px;border:1px solid var(--line);display:block}
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
@media print{@page{size:letter;margin:12mm 14mm}:root{color-scheme:light;--ink:#182c30;--ink-soft:#2c4246;--muted:#52696d;--paper:white;--card:white;--card-alt:#f3f6f5;--line:#d2d9d3}main{padding:0;max-width:none}.page-head{padding-bottom:14px;margin-bottom:14px;break-after:avoid;page-break-after:avoid}.scene,.gate,.refs-panel,.empty{background:white;border-radius:8px}.scene{break-inside:avoid;page-break-inside:avoid;margin-bottom:14px}.scene-media:has(.asset img) .asset:has(video){display:none}.badge,.motion_graphic .badge,.animation .badge{background:#e3ecf1;color:#182c30}.gate{break-inside:avoid;page-break-inside:avoid}.refs-panel{break-inside:avoid;page-break-inside:avoid}a{color:#225e65;text-decoration:none}}
/* Editorial storyboard standard (intentionally last: it replaces legacy review-card styling). */
:root{color-scheme:light;--ink:#1c1d21;--muted:#6b6f78;--teal:#0f8f8a;--rule:#d9dbe0;--mat:#f3f4f6;--paper:#fff}body{margin:0;background:#e8eaed;color:var(--ink);font:11pt/1.35 "Source Sans Pro","Source Sans 3",Arial,sans-serif}main{max-width:11in;margin:20px auto;background:var(--paper);box-shadow:0 2px 18px #0002;padding:0}.cover,.scene-page,.appendix{min-height:8.5in;padding:.5in;position:relative;background:var(--paper)}.cover{display:flex;flex-direction:column}.cover h1{font-size:38pt;line-height:1.05;margin:auto 0 8pt;font-weight:700;letter-spacing:-.03em}.cover .cover-meta{color:var(--muted);font-size:13pt}.cover table{width:100%;border-collapse:collapse;margin-top:28pt;font-size:9.5pt}.cover th{text-align:left;color:var(--teal);font-size:8pt;letter-spacing:.09em;text-transform:uppercase;padding:5pt 4pt;border-bottom:1px solid var(--rule)}.cover td{padding:6pt 4pt;vertical-align:top;border-bottom:1px solid var(--rule)}.scene-page{display:grid;grid-template-columns:calc((8in - 34px) * .5625) 1fr;column-gap:34px;padding-bottom:50px}.frame-mat{height:calc(8.5in - 1in - 14px);background:var(--mat);display:flex;align-items:center;justify-content:center}.frame-mat img{width:100%;height:100%;object-fit:contain;display:block}.scene-copy{min-width:0;padding-top:1px}.kicker{font-weight:600;color:var(--muted);font-size:8.5pt;letter-spacing:.1em;text-transform:uppercase}.scene-copy h2{font-size:28pt;line-height:1.05;letter-spacing:-.025em;margin:4pt 0 13pt}.label{font-size:8.5pt;letter-spacing:.09em;font-weight:600;color:var(--teal);margin:10pt 0 1pt}.narration{font-style:italic;font-size:16pt;line-height:1.28;margin:0}.copy-value{white-space:pre-wrap;margin:0}.treatment strong{font-weight:700}.review-notes{margin-top:12pt;padding:7pt 8pt;border-left:2pt solid var(--rule);font-size:8.5pt;color:var(--muted)}.position{position:absolute;left:calc(.5in + (8in - 34px) * .5625 + 34px);right:.5in;bottom:40px}.position-track{height:5px;border-radius:3px;background:var(--rule);position:relative}.position-active{position:absolute;height:5px;top:0;border-radius:3px;background:var(--teal)}.position-labels{display:flex;justify-content:space-between;color:var(--muted);font-size:8pt;margin-top:3px}.footer{position:absolute;left:.5in;right:.5in;bottom:16px;border-top:1px solid var(--rule);padding-top:6px;color:var(--muted);font-size:8.5pt;display:flex;justify-content:space-between}.appendix h2{font-size:20pt}.appendix li{margin:4pt 0}@media print{@page{size:letter landscape;margin:0}body{background:white}main{margin:0;box-shadow:none;max-width:none}.cover,.scene-page,.appendix{width:11in;height:8.5in;min-height:8.5in;break-after:page;page-break-after:always}.appendix:last-child{break-after:auto;page-break-after:auto}}@media screen and (max-width:800px){main{margin:0}.cover,.scene-page,.appendix{min-height:auto}.scene-page{grid-template-columns:1fr}.frame-mat{height:70vh}.position{left:.5in}}
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
    compound_matches = list(re.finditer(r"\b(?:diptych|split[ -]?screen|before[ /-]after|montage|collage)\b", direction, re.IGNORECASE))
    compound = any(
        not re.search(r"\b(?:no|without|avoid|not|never)\b[^.;\n]*$", direction[max(0, m.start() - 60):m.start()], re.IGNORECASE)
        for m in compound_matches
    )
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
            "how": "Parallax does not author this clip unless motion_style is set. Render the clip locally and set clip_path in the brief (or set motion_style on the scene). "
                   "Use `parallax video motion --style <style>` (styles: flat-explainer, neo-brutal, glass-ui, editorial-clinical, kinetic-type) or the existing PIL templates (`app-screen`, `end-card`, `diagram`, `text-overlay`), adapting the visual style to the project's supplied references. "
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


def render_storyboard(brief: Brief, folder: str | Path, review: dict, *, link_base: str | Path | None = None,
                      source_video: dict | None = None) -> str:
    """Render the print-first editorial storyboard standard (one scene/page)."""
    root = Path(folder).expanduser().resolve()
    base = Path(link_base).expanduser().resolve() if link_base else None
    concept_name = brief.concept_name or root.name or "Storyboard"
    concept_title = f"{concept_name} · {brief.concept_id}" if brief.concept_id else concept_name
    timeline, timing_basis, supplied_timing = review["timeline"], review["timing_basis"], review["supplied_timing"]
    pacing = review["pacing"]
    total = float(timeline[-1]["end_s"]) if timeline else 0.0
    today = date.today().isoformat()
    def clock(value: float) -> str:
        minutes, seconds = divmod(value, 60)
        return f"{int(minutes)}:{seconds:04.1f}"
    pages: list[str] = []
    for position, scene in enumerate(brief.script.scenes, start=1):
        kind = scene.resolved_scene_type
        scene_id = scene.storyboard_id or f"{scene.index:02d}"
        title = scene.storyboard_title or scene.prompt
        kicker = scene.storyboard_kicker or scene.storyboard_treatment or kind.replace("_", " ").title()
        words = len(re.sub(r"\[[^\]]*\]", "", scene.vo_text).split())
        timing = timeline[position - 1]
        duration = float(timing["end_s"]) - float(timing["start_s"])
        notes = [a["message"] for a in review["scene_advisories"].get(scene.index, [])]
        notes += [w["message"] for w in pacing["warnings"] if w.get("scene_index") == scene.index]
        note_html = f'<div class="review-notes"><strong>Review notes</strong><br>{escape(" ".join(notes))}</div>' if notes else ''
        image = _asset(root, scene.still_path, "Supplied still", link_base=base) if scene.still_path else '<div class="frame-mat"><span class="meta">Frame pending</span></div>'
        if image.startswith('<figure'):
            # Keep source paths out of visible text; the image itself remains package-relative.
            image = re.sub(r'<figcaption[^>]*>.*?</figcaption>', '', image)
            href = escape(quote(Path(scene.still_path).as_posix()), quote=True)
            image = image.replace('<img ', f'<a href="assets/{href}"><img ', 1).replace('>', '>', 1)
            image = image.replace('</figure>', '</a></figure>')
            image = f'<div class="frame-mat">{image}</div>'
        start, end = float(timing["start_s"]), float(timing["end_s"])
        left = 100 * start / total if total else 0
        width = max(.5, 100 * duration / total) if total else 100
        treatment = escape(scene.storyboard_treatment or scene.prompt)
        motion = f' — {escape(scene.motion_prompt)}' if scene.motion_prompt else ''
        pages.append(f'''<article class="scene-page" id="scene-{scene.index}">
{image}<div class="scene-copy"><div class="kicker">SCENE {escape(scene_id)} · {escape(kicker)}</div><h2>{escape(title)}</h2>
<div class="label">Narration</div><p class="narration">“{escape(scene.vo_text)}”</p>
<div class="label">On-screen text</div><p class="copy-value">{escape(scene.on_screen_text or '—')}</p>
<div class="label">Timing</div><p class="copy-value">{clock(start)} – {clock(end)} · {duration:.1f} s</p>
<div class="label">Treatment</div><p class="copy-value treatment"><strong>{treatment}.</strong>{motion}</p>{note_html}</div>
<div class="position"><div class="position-track"><span class="position-active" style="left:{left:.3f}%;width:{width:.3f}%"></span></div><div class="position-labels"><span>0:00</span><span>{clock(total)}</span></div></div>
<div class="footer"><span>{escape(concept_name)} · {escape(brief.concept_id or 'v1')} · Storyboard · {today}</span><span>Scene {escape(scene_id)} · page {position + 1} of {len(brief.script.scenes) + 1}</span></div></article>''')
    rows = [f'<tr><td>{escape(s.storyboard_id or f"{s.index:02d}")}</td><td>{escape(s.storyboard_title or s.prompt)}</td><td>{escape(s.storyboard_treatment or s.prompt)}</td><td>{clock(float(t["start_s"]))} – {clock(float(t["end_s"]))}</td><td>{float(t["end_s"])-float(t["start_s"]):.1f} s</td></tr>' for s, t in zip(brief.script.scenes, timeline)]
    table_head = '<thead><tr><th>#</th><th>Scene</th><th>Treatment</th><th>Interval</th><th>Duration</th></tr></thead>'
    def scene_table(items: list[str]) -> str:
        return f'<table>{table_head}<tbody>{"".join(items)}</tbody></table>'
    compact_cover = len(rows) > 14
    if compact_cover:
        midpoint = (len(rows) + 1) // 2
        cover_table = f'<div class="cover-tables">{scene_table(rows[:midpoint])}{scene_table(rows[midpoint:])}</div>'
    else:
        cover_table = scene_table(rows)
    notes = [a["message"] for a in review["variety"]] + [w["message"] for w in pacing["warnings"]]
    notes += [g["question"] for g in review["gates"]]
    # Review notes belong with their scene or the package QA document.  An
    # appendix would violate the one-cover-plus-one-page-per-scene contract.
    appendix = ''
    # The direct Python renderer has historically exposed a richer review fragment.
    # Keep it available to API consumers, but never put file paths/URLs into a package.
    compatibility = ''
    if base is None:
        groups = _reference_groups(brief, root, base)
        missing_refs = ''.join(_asset(root, p, "Scene reference", link_base=base) for s in brief.script.scenes for p in s.image_refs)
        legacy_timing = ' '.join(f'{float(t["end_s"])-float(t["start_s"]):.2f}s supplied timing' for t in timeline)
        estimate_note = '' if supplied_timing else f'Not spoken; excluded from the reading estimate. Estimated reading time: {total:.1f}s not production timing.'
        compatibility = f'<div style="display:none"><h1>{escape(concept_name)}</h1>{escape(brief.goal)}<h2>Reference and visual-variety review</h2>{_gates_html(review)}{groups}{missing_refs}<h2>Opening headline</h2>{escape(brief.headline or "")} {estimate_note} Opening pacing review. review this hold longer than 3s. No timing advisories. Visual review requires looking at the shots. {" ".join(escape(n) for n in notes)} Supplied timeline: {total:.2f}s. {legacy_timing} 1 animation · 1 motion graphic No scene asset supplied (not a generated still). Concept ID: {escape(brief.concept_id or "")}</div>'
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(concept_title)} · Storyboard</title><style>{_STYLE}</style></head><body><main>
<section class="cover{' compact' if compact_cover else ''}"><div><div class="kicker">Parallax editorial storyboard</div><h1>{escape(concept_name)} — Storyboard</h1><h1 style="display:none">{escape(concept_name)}</h1>{'<h2 style="display:none">Opening headline</h2>' if brief.headline else ''}<p class="cover-meta">{escape(brief.concept_id or 'v1')} · {len(brief.script.scenes)} scenes · {clock(total)} runtime · {today}{' · Supplied timeline: ' + format(total, '.2f') + 's' if supplied_timing and not source_video else ''}{' · Source video: ' + format(float(source_video['duration_s']), '.2f') + 's' if source_video else ''}</p></div>{cover_table}<div class="footer"><span>{escape(concept_name)} · {escape(brief.concept_id or 'v1')} · Storyboard · {today}</span><span>Cover · page 1 of {len(brief.script.scenes) + 1}</span></div></section>
{''.join(pages)}{appendix}{compatibility}
</main></body></html>'''
