"""HTML/SVG + GSAP motion-graphics generator, layout auditor, and headless Chrome CDP renderer."""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import os
import re
import select
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

import yaml

from parallax import runlog, usage
from parallax.motion_graphics import BRAND_DEFAULT, CAPTION_TOP, SAFE_X
from parallax.motion_primitives import (
    BG_VARIANTS,
    CHAR_LIMITS,
    CHILD_CAPS,
    EMPHASIS_VERBS,
    ICON_REGISTRY,
    LAYOUT_SLOTS,
    MOTION_VERBS,
    PRIMITIVE_TYPES,
    SceneSpecValidationError,
    build_default_scene_spec,
    extract_embedded_scene_spec,
    load_scene_spec,
    parse_llm_scene_spec,
    render_scene_spec_html,
    save_scene_spec,
    validate_scene_spec,
)
from parallax.openrouter import client as openrouter_client
from parallax.shim import is_test_mode

logger = logging.getLogger(__name__)

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

# Per-token fallback rates for google/gemini-2.5-flash on OpenRouter ($0.30/M input, $2.50/M output)
_FLASH_PROMPT_COST_PER_TOKEN = 0.30 / 1_000_000
_FLASH_COMPLETION_COST_PER_TOKEN = 2.50 / 1_000_000
_FLASH_ESTIMATED_PROMPT_TOKENS = 6_000
_FLASH_ESTIMATED_COMPLETION_TOKENS = 6_000
DEFAULT_MOTION_GEN_ESTIMATE_USD = round(
    _FLASH_ESTIMATED_PROMPT_TOKENS * _FLASH_PROMPT_COST_PER_TOKEN
    + _FLASH_ESTIMATED_COMPLETION_TOKENS * _FLASH_COMPLETION_COST_PER_TOKEN,
    4,
)  # $0.0168 for ~6k prompt + ~6k completion tokens

MAX_LAYOUT_REPAIRS = 2


def resolve_max_layout_repairs(max_repairs: int | None = None) -> int:
    """Resolve the maximum number of LLM layout repair passes (default `MAX_LAYOUT_REPAIRS=2`)."""
    if max_repairs is not None:
        return max(0, int(max_repairs))
    env_val = os.environ.get("PARALLAX_MOTION_MAX_REPAIRS", "").strip()
    if env_val:
        try:
            return max(0, int(env_val))
        except ValueError:
            pass
    return MAX_LAYOUT_REPAIRS


def estimate_motion_cost_usd(
    max_repairs: int | None = None,
    *,
    include_repairs: bool = True,
) -> float:
    """Estimate realistic per-scene LLM cost (~6k prompt + ~6k completion tokens plus repair allowance)."""
    base_call = (
        _FLASH_ESTIMATED_PROMPT_TOKENS * _FLASH_PROMPT_COST_PER_TOKEN
        + _FLASH_ESTIMATED_COMPLETION_TOKENS * _FLASH_COMPLETION_COST_PER_TOKEN
    )
    if not include_repairs:
        return round(base_call, 4)
    repairs = resolve_max_layout_repairs(max_repairs)
    # Account for expected repair passes (~8% per allowed repair pass, keeping estimate in ~$0.005-$0.02)
    return round(base_call * (1.0 + 0.08 * min(repairs, 2)), 4)


class LayoutViolationError(RuntimeError):
    """Raised when an HTML motion graphic fails layout safety checks after repair."""

    def __init__(
        self,
        violations: list[dict[str, Any]],
        message: str | None = None,
        *,
        rejected_html_path: str | Path | None = None,
    ) -> None:
        self.violations = violations
        self.rejected_html_path = Path(rejected_html_path) if rejected_html_path is not None else None
        summary = "; ".join(
            f"[{v.get('type')} @ t={v.get('time_s')}s] {v.get('detail', '')}"
            for v in violations[:5]
        )
        base_msg = message or f"HTML motion layout failed audit ({len(violations)} violation(s)): {summary}"
        if self.rejected_html_path is not None and str(self.rejected_html_path) not in base_msg:
            base_msg = f"{base_msg} (rejected HTML saved at {self.rejected_html_path})"
        super().__init__(base_msg)


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
        data.setdefault("tokens", {})
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


def _parse_hex_rgb(hex_color: str, fallback: tuple[int, int, int] = (0, 0, 0)) -> tuple[int, int, int]:
    """Parse `#RGB` or `#RRGGBB` into `(r, g, b)` 0..255."""
    s = (hex_color or "").strip().lstrip("#")
    if len(s) == 3 and all(c in "0123456789abcdefABCDEF" for c in s):
        s = "".join(c * 2 for c in s)
    if len(s) >= 6 and all(c in "0123456789abcdefABCDEF" for c in s[:6]):
        return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    return fallback


def _relative_luminance(hex_color: str) -> float:
    """Compute WCAG 2.1 relative luminance for a hex color."""
    r, g, b = _parse_hex_rgb(hex_color)

    def _lin(channel_8bit: int) -> float:
        c = channel_8bit / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def wcag_contrast_ratio(fg_hex: str, bg_hex: str) -> float:
    """Return the WCAG 2.1 contrast ratio (1.0..21.0) between two hex colors."""
    l1 = _relative_luminance(fg_hex)
    l2 = _relative_luminance(bg_hex)
    bright, dark = (l1, l2) if l1 >= l2 else (l2, l1)
    return (bright + 0.05) / (dark + 0.05)


def pick_contrasting_text(
    preferred_hex: str,
    bg_hex: str,
    *,
    min_ratio: float = 4.5,
) -> str:
    """Return `preferred_hex` if its WCAG contrast against `bg_hex` is >= `min_ratio`,
    otherwise whichever of `(preferred_hex, '#FFFFFF', '#0A0A0A')` has the best contrast.
    """
    if wcag_contrast_ratio(preferred_hex, bg_hex) >= min_ratio:
        return preferred_hex
    candidates = (preferred_hex, "#FFFFFF", "#0A0A0A")
    return max(candidates, key=lambda c: wcag_contrast_ratio(c, bg_hex))


