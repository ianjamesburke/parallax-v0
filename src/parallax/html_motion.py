"""HTML/SVG + GSAP motion-graphics generator, layout auditor, and headless Chrome CDP renderer."""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import select
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

import yaml

from parallax import usage
from parallax.motion_graphics import BRAND_DEFAULT, CAPTION_TOP, SAFE_X
from parallax.openrouter import client as openrouter_client
from parallax.shim import is_test_mode

DEFAULT_MOTION_MODEL = "google/gemini-2.5-flash"

CANVAS_WIDTH = 1080
CANVAS_HEIGHT = 1920
SAFE_LEFT = 70
SAFE_RIGHT = CANVAS_WIDTH - SAFE_LEFT  # 1010
SAFE_TOP = 80
CAPTION_TOP_PX = CAPTION_TOP * CANVAS_HEIGHT  # 1344.0 (0.70 * 1920)

_PACKAGE_DIR = Path(__file__).resolve().parent
MOTION_STYLES_DIR = _PACKAGE_DIR / "motion_styles"
CANNED_STYLES_DIR = MOTION_STYLES_DIR / "canned"
VENDOR_GSAP_PATH = _PACKAGE_DIR / "vendor" / "gsap.min.js"

CANONICAL_STYLE_ORDER = (
    "flat-explainer",
    "neo-brutal",
    "glass-ui",
    "editorial-clinical",
    "kinetic-type",
)

# Cheap per-token fallback rates for Gemini 2.5 Flash on OpenRouter ($/token)
_FLASH_PROMPT_COST_PER_TOKEN = 0.15 / 1_000_000
_FLASH_COMPLETION_COST_PER_TOKEN = 0.60 / 1_000_000
DEFAULT_MOTION_GEN_ESTIMATE_USD = 0.002


class LayoutViolationError(RuntimeError):
    """Raised when an HTML motion graphic fails layout safety checks after repair."""

    def __init__(self, violations: list[dict[str, Any]], message: str | None = None) -> None:
        self.violations = violations
        summary = "; ".join(
            f"[{v.get('type')} @ t={v.get('time_s')}s] {v.get('detail', '')}"
            for v in violations[:5]
        )
        msg = message or f"HTML motion layout failed audit ({len(violations)} violation(s)): {summary}"
        super().__init__(msg)


@dataclass
class AuditResult:
    """Structured result from `audit_layout`."""

    passed: bool
    violations: list[dict[str, Any]] = field(default_factory=list)
    sample_times: list[float] = field(default_factory=list)
    repaired: bool = False

    def __bool__(self) -> bool:
        return self.passed


def resolve_motion_model(model: str | None = None) -> str:
    """Resolve the LLM model for HTML motion generation."""
    if model and str(model).strip():
        return str(model).strip()
    env_model = os.environ.get("PARALLAX_MOTION_MODEL", "").strip()
    if env_model:
        return env_model
    return DEFAULT_MOTION_MODEL


def find_chrome_binary() -> str:
    """Find a working headless Chrome/Chromium executable.

    Checks `PARALLAX_CHROME` env var first, then `google-chrome`, `chromium`,
    `chromium-browser`, `google-chrome-stable`. Raises RuntimeError if none exist.
    """
    env_chrome = os.environ.get("PARALLAX_CHROME", "").strip()
    if env_chrome:
        resolved = shutil.which(env_chrome) or (env_chrome if Path(env_chrome).is_file() else None)
        if resolved:
            return str(resolved)
        raise RuntimeError(
            f"PARALLAX_CHROME={env_chrome!r} does not point to an executable Chrome binary."
        )

    candidates = (
        "google-chrome",
        "chromium",
        "chromium-browser",
        "google-chrome-stable",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome-stable",
    )
    for candidate in candidates:
        found = shutil.which(candidate)
        if found:
            return found
        if candidate.startswith("/") and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate

    raise RuntimeError(
        "No Chrome/Chromium binary found. Set PARALLAX_CHROME or install one of: "
        "google-chrome, chromium, chromium-browser, google-chrome-stable."
    )


def list_style_presets(styles_dir: Path | None = None) -> list[dict[str, Any]]:
    """Load all style preset data files from `src/parallax/motion_styles/`."""
    directory = styles_dir or MOTION_STYLES_DIR
    if not directory.is_dir():
        return []

    by_name: dict[str, dict[str, Any]] = {}
    for path in sorted(directory.iterdir()):
        if path.is_dir() or path.suffix.lower() not in {".yaml", ".yml", ".json"}:
            continue
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw) if path.suffix.lower() == ".json" else yaml.safe_load(raw)
        if not isinstance(data, dict):
            continue
        name = str(data.get("name") or path.stem).strip()
        data["name"] = name
        data.setdefault("description", "")
        data.setdefault("design_rules", "")
        data.setdefault("palette", {})
        data.setdefault("fonts", {})
        motion_vocab = data.get("motion_vocabulary") or data.get("motion") or {}
        data["motion_vocabulary"] = motion_vocab
        data["motion"] = motion_vocab
        data.setdefault("do", [])
        data.setdefault("dont", [])
        by_name[name] = data

    ordered: list[dict[str, Any]] = []
    for canonical in CANONICAL_STYLE_ORDER:
        if canonical in by_name:
            ordered.append(by_name.pop(canonical))
    for remaining_key in sorted(by_name):
        ordered.append(by_name[remaining_key])
    return ordered


def available_style_names(styles_dir: Path | None = None) -> list[str]:
    """Return all available motion style preset names."""
    return [preset["name"] for preset in list_style_presets(styles_dir)]


def load_style_preset(style: str, styles_dir: Path | None = None) -> dict[str, Any]:
    """Load a single motion style preset by name or raise ValueError."""
    clean = (style or "").strip()
    presets = {p["name"]: p for p in list_style_presets(styles_dir)}
    if clean not in presets:
        available = ", ".join(presets.keys()) or "(none)"
        raise ValueError(
            f"Unknown motion style {style!r}. Available styles: {available}"
        )
    return presets[clean]


def load_brand_file(path: str | Path) -> dict[str, Any]:
    """Load a brand YAML or JSON file from disk."""
    brand_path = Path(path)
    if not brand_path.is_file():
        raise FileNotFoundError(f"Brand file not found: {brand_path}")
    raw = brand_path.read_text(encoding="utf-8")
    if brand_path.suffix.lower() == ".json":
        data = json.loads(raw)
    else:
        data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ValueError(f"Brand file {brand_path} must contain a YAML/JSON mapping (got {type(data).__name__}).")
    result = dict(data)
    logo = result.get("logo")
    if isinstance(logo, str) and logo.strip() and not logo.strip().startswith("data:"):
        candidate = (brand_path.parent / logo).resolve()
        if candidate.is_file():
            result["logo"] = str(candidate)
    return result