def load_brand_file(path: str | Path) -> dict[str, Any]:
    """Load a brand YAML or JSON file from disk.

    Relative `path` is resolved relative to current working directory, and a relative
    `brand.logo` path inside the file is resolved relative to the brand file's directory.
    """
    brand_path = Path(path).expanduser()
    if not brand_path.is_absolute():
        brand_path = Path.cwd() / brand_path
    if not brand_path.is_file():
        raise FileNotFoundError(f"Brand file not found: {brand_path}")
    brand_path = brand_path.resolve()
    raw = brand_path.read_text(encoding="utf-8")
    if brand_path.suffix.lower() == ".json":
        data = json.loads(raw)
    else:
        data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ValueError(f"Brand file {brand_path} must contain a YAML/JSON mapping (got {type(data).__name__}).")
    result = dict(data)
    result["_brand_dir"] = str(brand_path.parent)
    logo = result.get("logo")
    if isinstance(logo, str) and logo.strip() and not logo.strip().startswith("data:") and not logo.strip().startswith("<svg"):
        logo_p = Path(logo.strip()).expanduser()
        candidate = logo_p if logo_p.is_absolute() else (brand_path.parent / logo_p).resolve()
        if candidate.is_file():
            result["logo"] = str(candidate)
    return result


def _default_logo_svg_data_uri(brand_name: str, accent_hex: str, ink_hex: str) -> str:
    """Build a crisp self-contained SVG badge data URI when no external logo file is given."""
    label = (brand_name or "BRAND").strip()
    initials = "".join(part[0] for part in label.split()[:2] if part).upper() or "PX"
    badge_fg = pick_contrasting_text("#FFFFFF", accent_hex)
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 68 68" width="68" height="68">'
        f'<rect x="2" y="2" width="64" height="64" rx="16" fill="{accent_hex}"/>'
        f'<text x="34" y="44" text-anchor="middle" font-family="Inter, system-ui, sans-serif" '
        f'font-size="28" font-weight="800" fill="{badge_fg}">{initials}</text>'
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
    """Normalize a brand dict/file and merge its palette and fonts over a style preset.

    Performs WCAG 4.5 contrast-aware role mapping:
    - If `contrast(ink, bg) < 4.5`, picks whichever of `(brand_ink, '#FFFFFF', '#0A0A0A')`
      has the best contrast against `bg`.
    - Computes `on_accent`, `on_accent2`, `on_surface`, and `on_ink` using the same rule
      so button/pill/card text is always legible.
    """
    if isinstance(brand, (str, Path)):
        raw_brand = load_brand_file(brand)
    elif isinstance(brand, dict):
        raw_brand = dict(brand)
    else:
        raw_brand = {}

    if base_dir is None and isinstance(raw_brand.get("_brand_dir"), str):
        base_dir = Path(raw_brand["_brand_dir"])

    preset_palette = dict((preset or {}).get("palette") or {})
    palette: dict[str, str] = {
        "bg": preset_palette.get("bg", BRAND_DEFAULT.get("bg", "#14102A")),
        "surface": preset_palette.get("surface", BRAND_DEFAULT.get("bg2", "#FFFFFF")),
        "ink": preset_palette.get("ink", BRAND_DEFAULT.get("text", "#0B162C")),
        "accent": preset_palette.get("accent", BRAND_DEFAULT.get("accent", "#1C64F2")),
        "accent2": preset_palette.get("accent2", BRAND_DEFAULT.get("good", "#0E9F6E")),
    }

    explicit_surface = False
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
                if dst_key == "surface":
                    explicit_surface = True
        for role_key in ("bg", "surface", "ink", "accent", "accent2"):
            val = brand_palette.get(role_key)
            if isinstance(val, str) and val.strip():
                palette[role_key] = val.strip()
                if role_key == "surface":
                    explicit_surface = True
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
            if dst_key == "surface":
                explicit_surface = True

    brand_ink = palette["ink"]
    palette["brand_ink"] = brand_ink

    # If the preset enforces a dark canvas (e.g. glass-ui) and the brand supplied a light bg,
    # retain the preset's dark canvas and dark frosted surface while keeping brand_ink for BrandHeader's auto-plate.
    preset_tokens = dict((preset or {}).get("tokens") or {})
    if preset_tokens.get("force_dark_canvas") and _relative_luminance(palette["bg"]) > 0.35:
        palette["bg"] = preset_palette.get("bg", "#0A1628")
        palette["surface"] = preset_palette.get("surface", "#132347")
        explicit_surface = True

    # Contrast-aware role mapping: ensure ink on bg has WCAG contrast >= 4.5
    palette["ink"] = pick_contrasting_text(brand_ink, palette["bg"], min_ratio=4.5)

    # If the brand overrode bg/ink without specifying surface, and the preset's surface
    # clashes with the resolved ink (e.g. dark preset surface #1E293B with dark brand ink #001A4D),
    # align surface with the background polarity so cards using var(--surface) + var(--ink) stay readable.
    if not explicit_surface and wcag_contrast_ratio(palette["ink"], palette["surface"]) < 4.5:
        if _relative_luminance(palette["bg"]) > 0.4:
            palette["surface"] = "#F1F5F9" if palette["bg"].upper() == "#FFFFFF" else "#FFFFFF"
        else:
            palette["surface"] = "#1E293B"

    palette["on_bg"] = palette["ink"]
    palette["on_surface"] = pick_contrasting_text(palette["ink"], palette["surface"], min_ratio=4.5)
    palette["on_accent"] = pick_contrasting_text(brand_ink, palette["accent"], min_ratio=4.5)
    palette["on_accent2"] = pick_contrasting_text(brand_ink, palette["accent2"], min_ratio=4.5)
    palette["on_ink"] = pick_contrasting_text("#FFFFFF", palette["ink"], min_ratio=4.5)

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
    """Build the JavaScript snippet that defines `window.__TIMING` and `window.__fitText`."""
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
        "};\n"
        "window.__fitText = function(el, maxWidth, minFontSize) {\n"
        "  var maxW = (typeof maxWidth === 'number' && maxWidth > 0) ? maxWidth : 940;\n"
        "  var minSize = (typeof minFontSize === 'number' && minFontSize > 0) ? minFontSize : 16;\n"
        "  if (!el) return;\n"
        "  var targets = [];\n"
        "  if (typeof el === 'string') {\n"
        "    try { targets = Array.prototype.slice.call(document.querySelectorAll(el)); } catch (e) { return; }\n"
        "  } else if (el.nodeType === 1) {\n"
        "    targets = [el];\n"
        "  } else if (el.length !== undefined) {\n"
        "    targets = Array.prototype.slice.call(el);\n"
        "  }\n"
        "  for (var i = 0; i < targets.length; i++) {\n"
        "    var node = targets[i];\n"
        "    if (!node || node.nodeType !== 1) continue;\n"
        "    var limit = maxW;\n"
        "    if (node.clientWidth && node.clientWidth > 0 && node.clientWidth < limit) {\n"
        "      limit = node.clientWidth;\n"
        "    }\n"
        "    var size = parseFloat(window.getComputedStyle(node).fontSize) || 64;\n"
        "    while (size > minSize && (node.scrollWidth > limit + 1 || node.getBoundingClientRect().width > maxW + 1)) {\n"
        "      size = Math.max(minSize, Math.floor(size * 0.92));\n"
        "      node.style.fontSize = size + 'px';\n"
        "    }\n"
        "    if (size <= minSize && (node.scrollWidth > limit + 1 || node.getBoundingClientRect().width > maxW + 1)) {\n"
        "      node.style.whiteSpace = 'normal';\n"
        "    }\n"
        "  }\n"
        "};"
    )


_TIMING_SCRIPT_RE = re.compile(
    r'<script[^>]*id=["\']parallax-timing["\'][^>]*>.*?</script>',
    re.DOTALL | re.IGNORECASE,
)
_WINDOW_TIMING_RE = re.compile(
    r"window\.__TIMING\s*=\s*\{.*?\}\s*;(?:\s*window\.__fitText\s*=\s*function\s*\(.*?\}\s*;)?",
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
    """Build a deterministic `SceneSpec` from `scene_text` and render it via the primitives engine ($0, zero network)."""
    spec = build_default_scene_spec(
        scene_text,
        style,
        brand=norm_brand,
        duration_s=duration_s,
        words=words,
    )
    return render_scene_spec_html(
        spec,
        style=style,
        preset=preset,
        norm_brand=norm_brand,
        duration_s=duration_s,
        words=words,
    )


def build_motion_system_prompt(
    preset: dict[str, Any],
    norm_brand: dict[str, Any],
    duration_s: float = 5.0,
    words: Any = None,
) -> str:
    """Build the system prompt instructing `gemini-2.5-flash` to emit ONLY a JSON `SceneSpec`."""
    palette = norm_brand["palette"]
    fonts = norm_brand["fonts"]
    vocab = preset.get("motion_vocabulary") or {}
    do_list = "\n".join(f"  - DO: {item}" for item in (preset.get("do") or []))
    dont_list = "\n".join(f"  - DON'T: {item}" for item in (preset.get("dont") or []))
    icons_str = ", ".join(ICON_REGISTRY.keys())
    slots_str = ", ".join(LAYOUT_SLOTS)
    bg_str = ", ".join(BG_VARIANTS)
    verbs_str = ", ".join(MOTION_VERBS)
    emphasis_str = ", ".join(EMPHASIS_VERBS)

    return f"""You are an expert motion designer composing a vertical video scene (`1080x1920`) using the Parallax Motion Primitives Layer.
Instead of writing raw HTML/CSS/JS, you MUST return ONLY a valid JSON `SceneSpec` object. The Python host deterministically compiles your `SceneSpec` into self-contained `1080x1920` HTML5/SVG + GSAP with guaranteed horizontal centering (`x = 540`) and caption-safe bounds (`bottom <= 1320 < {CAPTION_TOP_PX:.0f}`).

1. STAGE GEOMETRY & HOST RUNTIME GUARANTEES:
- Viewport is `{CANVAS_WIDTH}x{CANVAS_HEIGHT}`. Root `<main id="safe-stage">` is locked at `left: {SAFE_LEFT}px`, `top: {SAFE_TOP}px`, `width: 940px` (`right = {SAFE_RIGHT}px`), `height: 1240px` (`bottom = 1320px < {CAPTION_TOP_PX:.0f}px` caption line, `CAPTION_TOP = {CAPTION_TOP}`).
- Maximum text width is `940px`; the host automatically invokes `window.__fitText(el, maxWidth, minFontSize)` and `window.__TIMING.cue(word, fallback)` on your primitives.
- The first headline or kinetic element is guaranteed visible (`opacity: 1`) by `t <= 0.6s`, and every element ends at `opacity: 1`.
- Never supply raw `x`, `y`, `left`, `top`, or CSS strings — choose layout slots, primitives, props, and motion verbs.

2. STYLE PRESET: {preset.get("name")} — {preset.get("description")}
Design Rules:
{preset.get("design_rules", "").strip()}
Motion Vocabulary:
- Eases: {", ".join(str(x) for x in (vocab.get("eases") or ["power3.out"]))}
- Durations: {json.dumps(vocab.get("durations") or "0.35s")}
Style Guidelines:
{do_list}
{dont_list}

3. BRAND PALETTE (WCAG >= 4.5 CONTRAST-VERIFIED) & FONTS:
- Brand Name: {norm_brand["name"]} (Logo is automatically injected via `#brand-logo` in `BrandHeader` with `logo_plate: "auto"` contrast pill protection).
- Palette Tokens:
  - `--bg: {palette["bg"]}`, `--on-bg: {palette.get("on_bg", palette["ink"])}`
  - `--surface: {palette["surface"]}`, `--on-surface: {palette.get("on_surface", palette["ink"])}`
  - `--ink: {palette["ink"]}`, `--on-ink: {palette.get("on_ink", "#FFFFFF")}`
  - `--accent: {palette["accent"]}`, `--on-accent: {palette.get("on_accent", "#FFFFFF")}`
  - `--accent2: {palette["accent2"]}`, `--on-accent2: {palette.get("on_accent2", "#FFFFFF")}`
- Fonts: heading `{fonts["heading"]}`, body `{fonts["body"]}`
- Scene Duration: {duration_s:.2f} seconds.

4. SCENESPEC JSON SCHEMA:
Return a single JSON object with these top-level keys:
{{
  "style": "{preset.get("name")}",
  "layout_slot": one of [{slots_str}],
  "gap": "sm" | "md" | "lg",
  "bg_variant": one of [{bg_str}],
  "primitives": [ ... 2 to {CHILD_CAPS["stage_primitives"][1]} primitive objects ... ]
}}

5. ALLOWED PRIMITIVES, PROPS & CHARACTER/CHILD LIMITS:
Each entry in `"primitives"` has `{{"type": "<PrimitiveName>", "props": {{...}}, "motion": {{"entrance": "<verb>", "emphasis": "<verb>", "cue": "<voiceover_word>"}}}}`:
- `BrandHeader`:
  - `props`: `{{"align": "center"|"split", "logo_plate": "auto"|"pill"|"none", "tag_text": "<=18 chars", "wordmark": "<=28 chars (optional)"}}`
  - Always include `BrandHeader` as the first primitive with `"logo_plate": "auto"`.
- `HeadlineBlock`:
  - `props`: `{{"eyebrow": "<=22 chars (optional)", "headline": "<=44 chars", "subhead": "<=110 chars (optional)", "size": "md"|"lg"|"hero", "align": "center"}}`
- `KineticWordStack`:
  - `props`: `{{"rows": [ {{"text": "<=20 chars (each word <=12 chars, UPPERCASE)", "style": "plain"|"pill-accent"|"pill-accent2"|"outline", "cue": "<word>"}}, ... ]}}` (2 to 4 rows; rendered at oversized >=110px font-size, never below 96px).
- `PhoneFrame`:
  - `props`: `{{"header_label": "<=20 chars", "bezel": "preset", "children": [ ... 1 to 3 non-container primitives (FeatureCard, PillBadge, IconBadge, StatCallout, ChecklistStack, CtaButton) ... ]}}`
- `LayoutSlot`:
  - `props`: `{{"direction": "column"|"row"|"grid-2col", "align": "center"|"stretch", "children": [ ... 1 to 4 non-container primitives ... ]}}`
- `FeatureCard`:
  - `props`: `{{"title": "<=36 chars", "body": "<=90 chars (optional)", "icon": "<icon_name>", "tone": "surface"|"accent"|"accent2", "tilt": "none"|"left"|"right"}}`
- `PillBadge`:
  - `props`: `{{"label": "<=22 chars", "icon": "<icon_name>", "tone": "accent"|"accent2"|"ink"|"surface", "pulse": true|false}}`
- `IconBadge`:
  - `props`: `{{"name": "<icon_name>", "size": "sm"|"md"|"lg", "tone": "accent"|"accent2"|"ink"|"surface", "draw_stroke": true|false}}`
- `StatCallout`:
  - `props`: `{{"value": "<=20 chars", "label": "<=32 chars", "delta": "<=18 chars (optional)", "meter": "none"|"bar"|"arc", "progress": 0.05..1.0}}`
- `ChecklistStack`:
  - `props`: `{{"items": [ {{"label": "<=38 chars", "state": "check"|"alert"|"bullet"|"x", "cue": "<word>"}}, ... ]}}` (1 to 4 items)
- `CtaButton`:
  - `props`: `{{"label": "<=24 chars", "icon": "<icon_name> (optional)", "tone": "ink"|"accent"|"accent2", "emphasis": "pulse"|"shake"|"none"}}`

6. ALLOWED INLINE SVG ICON NAMES (NEVER USE EMOJIS):
- `icon` / `name` MUST be one of: `{icons_str}`

7. ALLOWED MOTION VERBS & VOICEOVER CUES:
- `motion.entrance`: one of `{verbs_str}`
- `motion.emphasis`: one of `{emphasis_str}`
- `motion.cue`: lowercase single word from the voiceover `words` list (matched via `window.__TIMING.cue(word, fallback)`).

8. OUTPUT FORMAT:
- Output ONLY the raw JSON `SceneSpec` object. Do not wrap in markdown prose or emit HTML.
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
    json_mode: bool = True,
) -> dict[str, Any]:
    """Call OpenRouter `/chat/completions` via `parallax.openrouter.client._post` and record usage."""
    api_model = openrouter_client._strip_or_prefix(model_id)
    req_body: dict[str, Any] = {
        "model": api_model,
        "messages": messages,
        "temperature": temperature,
    }
    if json_mode:
        req_body["response_format"] = {"type": "json_object"}

    t0 = time.monotonic()
    raw_resp = openrouter_client._post("/chat/completions", req_body)
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


def _extract_choice_content(resp: dict[str, Any]) -> str:
    choices = resp.get("choices") or []
    if choices and isinstance(choices[0], dict):
        msg = choices[0].get("message") or {}
        return str(msg.get("content") or "")
    return ""


def _log_motion_warning(event_name: str, msg: str, **fields: Any) -> None:
    """Safely record a motion warning via `runlog.event` without failing on closed stdio streams."""
    try:
        runlog.event(event_name, level="WARN", msg=msg, **fields)
    except Exception:
        pass


def generate_scene_spec_and_html(
    scene_text: str,
    style: str,
    brand: dict[str, Any] | str | Path | None = None,
    duration_s: float = 5.0,
    words: Any = None,
    model: str | None = None,
) -> tuple[dict[str, Any], str]:
    """Generate a validated `SceneSpec` dict and its deterministically rendered 1080x1920 HTML.

    - Test mode (`PARALLAX_TEST_MODE=1`): builds a deterministic `SceneSpec` from `scene_text`
      and renders it through `render_scene_spec_html` with zero network calls ($0).
    - Real mode: calls OpenRouter `/chat/completions` (`response_format={"type": "json_object"}`).
      On invalid JSON or schema errors, runs 1 LLM repair pass with the validation error message,
      then falls back to the deterministic `SceneSpec` built from `scene_text` (logging a warning).
    """
    preset = load_style_preset(style)
    norm_brand = normalize_brand(brand, preset)
    norm_words = normalize_words(words)
    dur = max(0.2, float(duration_s or 5.0))

    if is_test_mode():
        spec = build_default_scene_spec(
            scene_text,
            preset["name"],
            brand=norm_brand,
            duration_s=dur,
            words=norm_words,
        )
        html = render_scene_spec_html(
            spec,
            style=preset["name"],
            preset=preset,
            norm_brand=norm_brand,
            duration_s=dur,
            words=norm_words,
        )
        return spec, html

    resolved_model = resolve_motion_model(model)
    system_prompt = build_motion_system_prompt(preset, norm_brand, dur, words=norm_words)
    user_payload = json.dumps(
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
                    "Generate the JSON SceneSpec for this vertical video scene:\n"
                    f"{user_payload}"
                ),
            },
        ],
        prompt_preview=scene_text or preset["name"],
        temperature=0.3,
        json_mode=True,
    )
    content = _extract_choice_content(resp)

    try:
        spec = parse_llm_scene_spec(content, style=preset["name"], scene_text=scene_text)
    except SceneSpecValidationError as first_err:
        _log_motion_warning(
            "motion.spec.repair_attempt",
            f"Initial LLM SceneSpec validation failed for style={preset['name']}: {first_err}",
            style=preset["name"],
            error=str(first_err),
        )
        repair_msg = (
            f"Your previous response failed SceneSpec JSON validation with error:\n{first_err}\n\n"
            "Return ONLY a corrected, valid JSON SceneSpec object with top-level keys "
            "`style`, `layout_slot`, `gap`, `bg_variant`, and `primitives` (containing `BrandHeader` "
            "plus 1-4 content primitives from the allowed catalog).\n\n"
            f"Previous invalid response:\n{content[:2000]}"
        )
        repair_resp = _call_openrouter_chat(
            model_id=resolved_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": repair_msg},
            ],
            prompt_preview=f"spec-repair:{preset['name']}",
            temperature=0.2,
            json_mode=True,
        )
        repair_content = _extract_choice_content(repair_resp)
        try:
            spec = parse_llm_scene_spec(repair_content, style=preset["name"], scene_text=scene_text)
        except SceneSpecValidationError as second_err:
            _log_motion_warning(
                "motion.spec.fallback",
                f"LLM SceneSpec repair also failed for style={preset['name']} ({second_err}); falling back to deterministic SceneSpec.",
                style=preset["name"],
                error=str(second_err),
            )
            spec = build_default_scene_spec(
                scene_text,
                preset["name"],
                brand=norm_brand,
                duration_s=dur,
                words=norm_words,
            )

    html = render_scene_spec_html(
        spec,
        style=preset["name"],
        preset=preset,
        norm_brand=norm_brand,
        duration_s=dur,
        words=norm_words,
    )
    return spec, html


def generate_scene_spec(
    scene_text: str,
    style: str,
    brand: dict[str, Any] | str | Path | None = None,
    duration_s: float = 5.0,
    words: Any = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Generate a validated `SceneSpec` JSON dict for `scene_text` and `style`."""
    spec, _ = generate_scene_spec_and_html(
        scene_text=scene_text,
        style=style,
        brand=brand,
        duration_s=duration_s,
        words=words,
        model=model,
    )
    return spec


def generate_scene_html(
    scene_text: str,
    style: str,
    brand: dict[str, Any] | str | Path | None = None,
    duration_s: float = 5.0,
    words: Any = None,
    model: str | None = None,
) -> str:
    """Generate a self-contained 1080x1920 HTML/SVG + GSAP motion graphic scene via `SceneSpec` primitives."""
    _, html = generate_scene_spec_and_html(
        scene_text=scene_text,
        style=style,
        brand=brand,
        duration_s=duration_s,
        words=words,
        model=model,
    )
    return html


def repair_scene_spec_with_llm(
    spec: dict[str, Any],
    violations: list[dict[str, Any]],
    *,
    style: str,
    brand: dict[str, Any] | str | Path | None = None,
    duration_s: float = 5.0,
    words: Any = None,
    model: str | None = None,
    scene_text: str = "",
) -> dict[str, Any]:
    """Run a single LLM repair pass on `spec` given the layout violation list from `audit_layout`."""
    preset = load_style_preset(style)
    norm_brand = normalize_brand(brand, preset)
    norm_words = normalize_words(words)
    dur = max(0.2, float(duration_s or 5.0))
    resolved_model = resolve_motion_model(model)
    system_prompt = build_motion_system_prompt(preset, norm_brand, dur, words=norm_words)

    js_errors = [
        str(v.get("detail") or v.get("text") or "")
        for v in violations
        if v.get("type") in ("js_error", "js_exception")
    ]
    js_error_section = ""
    if js_errors:
        joined_js_errors = "\n".join(f"- {err}" for err in js_errors if err)
        js_error_section = (
            f"JavaScript Errors Thrown During Execution (MUST FIX):\n{joined_js_errors}\n\n"
        )

    repair_prompt = (
        "The rendered SceneSpec below failed the layout safety audit with the following violations:\n"
        f"{json.dumps(violations, indent=2)}\n\n"
        f"{js_error_section}"
        "Repair the JSON SceneSpec so that:\n"
        f"1. Every element stays strictly inside safe bounds: left >= {SAFE_LEFT}, right <= {SAFE_RIGHT}, top >= {SAFE_TOP}, bottom <= {CAPTION_TOP_PX:.0f} (max width 940px).\n"
        "2. Shorten any long headlines/labels/rows or reduce the number of stacked primitives/children so nothing overflows or clips.\n"
        "3. Ensure at least one primary content primitive (`HeadlineBlock`, `KineticWordStack`, `FeatureCard`, etc.) is present and visible across 50%, 75%, and 100% of the timeline.\n"
        "Return ONLY the corrected JSON `SceneSpec` object.\n\n"
        f"Current SceneSpec:\n{json.dumps(spec, indent=2, ensure_ascii=False)}"
    )

    resp = _call_openrouter_chat(
        model_id=resolved_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": repair_prompt},
        ],
        prompt_preview=f"repair:{preset['name']}",
        temperature=0.2,
        json_mode=True,
    )
    content = _extract_choice_content(resp)
    if not content.strip():
        raise RuntimeError(f"OpenRouter repair pass returned empty content for model {resolved_model!r}")

    try:
        return parse_llm_scene_spec(content, style=preset["name"], scene_text=scene_text)
    except SceneSpecValidationError as err:
        _log_motion_warning(
            "motion.spec.layout_repair_invalid",
            f"LLM layout-repair SceneSpec invalid for style={preset['name']}: {err}",
            style=preset["name"],
            error=str(err),
        )
        raise