def _default_logo_svg_data_uri(brand_name: str, accent_hex: str, ink_hex: str) -> str:
    """Build a crisp self-contained SVG badge data URI when no external logo file is given."""
    label = (brand_name or "BRAND").strip()
    initials = "".join(part[0] for part in label.split()[:2] if part).upper() or "PX"
    safe_label = (
        label[:18]
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 280 72" width="280" height="72">'
        f'<rect x="2" y="4" width="64" height="64" rx="16" fill="{accent_hex}"/>'
        f'<text x="34" y="46" text-anchor="middle" font-family="Inter, system-ui, sans-serif" '
        f'font-size="28" font-weight="800" fill="#FFFFFF">{initials}</text>'
        f'<text x="82" y="46" font-family="Inter, system-ui, sans-serif" '
        f'font-size="28" font-weight="800" fill="{ink_hex}">{safe_label}</text>'
        f"</svg>"
    )
    b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{b64}"


def encode_logo_data_uri(
    logo: str | Path | None,
    *,
    brand_name: str = "Brand",
    accent_hex: str = "#1C64F2",
    ink_hex: str = "#0B162C",
    base_dir: Path | None = None,
) -> str:
    """Inline a brand logo file (or SVG string / data URI) into a `data:image/...;base64,...` URI."""
    if logo is not None:
        raw_logo = str(logo).strip()
        if raw_logo.startswith("data:image/"):
            return raw_logo
        if raw_logo.startswith("<svg"):
            b64 = base64.b64encode(raw_logo.encode("utf-8")).decode("ascii")
            return f"data:image/svg+xml;base64,{b64}"
        if raw_logo:
            candidates = [Path(raw_logo)]
            if base_dir is not None and not Path(raw_logo).is_absolute():
                candidates.insert(0, (base_dir / raw_logo).resolve())
            for candidate in candidates:
                if candidate.is_file():
                    mime, _ = mimetypes.guess_type(candidate.name)
                    if not mime:
                        suffix = candidate.suffix.lower()
                        mime = {
                            ".svg": "image/svg+xml",
                            ".png": "image/png",
                            ".jpg": "image/jpeg",
                            ".jpeg": "image/jpeg",
                            ".webp": "image/webp",
                            ".gif": "image/gif",
                        }.get(suffix, "application/octet-stream")
                    b64 = base64.b64encode(candidate.read_bytes()).decode("ascii")
                    return f"data:{mime};base64,{b64}"
    return _default_logo_svg_data_uri(brand_name, accent_hex, ink_hex)


def normalize_brand(
    brand: dict[str, Any] | str | Path | None,
    preset: dict[str, Any] | None = None,
    *,
    base_dir: Path | None = None,
) -> dict[str, Any]:
    """Normalize a brand dict/file and merge its palette and fonts over a style preset."""
    if isinstance(brand, (str, Path)):
        raw_brand = load_brand_file(brand)
    elif isinstance(brand, dict):
        raw_brand = dict(brand)
    else:
        raw_brand = {}

    preset_palette = dict((preset or {}).get("palette") or {})
    palette = {
        "bg": preset_palette.get("bg", BRAND_DEFAULT.get("bg", "#14102A")),
        "surface": preset_palette.get("surface", BRAND_DEFAULT.get("bg2", "#FFFFFF")),
        "ink": preset_palette.get("ink", BRAND_DEFAULT.get("text", "#0B162C")),
        "accent": preset_palette.get("accent", BRAND_DEFAULT.get("accent", "#1C64F2")),
        "accent2": preset_palette.get("accent2", BRAND_DEFAULT.get("good", "#0E9F6E")),
    }

    brand_palette = raw_brand.get("palette")
    if isinstance(brand_palette, dict):
        # Map common brand palette names to preset role names first, then direct role overrides.
        alias_map = {
            "primary": "accent",
            "secondary": "accent2",
            "background": "bg",
            "card": "surface",
            "text": "ink",
            "teal": "accent2",
        }
        for src_key, dst_key in alias_map.items():
            val = brand_palette.get(src_key)
            if isinstance(val, str) and val.strip():
                palette[dst_key] = val.strip()
        for role_key in ("bg", "surface", "ink", "accent", "accent2"):
            val = brand_palette.get(role_key)
            if isinstance(val, str) and val.strip():
                palette[role_key] = val.strip()
        # Preserve any extra custom keys from brand.palette as well
        for k, v in brand_palette.items():
            if isinstance(v, str) and v.strip() and k not in palette:
                palette[k] = v.strip()

    # Also allow top-level color shortcuts if passed directly in brand dict
    for top_key, dst_key in (
        ("primary", "accent"),
        ("secondary", "accent2"),
        ("bg", "bg"),
        ("surface", "surface"),
        ("ink", "ink"),
        ("accent", "accent"),
        ("accent2", "accent2"),
    ):
        val = raw_brand.get(top_key)
        if isinstance(val, str) and val.strip():
            palette[dst_key] = val.strip()

    preset_fonts = dict((preset or {}).get("fonts") or {})
    fonts = {
        "heading": preset_fonts.get("heading", "'Inter', system-ui, -apple-system, sans-serif"),
        "body": preset_fonts.get("body", "'Inter', system-ui, -apple-system, sans-serif"),
    }
    brand_fonts = raw_brand.get("fonts")
    if isinstance(brand_fonts, dict):
        for fkey in ("heading", "body"):
            fval = brand_fonts.get(fkey)
            if isinstance(fval, str) and fval.strip():
                fonts[fkey] = fval.strip()

    brand_name = str(raw_brand.get("name") or "Parallax").strip()
    logo_data_uri = encode_logo_data_uri(
        raw_brand.get("logo"),
        brand_name=brand_name,
        accent_hex=palette["accent"],
        ink_hex=palette["ink"],
        base_dir=base_dir,
    )

    return {
        "name": brand_name,
        "logo": raw_brand.get("logo"),
        "logo_data_uri": logo_data_uri,
        "palette": palette,
        "fonts": fonts,
    }


def normalize_words(words: Any) -> list[dict[str, Any]]:
    """Normalize word timestamp inputs into `[{word, start, end}, ...]`.

    Accepts:
    - `None` -> `[]`
    - `list[{word, start, end}]` (or `text` key instead of `word`, or `start_s`/`end_s`)
    - `dict` containing `words: [...]` or `segments: [{words: [...]}]` or `scenes: [...]`
    - `str` or `Path` pointing to a JSON file containing any of the above.
    """
    if words is None:
        return []
    if isinstance(words, (str, Path)):
        p = Path(words)
        if p.is_file():
            words = json.loads(p.read_text(encoding="utf-8"))
        else:
            words = json.loads(str(words))

    raw_items: list[Any] = []
    if isinstance(words, list):
        raw_items = words
    elif isinstance(words, dict):
        if isinstance(words.get("words"), list):
            raw_items = words["words"]
        elif isinstance(words.get("segments"), list):
            for seg in words["segments"]:
                if isinstance(seg, dict) and isinstance(seg.get("words"), list):
                    raw_items.extend(seg["words"])
                elif isinstance(seg, dict) and ("word" in seg or "text" in seg):
                    raw_items.append(seg)
        elif isinstance(words.get("alignment"), list):
            raw_items = words["alignment"]

    normalized: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        word_text = str(item.get("word") or item.get("text") or item.get("token") or "").strip()
        if not word_text:
            continue
        start = float(item.get("start", item.get("start_s", item.get(" begin", 0.0))) or 0.0)
        end = float(item.get("end", item.get("end_s", start + 0.25)) or (start + 0.25))
        normalized.append(
            {
                "word": word_text,
                "start": round(start, 4),
                "end": round(max(start, end), 4),
            }
        )
    return normalized


def _build_timing_js(duration: float, fps: int = 30, words: list[dict[str, Any]] | None = None) -> str:
    """Build the JavaScript snippet that defines `window.__TIMING`."""
    norm_words = normalize_words(words)
    words_json = json.dumps(norm_words, ensure_ascii=False)
    dur_val = round(float(duration), 4)
    fps_val = int(fps)
    return (
        "window.__TIMING = {\n"
        f"  duration: {dur_val},\n"
        f"  fps: {fps_val},\n"
        f"  words: {words_json},\n"
        "  cue: function(word, fallback) {\n"
        "    var fb = (fallback !== undefined) ? fallback : 0;\n"
        "    if (word === undefined || word === null || word === '') return fb;\n"
        "    var target = String(word).toLowerCase().replace(/[^a-z0-9]+/g, '');\n"
        "    if (!target) return fb;\n"
        "    var list = this.words || [];\n"
        "    for (var i = 0; i < list.length; i++) {\n"
        "      var w = String((list[i] && list[i].word) || '').toLowerCase().replace(/[^a-z0-9]+/g, '');\n"
        "      if (w && w === target) {\n"
        "        var s = Number(list[i].start);\n"
        "        return isNaN(s) ? fb : s;\n"
        "      }\n"
        "    }\n"
        "    return fb;\n"
        "  }\n"
        "};"
    )


_TIMING_SCRIPT_RE = re.compile(
    r'<script[^>]*id=["\']parallax-timing["\'][^>]*>.*?</script>',
    re.DOTALL | re.IGNORECASE,
)
_WINDOW_TIMING_RE = re.compile(
    r"window\.__TIMING\s*=\s*\{.*?\}\s*;",
    re.DOTALL,
)


def patch_html_timing(
    html: str,
    duration: float,
    fps: int = 30,
    words: Any = None,
) -> str:
    """Replace or insert the `window.__TIMING` block in an HTML document with zero LLM calls."""
    timing_js = _build_timing_js(duration=duration, fps=fps, words=words)
    timing_script_tag = f'<script id="parallax-timing">\n{timing_js}\n</script>'

    if "/* __PARALLAX_TIMING_BLOCK__ */" in html:
        return html.replace("/* __PARALLAX_TIMING_BLOCK__ */", timing_js)

    if _TIMING_SCRIPT_RE.search(html):
        return _TIMING_SCRIPT_RE.sub(lambda _m: timing_script_tag, html, count=1)

    if _WINDOW_TIMING_RE.search(html):
        return _WINDOW_TIMING_RE.sub(lambda _m: timing_js, html, count=1)

    # Otherwise inject `<script id="parallax-timing">` right before the last `<script>` or `</head>` or `</body>`
    if "</head>" in html:
        return html.replace("</head>", f"{timing_script_tag}\n</head>", 1)
    if "<script" in html:
        return html.replace("<script", f"{timing_script_tag}\n<script", 1)
    return f"{timing_script_tag}\n{html}"


def _load_vendored_gsap() -> str:
    """Read the vendored GSAP 3.15.0 script from `src/parallax/vendor/gsap.min.js`."""
    if not VENDOR_GSAP_PATH.is_file():
        raise FileNotFoundError(f"Vendored GSAP not found at {VENDOR_GSAP_PATH}")
    return VENDOR_GSAP_PATH.read_text(encoding="utf-8")


_EXTERNAL_SCRIPT_RE = re.compile(
    r'<script\b[^>]*\bsrc=["\'](?:https?:)?//[^"\']+["\'][^>]*>\s*</script>',
    re.IGNORECASE | re.DOTALL,
)
_EXTERNAL_LINK_RE = re.compile(
    r'<link\b[^>]*\bhref=["\'](?:https?:)?//[^"\']+["\'][^>]*/?>',
    re.IGNORECASE | re.DOTALL,
)


def strip_external_network_tags(html: str) -> str:
    """Remove any CDN `<script src="http...">` or `<link href="http...">` tags emitted by an LLM."""
    cleaned = _EXTERNAL_SCRIPT_RE.sub("", html)
    cleaned = _EXTERNAL_LINK_RE.sub("", cleaned)
    return cleaned


def inline_gsap_script(html: str) -> str:
    """Inline vendored `gsap.min.js` into the HTML document if not already present."""
    gsap_js = _load_vendored_gsap()
    if "/* __PARALLAX_GSAP_SCRIPT__ */" in html:
        return html.replace("/* __PARALLAX_GSAP_SCRIPT__ */", gsap_js)
    if 'id="parallax-gsap"' in html or "GreenSock" in html:
        return html
    gsap_tag = f'<script id="parallax-gsap">\n{gsap_js}\n</script>'
    if "</head>" in html:
        return html.replace("</head>", f"{gsap_tag}\n</head>", 1)
    if "<script" in html:
        return html.replace("<script", f"{gsap_tag}\n<script", 1)
    return f"{gsap_tag}\n{html}"


def _split_scene_copy(scene_text: str) -> dict[str, str]:
    """Derive concise tag, headline, subtitle, and CTA strings from `scene_text` for canned templates."""
    cleaned = " ".join((scene_text or "").strip().split())
    if not cleaned:
        cleaned = "Stop phone scams before they reach you."

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", cleaned) if s.strip()]
    first = sentences[0] if sentences else cleaned
    rest = " ".join(sentences[1:]) if len(sentences) > 1 else ""

    words = first.split()
    if len(words) <= 7:
        headline = first
        if not rest:
            sub = "Verified real-time protection built for every call and message."
        else:
            sub = rest
    else:
        headline = " ".join(words[:6])
        sub = " ".join(words[6:]) + (" " + rest if rest else "")

    # Clamp lengths so text is never cropped inside cards
    if len(headline) > 44:
        headline = headline[:41].rstrip() + "..."
    if len(sub) > 110:
        sub = sub[:107].rstrip() + "..."

    # Pick a short tag and CTA
    alpha_words = [re.sub(r"[^A-Za-z0-9]+", "", w) for w in cleaned.split()]
    alpha_words = [w for w in alpha_words if len(w) >= 3]
    tag = (alpha_words[0].upper() + " ALERT") if alpha_words else "LIVE UPDATE"
    if len(tag) > 18:
        tag = tag[:18]
    cta = "Learn More"

    cue1 = alpha_words[0].lower() if len(alpha_words) >= 1 else "scam"
    cue2 = alpha_words[min(2, len(alpha_words) - 1)].lower() if len(alpha_words) >= 2 else "protect"

    return {
        "tag": tag,
        "headline": headline,
        "sub": sub,
        "cta": cta,
        "cue1": cue1,
        "cue2": cue2,
    }