def repair_scene_html_with_llm(
    html: str,
    violations: list[dict[str, Any]],
    *,
    style: str,
    brand: dict[str, Any] | str | Path | None = None,
    duration_s: float = 5.0,
    words: Any = None,
    model: str | None = None,
    spec: dict[str, Any] | None = None,
) -> str:
    """Repair a scene using `SceneSpec` JSON repair and re-render via `render_scene_spec_html`."""
    preset = load_style_preset(style)
    norm_brand = normalize_brand(brand, preset)
    norm_words = normalize_words(words)
    dur = max(0.2, float(duration_s or 5.0))

    current_spec = spec or extract_embedded_scene_spec(html)
    if current_spec is None:
        current_spec = build_default_scene_spec(
            "",
            preset["name"],
            brand=norm_brand,
            duration_s=dur,
            words=norm_words,
        )

    try:
        repaired_spec = repair_scene_spec_with_llm(
            current_spec,
            violations,
            style=preset["name"],
            brand=norm_brand,
            duration_s=dur,
            words=norm_words,
            model=model,
        )
    except SceneSpecValidationError:
        return html

    return render_scene_spec_html(
        repaired_spec,
        style=preset["name"],
        preset=preset,
        norm_brand=norm_brand,
        duration_s=dur,
        words=norm_words,
    )