def _escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _render_canned_html(
    *,
    style: str,
    preset: dict[str, Any],
    norm_brand: dict[str, Any],
    scene_text: str,
    duration_s: float,
    words: list[dict[str, Any]],
) -> str:
    """Populate the style's canned HTML template without any network or LLM calls."""
    canned_path = CANNED_STYLES_DIR / f"{style}.html"
    if not canned_path.is_file():
        canned_path = CANNED_STYLES_DIR / "flat-explainer.html"
    html = canned_path.read_text(encoding="utf-8")

    copy_parts = _split_scene_copy(scene_text)
    if words:
        first_word = re.sub(r"[^a-z0-9]+", "", str(words[0].get("word", "")).lower())
        mid_word = re.sub(
            r"[^a-z0-9]+",
            "",
            str(words[min(len(words) - 1, max(1, len(words) // 2))].get("word", "")).lower(),
        )
        if first_word:
            copy_parts["cue1"] = first_word
        if mid_word:
            copy_parts["cue2"] = mid_word

    palette = norm_brand["palette"]
    fonts = norm_brand["fonts"]
    replacements = {
        "__PALETTE_BG__": palette["bg"],
        "__PALETTE_SURFACE__": palette["surface"],
        "__PALETTE_INK__": palette["ink"],
        "__PALETTE_ACCENT__": palette["accent"],
        "__PALETTE_ACCENT2__": palette["accent2"],
        "__FONT_HEADING__": fonts["heading"],
        "__FONT_BODY__": fonts["body"],
        "__BRAND_LOGO_DATA_URI__": norm_brand["logo_data_uri"],
        "__BRAND_NAME__": _escape_html(norm_brand["name"]),
        "__SCENE_TAG__": _escape_html(copy_parts["tag"]),
        "__SCENE_HEADLINE__": _escape_html(copy_parts["headline"]),
        "__SCENE_SUB__": _escape_html(copy_parts["sub"]),
        "__SCENE_CTA__": _escape_html(copy_parts["cta"]),
        "__CUE_WORD_1__": copy_parts["cue1"],
        "__CUE_WORD_2__": copy_parts["cue2"],
    }
    for placeholder, val in replacements.items():
        html = html.replace(placeholder, val)

    html = inline_gsap_script(html)
    html = patch_html_timing(html, duration=duration_s, fps=30, words=words)
    return html


def build_motion_system_prompt(
    preset: dict[str, Any],
    norm_brand: dict[str, Any],
    duration_s: float,
) -> str:
    """Build the system prompt for HTML/SVG + GSAP scene generation."""
    palette = norm_brand["palette"]
    fonts = norm_brand["fonts"]
    vocab = preset.get("motion_vocabulary") or {}
    do_list = "\n".join(f"  - DO: {item}" for item in (preset.get("do") or []))
    dont_list = "\n".join(f"  - DON'T: {item}" for item in (preset.get("dont") or []))

    return f"""You are an expert motion designer building a single self-contained HTML5/SVG + GSAP vertical video scene.

1. CANVAS & SAFE ZONES (MANDATORY):
- Viewport / canvas size is strictly {CANVAS_WIDTH}x{CANVAS_HEIGHT} pixels (`width: 1080px; height: 1920px; overflow: hidden; position: relative;`).
- Safe box for ALL on-screen text and cards: `left >= {SAFE_LEFT}px`, `right <= {SAFE_RIGHT}px`, `top >= {SAFE_TOP}px`.
- Caption-safe zone: the bottom band from `y = {CAPTION_TOP_PX:.0f}px` to `1920px` (`CAPTION_TOP = {CAPTION_TOP}`) is reserved for burned-in captions. No text element may extend below `y = {CAPTION_TOP_PX:.0f}px`.
- HARD RULE: On-screen text is NEVER cropped (`scrollWidth <= clientWidth`, `scrollHeight <= clientHeight`, never outside the safe box, never overlapping other text, and never intruding into the caption-safe zone).

2. STYLE PRESET: {preset.get("name")} — {preset.get("description")}
Design Rules:
{preset.get("design_rules", "").strip()}
Motion Vocabulary:
- Eases: {", ".join(vocab.get("eases") or ["power3.out"])}
- Durations: {", ".join(vocab.get("durations") or ["0.45s"])}
- Entrances: {", ".join(vocab.get("entrances") or ["fade-up"])}
Style Guidelines:
{do_list}
{dont_list}

3. BRAND PALETTE, FONTS & LOGO:
- Brand Name: {norm_brand["name"]}
- Palette Roles:
  - bg: {palette["bg"]}
  - surface: {palette["surface"]}
  - ink: {palette["ink"]}
  - accent: {palette["accent"]}
  - accent2: {palette["accent2"]}
- Font Stack:
  - heading: {fonts["heading"]}
  - body: {fonts["body"]}
- Brand Logo Instruction: Include `<img id="brand-logo" src="__BRAND_LOGO_DATA_URI__" alt="{norm_brand["name"]}" />` inside the safe zone. Do NOT invent external image URLs or file paths; `__BRAND_LOGO_DATA_URI__` is replaced with an inline data URI at render time.

4. TIMING CONTRACT (GSAP):
- Duration: {duration_s:.2f} seconds.
- GSAP is injected inline by the host renderer before your script runs. Do NOT include any `<script src="https://...">` or CDN links.
- Before your script runs, `window.__TIMING = {{ duration, fps, words: [{{word, start, end}}], cue(word, fallback) }}` is injected.
- `window.__TIMING.cue('scam', 0.5)` returns the start timestamp (seconds) of the first matching voiceover word (case-insensitive, punctuation-stripped), or `fallback` if not found. Use `window.__TIMING.cue(...)` to key animation beats to voiceover words.
- Your script MUST create a paused master timeline and expose `window.__seek(t)`:
  ```js
  const tl = gsap.timeline({{ paused: true }});
  // build tweens on tl ...
  window.__seek = (t) => {{ tl.seek(t, false); }};
  window.__seek(0);
  ```

5. OUTPUT FORMAT:
- Output ONLY a single complete `<!DOCTYPE html><html>...</html>` document with zero external network requests.
"""


def _extract_html_document(raw_content: str) -> str:
    """Strip markdown code fences around an LLM HTML response if present."""
    text = (raw_content or "").strip()
    fence_match = re.search(r"```(?:html)?\s*(<!DOCTYPE html>.*?</html>|<html.*?</html>)\s*```", text, re.DOTALL | re.IGNORECASE)
    if fence_match:
        return fence_match.group(1).strip()
    doc_match = re.search(r"(<!DOCTYPE html>.*?</html>|<html.*?</html>)", text, re.DOTALL | re.IGNORECASE)
    if doc_match:
        return doc_match.group(1).strip()
    return text


def _call_openrouter_chat(
    *,
    model_id: str,
    messages: list[dict[str, str]],
    prompt_preview: str,
    temperature: float = 0.3,
) -> dict[str, Any]:
    """Call OpenRouter `/chat/completions` via `parallax.openrouter.client._post` and record usage."""
    api_model = openrouter_client._strip_or_prefix(model_id)
    t0 = time.monotonic()
    raw_resp = openrouter_client._post(
        "/chat/completions",
        {
            "model": api_model,
            "messages": messages,
            "temperature": temperature,
        },
    )
    duration_ms = int((time.monotonic() - t0) * 1000)

    if hasattr(raw_resp, "json"):
        openrouter_client._raise_for_credits_or_status(raw_resp)
        payload = raw_resp.json()
    elif isinstance(raw_resp, dict):
        payload = raw_resp
    else:
        raise RuntimeError(f"Unexpected response from OpenRouter _post: {type(raw_resp)!r}")

    usage_block = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage_block, dict):
        usage_block = {}
    prompt_tokens = int(usage_block.get("prompt_tokens") or 0)
    completion_tokens = int(usage_block.get("completion_tokens") or 0)
    total_tokens = int(usage_block.get("total_tokens") or (prompt_tokens + completion_tokens))

    gen_id = payload.get("id") if isinstance(payload, dict) else None
    openrouter_client.note_generation_id(gen_id)

    raw_cost = usage_block.get("cost") or usage_block.get("total_cost") or payload.get("cost")
    if raw_cost is not None:
        cost_usd = float(raw_cost)
        cost_source = "billed"
        openrouter_client.note_billed_cost({"usage": {"cost": cost_usd}})
    else:
        cost_usd = (
            prompt_tokens * _FLASH_PROMPT_COST_PER_TOKEN
            + completion_tokens * _FLASH_COMPLETION_COST_PER_TOKEN
        ) or DEFAULT_MOTION_GEN_ESTIMATE_USD
        cost_source = "estimate"
        openrouter_client.note_estimated_cost(cost_usd)

    usage.record(
        session_id=os.environ.get("PARALLAX_SESSION_ID"),
        backend="openrouter",
        alias=api_model,
        fal_id=f"openrouter/{api_model}",
        tier="fast",
        prompt=prompt_preview[:120],
        output_path="",
        duration_ms=duration_ms,
        cost_usd=cost_usd,
        cost_source=cost_source,
        generation_ids=[str(gen_id)] if gen_id else [],
        test_mode=False,
    )
    payload["_parallax_usage"] = {
        "model": api_model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "cost_usd": cost_usd,
        "cost_source": cost_source,
    }
    return payload


def _finalize_llm_html(
    raw_html: str,
    *,
    norm_brand: dict[str, Any],
    duration_s: float,
    words: list[dict[str, Any]],
) -> str:
    """Sanitize LLM HTML, inline logo data URI, inline GSAP, and inject `window.__TIMING`."""
    html = _extract_html_document(raw_html)
    html = strip_external_network_tags(html)
    html = html.replace("__BRAND_LOGO_DATA_URI__", norm_brand["logo_data_uri"])
    # Also ensure `<img id="brand-logo">` has our data URI if the LLM wrote a dummy src
    html = re.sub(
        r'(<img\b[^>]*\bid=["\']brand-logo["\'][^>]*\bsrc=["\'])([^"\']*)(["\'])',
        lambda m: f"{m.group(1)}{norm_brand['logo_data_uri']}{m.group(3)}",
        html,
        flags=re.IGNORECASE,
    )
    html = inline_gsap_script(html)
    html = patch_html_timing(html, duration=duration_s, fps=30, words=words)
    return html


def generate_scene_html(
    scene_text: str,
    style: str,
    brand: dict[str, Any] | str | Path | None = None,
    duration_s: float = 5.0,
    words: Any = None,
    model: str | None = None,
) -> str:
    """Generate a self-contained 1080x1920 HTML/SVG + GSAP motion graphic scene.

    In test mode (`PARALLAX_TEST_MODE=1`), makes zero network calls and populates
    the style's canned HTML template at $0 cost.
    In real mode, calls OpenRouter `/chat/completions` (default `google/gemini-2.5-flash`).
    """
    preset = load_style_preset(style)
    norm_brand = normalize_brand(brand, preset)
    norm_words = normalize_words(words)
    dur = max(0.2, float(duration_s or 5.0))

    if is_test_mode():
        return _render_canned_html(
            style=preset["name"],
            preset=preset,
            norm_brand=norm_brand,
            scene_text=scene_text,
            duration_s=dur,
            words=norm_words,
        )

    resolved_model = resolve_motion_model(model)
    system_prompt = build_motion_system_prompt(preset, norm_brand, dur)
    user_prompt = json.dumps(
        {
            "scene_text": scene_text,
            "style": preset["name"],
            "brand_name": norm_brand["name"],
            "palette": norm_brand["palette"],
            "fonts": norm_brand["fonts"],
            "duration_s": dur,
            "words": norm_words,
        },
        ensure_ascii=False,
        indent=2,
    )

    resp = _call_openrouter_chat(
        model_id=resolved_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    "Generate the complete self-contained 1080x1920 HTML document for this scene:\n"
                    f"{user_prompt}"
                ),
            },
        ],
        prompt_preview=scene_text or preset["name"],
        temperature=0.3,
    )

    choices = resp.get("choices") or []
    content = ""
    if choices and isinstance(choices[0], dict):
        msg = choices[0].get("message") or {}
        content = str(msg.get("content") or "")
    if not content.strip():
        raise RuntimeError(f"OpenRouter returned empty HTML content for model {resolved_model!r}")

    return _finalize_llm_html(
        content,
        norm_brand=norm_brand,
        duration_s=dur,
        words=norm_words,
    )


def repair_scene_html_with_llm(
    html: str,
    violations: list[dict[str, Any]],
    *,
    style: str,
    brand: dict[str, Any] | str | Path | None = None,
    duration_s: float = 5.0,
    words: Any = None,
    model: str | None = None,
) -> str:
    """Run a single LLM repair pass given the layout violation list from `audit_layout`."""
    preset = load_style_preset(style)
    norm_brand = normalize_brand(brand, preset)
    norm_words = normalize_words(words)
    dur = max(0.2, float(duration_s or 5.0))
    resolved_model = resolve_motion_model(model)
    system_prompt = build_motion_system_prompt(preset, norm_brand, dur)

    # Replace long inline scripts/URIs with placeholders before sending to LLM to save tokens
    compact_html = _TIMING_SCRIPT_RE.sub("<!-- timing injected by host -->", html)
    compact_html = re.sub(
        r'<script[^>]*id=["\']parallax-gsap["\'][^>]*>.*?</script>',
        "<!-- gsap injected by host -->",
        compact_html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    compact_html = re.sub(
        r"data:image/[^\"'\s>]+",
        "__BRAND_LOGO_DATA_URI__",
        compact_html,
    )

    repair_prompt = (
        "The HTML scene below failed the layout safety audit with the following violations:\n"
        f"{json.dumps(violations, indent=2)}\n\n"
        "Fix ALL layout violations so that:\n"
        f"1. Every visible text element stays strictly inside safe bounds: left >= {SAFE_LEFT}, right <= {SAFE_RIGHT}, top >= {SAFE_TOP}, bottom <= {CAPTION_TOP_PX:.0f}.\n"
        "2. No text element is clipped (`scrollWidth <= clientWidth` and `scrollHeight <= clientHeight`).\n"
        "3. No two visible text elements overlap.\n"
        "Return ONLY the complete corrected `<!DOCTYPE html><html>...</html>` document.\n\n"
        f"Current HTML:\n{compact_html}"
    )

    resp = _call_openrouter_chat(
        model_id=resolved_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": repair_prompt},
        ],
        prompt_preview=f"repair:{preset['name']}",
        temperature=0.2,
    )

    choices = resp.get("choices") or []
    content = ""
    if choices and isinstance(choices[0], dict):
        msg = choices[0].get("message") or {}
        content = str(msg.get("content") or "")
    if not content.strip():
        raise RuntimeError(f"OpenRouter repair pass returned empty content for model {resolved_model!r}")

    return _finalize_llm_html(
        content,
        norm_brand=norm_brand,
        duration_s=dur,
        words=norm_words,
    )