def _format_cdp_exception_details(details: dict[str, Any], time_s: float = 0.0) -> dict[str, Any]:
    """Convert a CDP `exceptionDetails` payload into a violation dict."""
    exc = details.get("exception") if isinstance(details.get("exception"), dict) else {}
    desc = str(
        exc.get("description")
        or exc.get("value")
        or details.get("text")
        or "Uncaught JS exception"
    ).strip()
    text = str(details.get("text") or "Uncaught").strip()
    line_no = details.get("lineNumber")
    col_no = details.get("columnNumber")
    loc = f" (line {line_no}:{col_no})" if line_no is not None else ""
    detail_msg = (
        f"Runtime.exceptionThrown: {text}: {desc}{loc}"
        if text and not desc.startswith(text)
        else f"Runtime.exceptionThrown: {desc}{loc}"
    )
    return {
        "type": "js_error",
        "source": "Runtime.exceptionThrown",
        "time_s": time_s,
        "text": desc,
        "detail": detail_msg,
    }


def _chrome_stray_Watch_roots() -> tuple[Path, ...]:
    """Return directories where headless Chrome may drop `scoped_dir*` or `com.google.Chrome.*` folders."""
    roots: list[Path] = []
    headless_cfg = Path.home() / ".config" / "google-chrome-headless"
    if headless_cfg.is_dir():
        roots.append(headless_cfg)
    tmp_root = Path(tempfile.gettempdir())
    if tmp_root.is_dir() and tmp_root not in roots:
        roots.append(tmp_root)
    return tuple(roots)