class _CDPPipeSession:
    """Lightweight Chrome DevTools Protocol client over `--remote-debugging-pipe` (fds 3 & 4).

    Requires zero third-party packages and opens no network sockets.
    """

    def __init__(self, chrome_bin: str, width: int = CANVAS_WIDTH, height: int = CANVAS_HEIGHT) -> None:
        self.chrome_bin = chrome_bin
        self.width = width
        self.height = height
        self._proc: subprocess.Popen[bytes] | None = None
        self._c2b_w: int | None = None
        self._b2c_r: int | None = None
        self._buf = b""
        self._msg_id = 0
        self._session_id: str | None = None

    def __enter__(self) -> _CDPPipeSession:
        c2b_r, c2b_w = os.pipe()
        b2c_r, b2c_w = os.pipe()

        def _setup_child_fds() -> None:
            # Ensure target fds 3 and 4 do not collide before dup2
            r_fd = os.dup(c2b_r) if c2b_r in (3, 4) else c2b_r
            w_fd = os.dup(b2c_w) if b2c_w in (3, 4) else b2c_w
            os.dup2(r_fd, 3)
            os.dup2(w_fd, 4)
            os.set_inheritable(3, True)
            os.set_inheritable(4, True)

        args = [
            self.chrome_bin,
            "--headless=new",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--hide-scrollbars",
            "--mute-audio",
            "--disable-background-timer-throttling",
            "--disable-renderer-backgrounding",
            "--disable-backgrounding-occluded-windows",
            f"--window-size={self.width},{self.height}",
            "--remote-debugging-pipe",
            "about:blank",
        ]
        self._proc = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            pass_fds=(3, 4),
            preexec_fn=_setup_child_fds,
        )
        os.close(c2b_r)
        os.close(b2c_w)
        self._c2b_w = c2b_w
        self._b2c_r = b2c_r
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        for fd in (self._c2b_w, self._b2c_r):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
        self._c2b_w = None
        self._b2c_r = None
        if self._proc is not None:
            if self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                    self._proc.wait()
            self._proc = None

    def send(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        if self._c2b_w is None or self._b2c_r is None:
            raise RuntimeError("CDP pipe session is not open.")
        self._msg_id += 1
        req_id = self._msg_id
        payload: dict[str, Any] = {"id": req_id, "method": method, "params": params or {}}
        sid = session_id if session_id is not None else self._session_id
        if sid:
            payload["sessionId"] = sid
        raw = json.dumps(payload).encode("utf-8") + b"\x00"
        os.write(self._c2b_w, raw)

        while True:
            while b"\x00" in self._buf:
                part, self._buf = self._buf.split(b"\x00", 1)
                if not part:
                    continue
                msg = json.loads(part.decode("utf-8"))
                if msg.get("id") == req_id:
                    if "error" in msg:
                        raise RuntimeError(f"CDP {method} failed: {msg['error']}")
                    return msg.get("result") or {}
            ready, _, _ = select.select([self._b2c_r], [], [], timeout)
            if not ready:
                raise TimeoutError(f"Timed out waiting for CDP response to {method}")
            chunk = os.read(self._b2c_r, 65536)
            if not chunk:
                raise RuntimeError(f"Chrome closed CDP pipe unexpectedly during {method}")
            self._buf += chunk

    def open_page(self, html_path: Path) -> None:
        """Attach to the browser's page target, set viewport metrics, and navigate to `html_path`."""
        targets = self.send("Target.getTargets", session_id="").get("targetInfos") or []
        page_target_id: str | None = None
        for t in targets:
            if t.get("type") == "page":
                page_target_id = t.get("targetId")
                break
        if not page_target_id:
            page_target_id = self.send(
                "Target.createTarget", {"url": "about:blank"}, session_id=""
            ).get("targetId")
        attached = self.send(
            "Target.attachToTarget",
            {"targetId": page_target_id, "flatten": True},
            session_id="",
        )
        self._session_id = attached["sessionId"]
        self.send("Page.enable")
        self.send("Runtime.enable")
        self.send(
            "Emulation.setDeviceMetricsOverride",
            {
                "width": self.width,
                "height": self.height,
                "deviceScaleFactor": 1,
                "mobile": False,
            },
        )
        file_url = html_path.resolve().as_uri()
        self.send("Page.navigate", {"url": file_url})
        # Wait for document.readyState === 'complete' and fonts ready
        self.send(
            "Runtime.evaluate",
            {
                "expression": (
                    "new Promise((resolve) => {"
                    "  const done = () => {"
                    "    if (document.fonts && document.fonts.ready) {"
                    "      document.fonts.ready.then(() => resolve(true)).catch(() => resolve(true));"
                    "    } else { resolve(true); }"
                    "  };"
                    "  if (document.readyState === 'complete') done();"
                    "  else window.addEventListener('load', done, { once: true });"
                    "})"
                ),
                "awaitPromise": True,
                "returnByValue": True,
            },
        )


_AUDIT_JS_TEMPLATE = """
(() => {
  const t = __SAMPLE_TIME__;
  if (typeof window.__seek === 'function') {
    window.__seek(t);
  }
  const W = __WIDTH__;
  const H = __HEIGHT__;
  const SAFE_L = __SAFE_LEFT__;
  const SAFE_R = __SAFE_RIGHT__;
  const SAFE_T = __SAFE_TOP__;
  const CAPTION_Y = __CAPTION_TOP_PX__;
  const EPS = 2.0;

  const IGNORED_TAGS = new Set(['SCRIPT', 'STYLE', 'TITLE', 'HEAD', 'META', 'LINK', 'DEFS', 'DESC', 'CLIPPATH', 'MASK', 'LINEARGRADIENT', 'RADIALGRADIENT']);

  function isElementVisible(el) {
    let cur = el;
    while (cur && cur.nodeType === 1) {
      const st = window.getComputedStyle(cur);
      if (st.display === 'none' || st.visibility === 'hidden' || st.visibility === 'collapse') {
        return false;
      }
      if (parseFloat(st.opacity) <= 0.05) {
        return false;
      }
      cur = cur.parentElement;
    }
    return true;
  }

  function hasDirectText(el) {
    if (IGNORED_TAGS.has(el.tagName.toUpperCase())) return false;
    for (let i = 0; i < el.childNodes.length; i++) {
      const n = el.childNodes[i];
      if (n.nodeType === 3 && n.nodeValue && n.nodeValue.trim().length > 0) {
        return true;
      }
    }
    return false;
  }

  const all = Array.from(document.querySelectorAll('body *'));
  const textEntries = [];
  const violations = [];

  for (const el of all) {
    if (!hasDirectText(el)) continue;
    if (!isElementVisible(el)) continue;
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0.5 || rect.height <= 0.5) continue;

    const textSample = (el.textContent || '').trim().slice(0, 60);
    const isSvg = el instanceof SVGElement;

    // 1. Internal overflow / clipping check (scrollWidth > clientWidth)
    if (!isSvg && el.clientWidth > 0 && el.scrollWidth > el.clientWidth + EPS) {
      violations.push({
        type: 'text_clipped',
        time_s: t,
        text: textSample,
        detail: `scrollWidth (${el.scrollWidth}) > clientWidth (${el.clientWidth}) for "${textSample}"`,
        rect: { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom }
      });
    }
    if (!isSvg && el.clientHeight > 0 && el.scrollHeight > el.clientHeight + EPS) {
      const st = window.getComputedStyle(el);
      if (st.overflowY === 'hidden' || st.overflowY === 'clip' || st.overflow === 'hidden' || st.overflow === 'clip') {
        violations.push({
          type: 'text_clipped',
          time_s: t,
          text: textSample,
          detail: `scrollHeight (${el.scrollHeight}) > clientHeight (${el.clientHeight}) with overflow hidden for "${textSample}"`,
          rect: { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom }
        });
      }
    }

    // Check if an ancestor with overflow:hidden/clip crops this text element's bounding rect
    let parent = el.parentElement;
    while (parent && parent !== document.body && parent !== document.documentElement) {
      const pst = window.getComputedStyle(parent);
      if (pst.overflow === 'hidden' || pst.overflow === 'clip' || pst.overflowX === 'hidden' || pst.overflowX === 'clip') {
        const prect = parent.getBoundingClientRect();
        if (
          rect.left < prect.left - EPS ||
          rect.right > prect.right + EPS ||
          rect.top < prect.top - EPS ||
          rect.bottom > prect.bottom + EPS
        ) {
          violations.push({
            type: 'text_clipped_by_container',
            time_s: t,
            text: textSample,
            detail: `Text "${textSample}" extends outside clipping container (${Math.round(rect.left)}..${Math.round(rect.right)} vs ${Math.round(prect.left)}..${Math.round(prect.right)})`,
            rect: { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom }
          });
          break;
        }
      }
      parent = parent.parentElement;
    }

    // 2. Viewport bounds check
    if (rect.left < -EPS || rect.right > W + EPS || rect.top < -EPS || rect.bottom > H + EPS) {
      violations.push({
        type: 'outside_viewport',
        time_s: t,
        text: textSample,
        detail: `Text "${textSample}" is outside viewport (left=${rect.left.toFixed(1)}, top=${rect.top.toFixed(1)}, right=${rect.right.toFixed(1)}, bottom=${rect.bottom.toFixed(1)})`,
        rect: { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom }
      });
    }

    // 3. Safe box check (left >= 70, right <= 1010, top >= 80)
    if (rect.left < SAFE_L - EPS || rect.right > SAFE_R + EPS || rect.top < SAFE_T - EPS) {
      violations.push({
        type: 'outside_safe_box',
        time_s: t,
        text: textSample,
        detail: `Text "${textSample}" is outside safe box [left>=${SAFE_L}, right<=${SAFE_R}, top>=${SAFE_T}] (got left=${rect.left.toFixed(1)}, right=${rect.right.toFixed(1)}, top=${rect.top.toFixed(1)})`,
        rect: { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom }
      });
    }

    // 4. Caption-safe zone intrusion (bottom > CAPTION_TOP * 1920)
    if (rect.bottom > CAPTION_Y + EPS) {
      violations.push({
        type: 'caption_zone_intrusion',
        time_s: t,
        text: textSample,
        detail: `Text "${textSample}" intrudes into caption-safe zone (bottom=${rect.bottom.toFixed(1)} > ${CAPTION_Y.toFixed(1)})`,
        rect: { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom }
      });
    }

    textEntries.push({ el, rect, text: textSample });
  }

  // 5. Text-on-text overlap check (excluding ancestor/descendant relationships)
  for (let i = 0; i < textEntries.length; i++) {
    for (let j = i + 1; j < textEntries.length; j++) {
      const a = textEntries[i];
      const b = textEntries[j];
      if (a.el.contains(b.el) || b.el.contains(a.el)) continue;
      const overlapX = Math.min(a.rect.right, b.rect.right) - Math.max(a.rect.left, b.rect.left);
      const overlapY = Math.min(a.rect.bottom, b.rect.bottom) - Math.max(a.rect.top, b.rect.top);
      if (overlapX > 4.0 && overlapY > 4.0) {
        violations.push({
          type: 'text_overlap',
          time_s: t,
          text: `${a.text} <-> ${b.text}`,
          detail: `Text "${a.text}" overlaps "${b.text}" by ${overlapX.toFixed(1)}x${overlapY.toFixed(1)}px`,
          rect: { left: a.rect.left, top: a.rect.top, right: a.rect.right, bottom: a.rect.bottom }
        });
      }
    }
  }

  return violations;
})()
"""


def audit_layout(
    html_or_path: str | Path,
    duration_s: float = 5.0,
    *,
    sample_fractions: Sequence[float] = (0.25, 0.5, 0.75, 1.0),
    width: int = CANVAS_WIDTH,
    height: int = CANVAS_HEIGHT,
    safe_left: int = SAFE_LEFT,
    safe_right: int = SAFE_RIGHT,
    safe_top: int = SAFE_TOP,
    caption_top_px: float = CAPTION_TOP_PX,
) -> AuditResult:
    """Inspect an HTML motion graphic at sample timestamps in headless Chrome and flag layout violations."""
    chrome_bin = find_chrome_binary()
    dur = max(0.2, float(duration_s or 5.0))
    sample_times = [round(dur * float(frac), 4) for frac in sample_fractions]

    temp_file: tempfile.NamedTemporaryFile[str] | None = None
    try:
        if isinstance(html_or_path, Path) or (
            isinstance(html_or_path, str)
            and "<" not in html_or_path
            and Path(html_or_path).is_file()
        ):
            target_path = Path(html_or_path)
        else:
            temp_file = tempfile.NamedTemporaryFile(
                mode="w", suffix=".html", encoding="utf-8", delete=False
            )
            temp_file.write(str(html_or_path))
            temp_file.flush()
            temp_file.close()
            target_path = Path(temp_file.name)

        all_violations: list[dict[str, Any]] = []
        with _CDPPipeSession(chrome_bin, width=width, height=height) as cdp:
            cdp.open_page(target_path)
            for t in sample_times:
                expr = (
                    _AUDIT_JS_TEMPLATE.replace("__SAMPLE_TIME__", repr(float(t)))
                    .replace("__WIDTH__", str(int(width)))
                    .replace("__HEIGHT__", str(int(height)))
                    .replace("__SAFE_LEFT__", str(int(safe_left)))
                    .replace("__SAFE_RIGHT__", str(int(safe_right)))
                    .replace("__SAFE_TOP__", str(int(safe_top)))
                    .replace("__CAPTION_TOP_PX__", repr(float(caption_top_px)))
                )
                res = cdp.send(
                    "Runtime.evaluate",
                    {"expression": expr, "returnByValue": True, "awaitPromise": True},
                )
                value = (res.get("result") or {}).get("value")
                if isinstance(value, list):
                    all_violations.extend(value)

        return AuditResult(
            passed=(len(all_violations) == 0),
            violations=all_violations,
            sample_times=sample_times,
        )
    finally:
        if temp_file is not None:
            try:
                Path(temp_file.name).unlink(missing_ok=True)
            except OSError:
                pass


def audit_and_repair_html(
    html: str,
    *,
    duration_s: float = 5.0,
    style: str = "flat-explainer",
    brand: dict[str, Any] | str | Path | None = None,
    words: Any = None,
    model: str | None = None,
) -> tuple[str, AuditResult]:
    """Run `audit_layout` on `html`; on violations in real mode, do ONE LLM repair pass and re-audit.

    Raises `LayoutViolationError` if layout audit still fails (never silently ships cropped text).
    """
    audit = audit_layout(html, duration_s=duration_s)
    if audit.passed:
        return html, audit

    if is_test_mode():
        raise LayoutViolationError(audit.violations)

    repaired_html = repair_scene_html_with_llm(
        html,
        audit.violations,
        style=style,
        brand=brand,
        duration_s=duration_s,
        words=words,
        model=model,
    )
    second_audit = audit_layout(repaired_html, duration_s=duration_s)
    second_audit.repaired = True
    if not second_audit.passed:
        raise LayoutViolationError(second_audit.violations)
    return repaired_html, second_audit


def render_html_to_mp4(
    html_path: str | Path,
    out_mp4: str | Path,
    duration_s: float,
    fps: int = 30,
    width: int = CANVAS_WIDTH,
    height: int = CANVAS_HEIGHT,
) -> Path:
    """Render an HTML/SVG + GSAP scene to an H.264 MP4 via headless Chrome CDP frame-stepping."""
    src_html = Path(html_path)
    if not src_html.is_file():
        raise FileNotFoundError(f"HTML scene file not found: {src_html}")

    out_path = Path(out_mp4)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    chrome_bin = find_chrome_binary()
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        raise RuntimeError("ffmpeg binary not found on PATH.")

    fps_int = max(1, int(fps))
    total_frames = max(1, int(round(float(duration_s) * fps_int)))

    ffmpeg_cmd = [
        ffmpeg_bin,
        "-y",
        "-f",
        "image2pipe",
        "-vcodec",
        "png",
        "-r",
        str(fps_int),
        "-i",
        "-",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-vf",
        f"scale={int(width)}:{int(height)}",
        "-r",
        str(fps_int),
        "-frames:v",
        str(total_frames),
        "-movflags",
        "+faststart",
        str(out_path),
    ]

    ffmpeg_proc = subprocess.Popen(
        ffmpeg_cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    try:
        with _CDPPipeSession(chrome_bin, width=width, height=height) as cdp:
            cdp.open_page(src_html)
            for frame_idx in range(total_frames):
                t = frame_idx / float(fps_int)
                cdp.send(
                    "Runtime.evaluate",
                    {
                        "expression": f"window.__seek && window.__seek({t:.6f})",
                        "awaitPromise": False,
                    },
                )
                shot = cdp.send(
                    "Page.captureScreenshot",
                    {
                        "format": "png",
                        "clip": {
                            "x": 0,
                            "y": 0,
                            "width": int(width),
                            "height": int(height),
                            "scale": 1,
                        },
                    },
                )
                png_bytes = base64.b64decode(shot["data"])
                assert ffmpeg_proc.stdin is not None
                ffmpeg_proc.stdin.write(png_bytes)

        if ffmpeg_proc.stdin is not None:
            ffmpeg_proc.stdin.close()
            ffmpeg_proc.stdin = None
        _, stderr_bytes = ffmpeg_proc.communicate(timeout=60)
        if ffmpeg_proc.returncode != 0:
            err_msg = stderr_bytes.decode("utf-8", errors="replace")[-600:]
            raise RuntimeError(f"ffmpeg failed with exit code {ffmpeg_proc.returncode}: {err_msg}")
    finally:
        if ffmpeg_proc.stdin is not None:
            try:
                ffmpeg_proc.stdin.close()
            except OSError:
                pass
        if ffmpeg_proc.poll() is None:
            ffmpeg_proc.kill()
            ffmpeg_proc.wait()

    return out_path