def _iter_chrome_stray_candidates() -> list[Path]:
    """List existing `scoped_dir*`, `com.google.Chrome.*`, and `.org.chromium.Chromium.*` dirs in watch roots."""
    found: list[Path] = []
    prefixes = ("scoped_dir", "com.google.Chrome.", ".org.chromium.Chromium.")
    for root in _chrome_stray_Watch_roots():
        try:
            for entry in root.iterdir():
                if entry.name.startswith(prefixes):
                    found.append(entry)
        except OSError:
            continue
    return found


def _cleanup_chrome_stray_dirs(
    start_time: float,
    pre_existing: set[Path] | None = None,
) -> None:
    """Remove stray Chrome temp dirs created since `start_time` and owned by the current UID."""
    uid = os.getuid()
    known = pre_existing or set()
    for candidate in _iter_chrome_stray_candidates():
        if candidate in known:
            continue
        try:
            if candidate.is_symlink() or not candidate.is_dir():
                continue
            st = candidate.stat()
            if st.st_uid != uid:
                continue
            if st.st_mtime < start_time - 0.5:
                continue
            shutil.rmtree(candidate, ignore_errors=True)
        except OSError:
            continue


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
        self._user_data_dir: str | None = None
        self._start_time: float = 0.0
        self._pre_existing_stray_dirs: set[Path] = set()
        self.js_errors: list[dict[str, Any]] = []

    def __enter__(self) -> _CDPPipeSession:
        self._start_time = time.time()
        self._pre_existing_stray_dirs = set(_iter_chrome_stray_candidates())
        self._user_data_dir = tempfile.mkdtemp(prefix="parallax-chrome-")
        c2b_r: int | None = None
        b2c_w: int | None = None
        try:
            c2b_r, c2b_w = os.pipe()
            b2c_r, b2c_w = os.pipe()
            self._c2b_w = c2b_w
            self._b2c_r = b2c_r

            def _setup_child_fds() -> None:
                assert c2b_r is not None and b2c_w is not None
                try:
                    os.setsid()
                except OSError:
                    pass
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
                "--no-first-run",
                "--no-default-browser-check",
                f"--user-data-dir={self._user_data_dir}",
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
            c2b_r = None
            os.close(b2c_w)
            b2c_w = None
            return self
        except Exception:
            for extra_fd in (c2b_r, b2c_w):
                if extra_fd is not None:
                    try:
                        os.close(extra_fd)
                    except OSError:
                        pass
            self._close_and_cleanup()
            raise

    def _close_and_cleanup(self) -> None:
        try:
            for fd in (self._c2b_w, self._b2c_r):
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
            self._c2b_w = None
            self._b2c_r = None
            if self._proc is not None:
                pid = self._proc.pid
                if self._proc.poll() is None:
                    self._proc.terminate()
                    try:
                        os.killpg(pid, signal.SIGTERM)
                    except OSError:
                        pass
                    try:
                        self._proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self._proc.kill()
                        try:
                            os.killpg(pid, signal.SIGKILL)
                        except OSError:
                            pass
                        self._proc.wait()
                try:
                    os.killpg(pid, signal.SIGKILL)
                except OSError:
                    pass
                self._proc = None
        finally:
            if self._user_data_dir is not None:
                udir = Path(self._user_data_dir)
                for _ in range(5):
                    shutil.rmtree(udir, ignore_errors=True)
                    if not udir.exists():
                        break
                    time.sleep(0.05)
                self._user_data_dir = None
            if self._start_time > 0:
                _cleanup_chrome_stray_dirs(self._start_time, self._pre_existing_stray_dirs)

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self._close_and_cleanup()

    def _record_event(self, msg: dict[str, Any]) -> None:
        method = msg.get("method")
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        if method == "Runtime.exceptionThrown":
            details = params.get("exceptionDetails")
            if isinstance(details, dict):
                self.js_errors.append(_format_cdp_exception_details(details, time_s=0.0))
        elif method == "Runtime.consoleAPICalled":
            call_type = str(params.get("type") or "").lower()
            if call_type in ("error", "assert"):
                args = params.get("args") or []
                rendered: list[str] = []
                for arg in args:
                    if isinstance(arg, dict):
                        val = arg.get("description") or arg.get("value") or arg.get("unserializableValue")
                        if val is not None:
                            rendered.append(str(val).strip())
                err_text = " ".join(s for s in rendered if s).strip() or f"console.{call_type}"
                self.js_errors.append(
                    {
                        "type": "js_error",
                        "source": "Runtime.consoleAPICalled",
                        "time_s": 0.0,
                        "text": err_text,
                        "detail": f"Runtime.consoleAPICalled ({call_type}): {err_text}",
                    }
                )

    def drain_events(self) -> None:
        """Non-blockingly read and record any pending CDP events from the pipe."""
        if self._b2c_r is None:
            return
        while True:
            ready, _, _ = select.select([self._b2c_r], [], [], 0.0)
            if not ready:
                break
            chunk = os.read(self._b2c_r, 65536)
            if not chunk:
                break
            self._buf += chunk
        while b"\x00" in self._buf:
            part, self._buf = self._buf.split(b"\x00", 1)
            if not part:
                continue
            msg = json.loads(part.decode("utf-8"))
            if "method" in msg:
                self._record_event(msg)

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
                if "method" in msg:
                    self._record_event(msg)
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

    def _open_page_once(self, html_path: Path) -> None:
        page_target_id: str | None = None
        for _ in range(30):
            targets = self.send("Target.getTargets", session_id="", timeout=10.0).get("targetInfos") or []
            for t in targets:
                if t.get("type") == "page":
                    page_target_id = t.get("targetId")
                    break
            if page_target_id:
                break
            time.sleep(0.05)
        if not page_target_id:
            page_target_id = self.send(
                "Target.createTarget", {"url": "about:blank"}, session_id="", timeout=10.0
            ).get("targetId")
        attached = self.send(
            "Target.attachToTarget",
            {"targetId": page_target_id, "flatten": True},
            session_id="",
            timeout=10.0,
        )
        self._session_id = attached["sessionId"]
        self.send("Page.enable", timeout=10.0)
        self.send("Runtime.enable", timeout=10.0)
        self.send(
            "Emulation.setDeviceMetricsOverride",
            {
                "width": self.width,
                "height": self.height,
                "deviceScaleFactor": 1,
                "mobile": False,
            },
            timeout=10.0,
        )
        file_url = html_path.resolve().as_uri()
        self.send("Page.navigate", {"url": file_url}, timeout=15.0)
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
            timeout=15.0,
        )
        self.drain_events()

    def open_page(self, html_path: Path) -> None:
        """Attach to the browser's page target, set viewport metrics, and navigate to `html_path`."""
        try:
            self._open_page_once(html_path)
        except (TimeoutError, RuntimeError, OSError):
            self._close_and_cleanup()
            self._buf = b""
            self._msg_id = 0
            self._session_id = None
            self.js_errors.clear()
            self.__enter__()
            self._open_page_once(html_path)


_AUDIT_JS_TEMPLATE = """
(() => {
  const t = __SAMPLE_TIME__;
  const REQUIRE_VISIBLE_TEXT = __REQUIRE_VISIBLE_TEXT__;
  const violations = [];
  if (typeof window.__seek === 'function') {
    try {
      window.__seek(t);
    } catch (err) {
      violations.push({
        type: 'js_error',
        source: 'window.__seek',
        time_s: t,
        text: String((err && err.message) || err),
        detail: `Uncaught error in window.__seek(${t}): ${String((err && err.stack) || err)}`
      });
    }
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
  let visibleOnScreenCount = 0;

  for (const el of all) {
    if (!hasDirectText(el)) continue;
    if (!isElementVisible(el)) continue;
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0.5 || rect.height <= 0.5) continue;

    const textSample = (el.textContent || '').trim().slice(0, 60);
    const isSvg = el instanceof SVGElement;

    if (rect.right > 0 && rect.left < W && rect.bottom > 0 && rect.top < H) {
      visibleOnScreenCount += 1;
    }

    // 1. Internal overflow / clipping check (scrollWidth > clientWidth)
    const CLIP_EPS = 1.0;
    let clippedInternal = false;
    if (!isSvg && el.clientWidth > 0 && el.scrollWidth > el.clientWidth + CLIP_EPS) {
      clippedInternal = true;
      violations.push({
        type: 'text_clipped',
        time_s: t,
        text: textSample,
        detail: `scrollWidth (${el.scrollWidth}) > clientWidth (${el.clientWidth}) for "${textSample}"`,
        rect: { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom }
      });
    }
    if (!isSvg && el.clientHeight > 0 && el.scrollHeight > el.clientHeight + CLIP_EPS) {
      const st = window.getComputedStyle(el);
      if (st.overflowY === 'hidden' || st.overflowY === 'clip' || st.overflow === 'hidden' || st.overflow === 'clip') {
        clippedInternal = true;
        violations.push({
          type: 'text_clipped',
          time_s: t,
          text: textSample,
          detail: `scrollHeight (${el.scrollHeight}) > clientHeight (${el.clientHeight}) with overflow hidden for "${textSample}"`,
          rect: { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom }
        });
      }
    }

    // Measure actual text glyph range rect in case el or an ancestor clips text
    let textRect = rect;
    if (!isSvg && typeof document.createRange === 'function') {
      try {
        const range = document.createRange();
        range.selectNodeContents(el);
        const rRect = range.getBoundingClientRect();
        if (rRect && rRect.width > 0.5 && rRect.height > 0.5) {
          textRect = rRect;
        }
      } catch (e) {}
    }

    // Check if el itself or an ancestor with overflow:hidden/clip crops this text
    let clipTarget = el;
    while (clipTarget && clipTarget !== document.body && clipTarget !== document.documentElement) {
      const pst = window.getComputedStyle(clipTarget);
      const clipsX = (pst.overflow === 'hidden' || pst.overflow === 'clip' || pst.overflowX === 'hidden' || pst.overflowX === 'clip');
      const clipsY = (pst.overflow === 'hidden' || pst.overflow === 'clip' || pst.overflowY === 'hidden' || pst.overflowY === 'clip');
      if (clipsX || clipsY) {
        const prect = clipTarget.getBoundingClientRect();
        const overX = clipsX && (
          rect.left < prect.left - CLIP_EPS ||
          rect.right > prect.right + CLIP_EPS ||
          textRect.left < prect.left - CLIP_EPS ||
          textRect.right > prect.right + CLIP_EPS
        );
        const overY = clipsY && (
          rect.top < prect.top - CLIP_EPS ||
          rect.bottom > prect.bottom + CLIP_EPS ||
          textRect.top < prect.top - CLIP_EPS ||
          textRect.bottom > prect.bottom + CLIP_EPS
        );
        if (overX || overY) {
          if (!(clipTarget === el && clippedInternal)) {
            violations.push({
              type: clipTarget === el ? 'text_clipped' : 'text_clipped_by_container',
              time_s: t,
              text: textSample,
              detail: `Text "${textSample}" extends outside clipping container (${Math.round(Math.min(rect.left, textRect.left))}..${Math.round(Math.max(rect.right, textRect.right))} vs ${Math.round(prect.left)}..${Math.round(prect.right)})`,
              rect: { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom }
            });
          }
          break;
        }
      }
      clipTarget = clipTarget.parentElement;
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

  // 6. Blank frame / no visible text check at 50%/75%/100% samples
  if (REQUIRE_VISIBLE_TEXT && visibleOnScreenCount === 0) {
    violations.push({
      type: 'no_visible_text',
      kind: 'blank_frame',
      blank_frame: true,
      time_s: t,
      text: '',
      detail: `blank_frame / no_visible_text: no visible text element with opacity > 0.05 on screen at t=${t}s`
    });
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
    frac_list = [float(frac) for frac in sample_fractions]
    sample_times = [round(dur * frac, 4) for frac in frac_list]

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
            for frac, t in zip(frac_list, sample_times):
                require_visible = "true" if frac >= 0.5 - 1e-6 else "false"
                expr = (
                    _AUDIT_JS_TEMPLATE.replace("__SAMPLE_TIME__", repr(float(t)))
                    .replace("__REQUIRE_VISIBLE_TEXT__", require_visible)
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
                if isinstance(res.get("exceptionDetails"), dict):
                    all_violations.append(_format_cdp_exception_details(res["exceptionDetails"], time_s=t))
                value = (res.get("result") or {}).get("value")
                if isinstance(value, list):
                    all_violations.extend(value)
            cdp.drain_events()
            if cdp.js_errors:
                all_violations = list(cdp.js_errors) + all_violations

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


def _save_rejected_html(
    html: str,
    out_path: str | Path | None = None,
    *,
    rejected_html_path: str | Path | None = None,
    style: str = "scene",
) -> Path:
    """Save the rejected HTML next to the output as `<name>.rejected.html`."""
    if rejected_html_path is not None:
        p = Path(rejected_html_path)
        target = p if p.name.endswith(".rejected.html") else p.with_suffix(".rejected.html")
    elif out_path is not None:
        p = Path(out_path)
        target = p if p.name.endswith(".rejected.html") else p.with_suffix(".rejected.html")
    else:
        safe_style = re.sub(r"[^a-zA-Z0-9_-]+", "-", style or "scene").strip("-") or "scene"
        target = Path(tempfile.gettempdir()) / f"{safe_style}.rejected.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html, encoding="utf-8")
    return target


def audit_and_repair_scene(
    spec: dict[str, Any],
    html: str,
    *,
    duration_s: float = 5.0,
    style: str = "flat-explainer",
    brand: dict[str, Any] | str | Path | None = None,
    words: Any = None,
    model: str | None = None,
    max_repairs: int | None = None,
    out_path: str | Path | None = None,
    rejected_html_path: str | Path | None = None,
    scene_text: str = "",
) -> tuple[dict[str, Any], str, AuditResult]:
    """Run `audit_layout` on `html`; on violations in real mode, repair the `SceneSpec` via LLM,
    re-render HTML, and re-audit up to `MAX_LAYOUT_REPAIRS` passes.
    """
    current_spec = spec
    current_html = html
    audit = audit_layout(current_html, duration_s=duration_s)
    if audit.passed:
        return current_spec, current_html, audit

    if is_test_mode():
        rejected = _save_rejected_html(
            current_html,
            out_path=out_path,
            rejected_html_path=rejected_html_path,
            style=style,
        )
        raise LayoutViolationError(audit.violations, rejected_html_path=rejected)

    preset = load_style_preset(style)
    norm_brand = normalize_brand(brand, preset)
    norm_words = normalize_words(words)
    dur = max(0.2, float(duration_s or 5.0))
    repair_limit = resolve_max_layout_repairs(max_repairs)

    for _attempt in range(repair_limit):
        try:
            current_spec = repair_scene_spec_with_llm(
                current_spec,
                audit.violations,
                style=preset["name"],
                brand=norm_brand,
                duration_s=dur,
                words=norm_words,
                model=model,
                scene_text=scene_text,
            )
            current_html = render_scene_spec_html(
                current_spec,
                style=preset["name"],
                preset=preset,
                norm_brand=norm_brand,
                duration_s=dur,
                words=norm_words,
            )
        except SceneSpecValidationError:
            pass
        audit = audit_layout(current_html, duration_s=duration_s)
        audit.repaired = True
        if audit.passed:
            return current_spec, current_html, audit

    rejected = _save_rejected_html(
        current_html,
        out_path=out_path,
        rejected_html_path=rejected_html_path,
        style=style,
    )
    raise LayoutViolationError(audit.violations, rejected_html_path=rejected)


def audit_and_repair_html(
    html: str,
    *,
    duration_s: float = 5.0,
    style: str = "flat-explainer",
    brand: dict[str, Any] | str | Path | None = None,
    words: Any = None,
    model: str | None = None,
    max_repairs: int | None = None,
    out_path: str | Path | None = None,
    rejected_html_path: str | Path | None = None,
    spec: dict[str, Any] | None = None,
) -> tuple[str, AuditResult]:
    """Run `audit_layout` on `html`; on violations in real mode, run up to `MAX_LAYOUT_REPAIRS`
    (default 2, overridable via `PARALLAX_MOTION_MAX_REPAIRS`) LLM spec-repair passes and re-audit.

    On final failure, saves the last HTML next to the output as `<name>.rejected.html`
    and raises `LayoutViolationError` with that path in the error message.
    """
    current_html = html
    audit = audit_layout(current_html, duration_s=duration_s)
    if audit.passed:
        return current_html, audit

    if is_test_mode():
        rejected = _save_rejected_html(
            current_html,
            out_path=out_path,
            rejected_html_path=rejected_html_path,
            style=style,
        )
        raise LayoutViolationError(audit.violations, rejected_html_path=rejected)

    current_spec = spec or extract_embedded_scene_spec(current_html)
    repair_limit = resolve_max_layout_repairs(max_repairs)
    for _attempt in range(repair_limit):
        current_html = repair_scene_html_with_llm(
            current_html,
            audit.violations,
            style=style,
            brand=brand,
            duration_s=duration_s,
            words=words,
            model=model,
            spec=current_spec,
        )
        current_spec = extract_embedded_scene_spec(current_html) or current_spec
        audit = audit_layout(current_html, duration_s=duration_s)
        audit.repaired = True
        if audit.passed:
            return current_html, audit

    rejected = _save_rejected_html(
        current_html,
        out_path=out_path,
        rejected_html_path=rejected_html_path,
        style=style,
    )
    raise LayoutViolationError(audit.violations, rejected_html_path=rejected)


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
