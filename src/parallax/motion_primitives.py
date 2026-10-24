"""Schema-constrained motion-graphic primitives, SceneSpec validator, and deterministic HTML/SVG/GSAP renderer."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

from parallax.motion_graphics import CAPTION_TOP

CANVAS_WIDTH = 1080
CANVAS_HEIGHT = 1920
STAGE_LEFT = 70
STAGE_TOP = 80
STAGE_WIDTH = 940
STAGE_HEIGHT = 1240
STAGE_RIGHT = STAGE_LEFT + STAGE_WIDTH  # 1010
STAGE_BOTTOM = STAGE_TOP + STAGE_HEIGHT  # 1320 (< 1344 caption line)
STAGE_CENTER_X = STAGE_LEFT + STAGE_WIDTH // 2  # 540
CAPTION_TOP_PX = CAPTION_TOP * CANVAS_HEIGHT  # 1344.0

_PACKAGE_DIR = Path(__file__).resolve().parent
MOTION_STYLES_DIR = _PACKAGE_DIR / "motion_styles"
SPECS_STYLES_DIR = MOTION_STYLES_DIR / "specs"

LAYOUT_SLOTS: tuple[str, ...] = (
    "center-stack",
    "phone-hero",
    "kinetic-rows",
    "editorial-grid",
)

BG_VARIANTS: tuple[str, ...] = (
    "solid",
    "radial",
    "grid",
    "orb-glow",
)

GAP_TOKENS: dict[str, str] = {
    "sm": "26px",
    "md": "34px",
    "lg": "44px",
}

MOTION_VERBS: tuple[str, ...] = (
    "fade-up",
    "pop",
    "slide-up",
    "stamp",
    "wipe",
    "draw",
)

EMPHASIS_VERBS: tuple[str, ...] = (
    "pulse",
    "shake",
    "none",
)

_ENTRANCE_VERB_ALIASES: dict[str, str] = {
    "fade-up": "fade-up",
    "frosted-fade-up": "fade-up",
    "metric-fade-up": "fade-up",
    "ambient-float": "fade-up",
    "fade": "fade-up",
    "pop": "pop",
    "scale-pop-badge": "pop",
    "word-by-word-pop": "pop",
    "scale-punch": "pop",
    "glow-scale-in": "pop",
    "sticker-tilt-pop": "pop",
    "cta-pill-pulse": "pop",
    "slide-up": "slide-up",
    "slide-up-card": "slide-up",
    "stepped-slide-up": "slide-up",
    "slide-up-stagger": "slide-up",
    "stamp": "stamp",
    "stamp-slam": "stamp",
    "alert-shake": "stamp",
    "wipe": "wipe",
    "pill-highlight-wipe": "wipe",
    "masked-line-reveal": "wipe",
    "hairline-rule-grow": "wipe",
    "timeline-bar-fill": "wipe",
    "draw": "draw",
    "svg-stroke-draw": "draw",
    "arc-gauge-sweep": "draw",
}

PRIMITIVE_TYPES: tuple[str, ...] = (
    "StageSafeZone",
    "BrandHeader",
    "LayoutSlot",
    "HeadlineBlock",
    "KineticWordStack",
    "PhoneFrame",
    "FeatureCard",
    "PillBadge",
    "IconBadge",
    "StatCallout",
    "ChecklistStack",
    "CtaButton",
)

# Fixed inline SVG icon registry (all paths carry class="svg-draw-path" for stroke-draw tweens)
ICON_REGISTRY: dict[str, str] = {
    "shield": (
        '<path class="svg-draw-path" d="M32 6L54 15V31C54 46.5 44.5 55.8 32 60C19.5 55.8 10 46.5 10 31V15L32 6Z" '
        'stroke="currentColor" stroke-width="4.5" stroke-linejoin="round" fill="none"/>'
        '<path class="svg-draw-path" d="M23 32.5L29.5 39L42 25.5" '
        'stroke="currentColor" stroke-width="5" stroke-linecap="round" stroke-linejoin="round" fill="none"/>'
    ),
    "check": (
        '<circle class="svg-draw-path" cx="32" cy="32" r="24" stroke="currentColor" stroke-width="4.5" fill="none"/>'
        '<path class="svg-draw-path" d="M21 32.5L28.5 40L43.5 24.5" '
        'stroke="currentColor" stroke-width="5" stroke-linecap="round" stroke-linejoin="round" fill="none"/>'
    ),
    "phone-call": (
        '<path class="svg-draw-path" d="M20 12H27L31 23L25.5 27C28.2 32.8 31.2 35.8 37 38.5L41 33L52 37V44C52 48.4 48.4 52 44 52C25.2 50.8 13.2 38.8 12 20C12 15.6 15.6 12 20 12Z" '
        'stroke="currentColor" stroke-width="4.5" stroke-linejoin="round" fill="none"/>'
        '<path class="svg-draw-path" d="M38 14C44.6 15.2 48.8 19.4 50 26" '
        'stroke="currentColor" stroke-width="4.5" stroke-linecap="round" fill="none"/>'
    ),
    "alert": (
        '<path class="svg-draw-path" d="M32 9L56 51H8L32 9Z" '
        'stroke="currentColor" stroke-width="4.5" stroke-linejoin="round" fill="none"/>'
        '<path class="svg-draw-path" d="M32 25V37" stroke="currentColor" stroke-width="5" stroke-linecap="round" fill="none"/>'
        '<circle cx="32" cy="44.5" r="2.8" fill="currentColor"/>'
    ),
    "lock": (
        '<rect class="svg-draw-path" x="14" y="28" width="36" height="28" rx="7" '
        'stroke="currentColor" stroke-width="4.5" fill="none"/>'
        '<path class="svg-draw-path" d="M22 28V20C22 14.5 26.5 10 32 10C37.5 10 42 14.5 42 20V28" '
        'stroke="currentColor" stroke-width="4.5" stroke-linecap="round" fill="none"/>'
        '<circle cx="32" cy="42" r="3.5" fill="currentColor"/>'
    ),
    "scan": (
        '<path class="svg-draw-path" d="M12 22V14C12 12.9 12.9 12 14 12H22M42 12H50C51.1 12 52 12.9 52 14V22M52 42V50C52 51.1 51.1 52 50 52H42M22 52H14C12.9 52 12 51.1 12 50V42" '
        'stroke="currentColor" stroke-width="4.5" stroke-linecap="round" fill="none"/>'
        '<line class="svg-draw-path" x1="10" y1="32" x2="54" y2="32" '
        'stroke="currentColor" stroke-width="4.5" stroke-linecap="round"/>'
    ),
    "spark": (
        '<path class="svg-draw-path" d="M32 8L37.5 26.5L56 32L37.5 37.5L32 56L26.5 37.5L8 32L26.5 26.5L32 8Z" '
        'stroke="currentColor" stroke-width="4.5" stroke-linejoin="round" fill="none"/>'
    ),
    "chart": (
        '<path class="svg-draw-path" d="M12 52H52" stroke="currentColor" stroke-width="4.5" stroke-linecap="round"/>'
        '<rect class="svg-draw-path" x="16" y="34" width="8" height="18" rx="2" stroke="currentColor" stroke-width="4" fill="none"/>'
        '<rect class="svg-draw-path" x="28" y="24" width="8" height="28" rx="2" stroke="currentColor" stroke-width="4" fill="none"/>'
        '<rect class="svg-draw-path" x="40" y="14" width="8" height="38" rx="2" stroke="currentColor" stroke-width="4" fill="none"/>'
    ),
    "x": (
        '<circle class="svg-draw-path" cx="32" cy="32" r="24" stroke="currentColor" stroke-width="4.5" fill="none"/>'
        '<path class="svg-draw-path" d="M23 23L41 41M41 23L23 41" '
        'stroke="currentColor" stroke-width="5" stroke-linecap="round" fill="none"/>'
    ),
}

CHAR_LIMITS: dict[str, dict[str, int]] = {
    "BrandHeader": {"tag_text": 18, "wordmark": 28},
    "HeadlineBlock": {"eyebrow": 22, "headline": 44, "subhead": 110},
    "KineticWordStack": {"row_text": 20, "max_single_word": 12},
    "PhoneFrame": {"header_label": 20},
    "FeatureCard": {"title": 36, "body": 90},
    "PillBadge": {"label": 22},
    "StatCallout": {"value": 20, "label": 32, "delta": 18},
    "ChecklistStack": {"item_label": 38},
    "CtaButton": {"label": 24},
}

CHILD_CAPS: dict[str, tuple[int, int]] = {
    "stage_primitives": (1, 5),
    "LayoutSlot": (1, 4),
    "PhoneFrame": (1, 4),
    "KineticWordStack": (2, 4),
    "ChecklistStack": (1, 4),
}


class SceneSpecValidationError(ValueError):
    """Raised when an LLM SceneSpec payload is not valid JSON or fails structural schema checks."""


def _escape_html(text: str) -> str:
    return (
        str(text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


_TRUNCATED_WORD_FIXES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bLikel$", re.IGNORECASE), "Likely"),
    (re.compile(r"\bLik$", re.IGNORECASE), "Likely"),
    (re.compile(r"\bBlocke$", re.IGNORECASE), "Blocked"),
    (re.compile(r"\bProtecte$", re.IGNORECASE), "Protected"),
    (re.compile(r"\bDetecte$", re.IGNORECASE), "Detected"),
)


def _repair_truncated_words(text: str) -> str:
    out = text
    for pattern, repl in _TRUNCATED_WORD_FIXES:
        out = pattern.sub(
            lambda m: repl.upper() if m.group(0).isupper() else repl,
            out,
        )
    return out


def _clamp_str(val: Any, max_len: int, default: str = "") -> str:
    s = " ".join(str(val if val is not None else default).strip().split())
    s = _repair_truncated_words(s)
    if not s:
        return default
    if len(s) <= max_len:
        return s
    if max_len <= 3:
        return s[:max_len]
    # Always truncate at a word boundary when multi-word so words are never sliced mid-token
    window = s[: max_len + 1]
    last_space = window.rfind(" ")
    if last_space > 0:
        clipped = window[:last_space].rstrip(" ,;:-")
        if clipped:
            return clipped
    return s[:max_len].rstrip()


def _resolve_cta_fallback(scene_text: str = "", default: str = "Learn More") -> str:
    """Derive a concise CTA-ish fallback phrase from `scene_text` or return `default`."""
    cleaned = " ".join((scene_text or "").strip().split())
    if cleaned:
        m = re.search(
            r"\b((?:try|start|get|download|protect|block|check|scan|learn|claim|activate|join)\b[^.!?,;:]{2,20})",
            cleaned,
            flags=re.IGNORECASE,
        )
        if m:
            candidate = _clamp_str(m.group(1).strip(), CHAR_LIMITS["CtaButton"]["label"])
            if len(re.sub(r"[^A-Za-z0-9]+", "", candidate)) >= 4:
                return candidate
    return default


def _clean_cta_label(raw_label: Any, fallback: str = "Learn More") -> str:
    """Ensure a CtaButton label is never empty, whitespace-only, or too short."""
    fb = _clamp_str(fallback or "Learn More", CHAR_LIMITS["CtaButton"]["label"], default="Learn More")
    if len(re.sub(r"[^A-Za-z0-9]+", "", fb)) < 3:
        fb = "Learn More"
    if raw_label is None:
        return fb
    s = " ".join(str(raw_label).strip().split())
    s = _repair_truncated_words(s)
    alnum = re.sub(r"[^A-Za-z0-9]+", "", s)
    if not s or len(alnum) < 3 or len(s) < 3:
        return fb
    clamped = _clamp_str(s, CHAR_LIMITS["CtaButton"]["label"], default=fb)
    if len(re.sub(r"[^A-Za-z0-9]+", "", clamped)) < 3:
        return fb
    return clamped


def _clamp_kinetic_row_text(val: Any) -> str:
    """Clamp a KineticWordStack row string to <=20 chars and ensure no single unbroken word >12 chars."""
    s = " ".join(str(val or "").strip().split()).upper()
    if not s:
        return "PROTECTED"
    max_total = CHAR_LIMITS["KineticWordStack"]["row_text"]
    max_word = CHAR_LIMITS["KineticWordStack"]["max_single_word"]
    tokens = [tok[:max_word] for tok in s.split() if tok]
    rebuilt = " ".join(tokens)
    if len(rebuilt) > max_total:
        # Try dropping trailing tokens if multi-word, else slice
        fit_tokens: list[str] = []
        for tok in tokens:
            candidate = " ".join(fit_tokens + [tok])
            if len(candidate) <= max_total:
                fit_tokens.append(tok)
            else:
                break
        rebuilt = " ".join(fit_tokens) if fit_tokens else rebuilt[:max_total].rstrip()
    return rebuilt or "PROTECTED"


def normalize_motion_spec(
    raw_motion: Any,
    *,
    cue_fallback: Any = None,
    default_entrance: str = "fade-up",
    default_emphasis: str = "none",
) -> dict[str, str]:
    """Normalize a primitive's `motion` block into `{entrance, emphasis, cue}`."""
    if not isinstance(raw_motion, dict):
        raw_motion = {}
    raw_entrance = str(raw_motion.get("entrance") or default_entrance).strip().lower()
    entrance = _ENTRANCE_VERB_ALIASES.get(raw_entrance, default_entrance)
    if entrance not in MOTION_VERBS:
        entrance = default_entrance

    raw_emphasis = str(raw_motion.get("emphasis") or default_emphasis).strip().lower()
    emphasis = raw_emphasis if raw_emphasis in EMPHASIS_VERBS else default_emphasis

    raw_cue = raw_motion.get("cue")
    if raw_cue is None:
        raw_cue = cue_fallback
    cue = re.sub(r"[^a-zA-Z0-9]+", "", str(raw_cue or "").strip().lower())
    return {
        "entrance": entrance,
        "emphasis": emphasis,
        "cue": cue,
    }


def _validate_icon_name(icon: Any) -> str | None:
    if not icon or not isinstance(icon, str):
        return None
    clean = icon.strip().lower()
    return clean if clean in ICON_REGISTRY else None


def _validate_primitive(
    item: Any,
    *,
    allow_containers: bool = True,
    cta_fallback: str = "Learn More",
) -> dict[str, Any] | None:
    """Validate and clamp a single primitive dict. Returns `None` if primitive type or required icon is unknown."""
    if not isinstance(item, dict):
        return None
    ptype = str(item.get("type") or "").strip()
    if ptype not in PRIMITIVE_TYPES or ptype == "StageSafeZone":
        return None
    if not allow_containers and ptype in ("PhoneFrame", "LayoutSlot", "BrandHeader", "KineticWordStack"):
        return None

    raw_props = item.get("props") if isinstance(item.get("props"), dict) else {}
    # Also allow flat props directly on `item` if an LLM omitted the nested `"props"` wrapper
    merged_props = {k: v for k, v in item.items() if k not in ("type", "props", "motion")}
    merged_props.update(raw_props)

    motion = normalize_motion_spec(
        item.get("motion"),
        cue_fallback=merged_props.get("cue"),
    )

    if ptype == "BrandHeader":
        align = str(merged_props.get("align") or "center").strip().lower()
        if align not in ("center", "split"):
            align = "center"
        logo_plate = str(merged_props.get("logo_plate") or "auto").strip().lower()
        if logo_plate not in ("auto", "pill", "none"):
            logo_plate = "auto"
        tag_text = _clamp_str(merged_props.get("tag_text", ""), CHAR_LIMITS["BrandHeader"]["tag_text"])
        wordmark = _clamp_str(merged_props.get("wordmark", ""), CHAR_LIMITS["BrandHeader"]["wordmark"])
        props: dict[str, Any] = {
            "align": align,
            "logo_plate": logo_plate,
        }
        if tag_text:
            props["tag_text"] = tag_text
        if wordmark:
            props["wordmark"] = wordmark
        return {"type": "BrandHeader", "props": props, "motion": motion}

    if ptype == "HeadlineBlock":
        headline = _clamp_str(
            merged_props.get("headline") or merged_props.get("title") or merged_props.get("text"),
            CHAR_LIMITS["HeadlineBlock"]["headline"],
            default="Real-Time Protection",
        )
        eyebrow = _clamp_str(merged_props.get("eyebrow", ""), CHAR_LIMITS["HeadlineBlock"]["eyebrow"])
        subhead = _clamp_str(
            merged_props.get("subhead") or merged_props.get("subtitle") or merged_props.get("body") or "",
            CHAR_LIMITS["HeadlineBlock"]["subhead"],
        )
        size = str(merged_props.get("size") or "lg").strip().lower()
        if size not in ("md", "lg", "hero"):
            size = "lg"
        align = str(merged_props.get("align") or "center").strip().lower()
        if align not in ("center", "left"):
            align = "center"
        props = {
            "headline": headline,
            "size": size,
            "align": align,
        }
        if eyebrow:
            props["eyebrow"] = eyebrow
        if subhead:
            props["subhead"] = subhead
        return {"type": "HeadlineBlock", "props": props, "motion": motion}

    if ptype == "KineticWordStack":
        raw_rows = merged_props.get("rows")
        if not isinstance(raw_rows, list):
            raw_rows = []
        valid_rows: list[dict[str, Any]] = []
        valid_styles = ("plain", "pill-accent", "pill-accent2", "outline")
        for idx, r in enumerate(raw_rows):
            if isinstance(r, str):
                r = {"text": r, "style": valid_styles[idx % len(valid_styles)]}
            if not isinstance(r, dict):
                continue
            r_text = _clamp_kinetic_row_text(r.get("text") or r.get("label") or r.get("word"))
            r_style = str(r.get("style") or valid_styles[idx % len(valid_styles)]).strip().lower()
            if r_style not in valid_styles:
                r_style = valid_styles[idx % len(valid_styles)]
            r_cue = re.sub(r"[^a-zA-Z0-9]+", "", str(r.get("cue") or "").strip().lower())
            valid_rows.append({"text": r_text, "style": r_style, "cue": r_cue})
        min_rows, max_rows = CHILD_CAPS["KineticWordStack"]
        valid_rows = valid_rows[:max_rows]
        if len(valid_rows) == 1:
            words_in_row = valid_rows[0]["text"].split()
            if len(words_in_row) >= 2:
                mid = max(1, len(words_in_row) // 2)
                valid_rows = [
                    {
                        "text": _clamp_kinetic_row_text(" ".join(words_in_row[:mid])),
                        "style": valid_rows[0]["style"],
                        "cue": valid_rows[0]["cue"],
                    },
                    {
                        "text": _clamp_kinetic_row_text(" ".join(words_in_row[mid:])),
                        "style": "pill-accent2",
                        "cue": valid_rows[0]["cue"],
                    },
                ]
            else:
                valid_rows.append({"text": "INSTANTLY", "style": "pill-accent2", "cue": ""})
        elif len(valid_rows) < min_rows:
            valid_rows = [
                {"text": "SCAM GUARD", "style": "pill-accent", "cue": ""},
                {"text": "PROTECTED", "style": "outline", "cue": ""},
            ]
        return {"type": "KineticWordStack", "props": {"rows": valid_rows}, "motion": motion}

    if ptype == "PhoneFrame":
        header_label = _clamp_str(
            merged_props.get("header_label") or merged_props.get("title") or "LIVE PROTECTION",
            CHAR_LIMITS["PhoneFrame"]["header_label"],
        )
        raw_children = merged_props.get("children")
        if not isinstance(raw_children, list):
            raw_children = []
        children: list[dict[str, Any]] = []
        for ch in raw_children:
            v_ch = _validate_primitive(ch, allow_containers=False, cta_fallback=cta_fallback)
            if v_ch is not None:
                children.append(v_ch)
        _, max_ch = CHILD_CAPS["PhoneFrame"]
        children = children[:max_ch]
        if not children:
            children = [
                {
                    "type": "FeatureCard",
                    "props": {
                        "title": "Protected Call",
                        "body": "Verified safe before your phone rings.",
                        "icon": "shield",
                        "tone": "accent",
                        "tilt": "none",
                    },
                    "motion": {"entrance": "pop", "emphasis": "none", "cue": motion["cue"]},
                }
            ]
        return {
            "type": "PhoneFrame",
            "props": {
                "header_label": header_label,
                "bezel": "preset",
                "children": children,
            },
            "motion": motion,
        }

    if ptype == "LayoutSlot":
        direction = str(merged_props.get("direction") or "column").strip().lower()
        if direction not in ("column", "row", "grid-2col"):
            direction = "column"
        align = str(merged_props.get("align") or "center").strip().lower()
        if align not in ("center", "stretch"):
            align = "center"
        raw_children = merged_props.get("children")
        if not isinstance(raw_children, list):
            raw_children = []
        children = []
        for ch in raw_children:
            v_ch = _validate_primitive(ch, allow_containers=False, cta_fallback=cta_fallback)
            if v_ch is not None:
                children.append(v_ch)
        _, max_ch = CHILD_CAPS["LayoutSlot"]
        children = children[:max_ch]
        if not children:
            return None
        return {
            "type": "LayoutSlot",
            "props": {"direction": direction, "align": align, "children": children},
            "motion": motion,
        }

    if ptype == "FeatureCard":
        title = _clamp_str(
            merged_props.get("title") or merged_props.get("headline") or merged_props.get("label"),
            CHAR_LIMITS["FeatureCard"]["title"],
            default="Verified Protection",
        )
        body = _clamp_str(
            merged_props.get("body") or merged_props.get("subhead") or merged_props.get("text") or "",
            CHAR_LIMITS["FeatureCard"]["body"],
        )
        icon = _validate_icon_name(merged_props.get("icon"))
        tone = str(merged_props.get("tone") or "surface").strip().lower()
        if tone not in ("surface", "accent", "accent2"):
            tone = "surface"
        tilt = str(merged_props.get("tilt") or "none").strip().lower()
        if tilt not in ("none", "left", "right"):
            tilt = "none"
        props = {"title": title, "tone": tone, "tilt": tilt}
        if body:
            props["body"] = body
        if icon:
            props["icon"] = icon
        return {"type": "FeatureCard", "props": props, "motion": motion}

    if ptype == "PillBadge":
        label = _clamp_str(
            merged_props.get("label") or merged_props.get("text") or merged_props.get("title"),
            CHAR_LIMITS["PillBadge"]["label"],
            default="LIVE SHIELD",
        )
        icon = _validate_icon_name(merged_props.get("icon"))
        tone = str(merged_props.get("tone") or "accent").strip().lower()
        if tone not in ("accent", "accent2", "ink", "surface"):
            tone = "accent"
        pulse = bool(merged_props.get("pulse", False))
        props = {"label": label, "tone": tone, "pulse": pulse}
        if icon:
            props["icon"] = icon
        return {"type": "PillBadge", "props": props, "motion": motion}

    if ptype == "IconBadge":
        icon_name = _validate_icon_name(merged_props.get("name") or merged_props.get("icon"))
        if icon_name is None:
            # Drop IconBadge with unknown icon name
            return None
        size = str(merged_props.get("size") or "md").strip().lower()
        if size not in ("sm", "md", "lg"):
            size = "md"
        tone = str(merged_props.get("tone") or "accent").strip().lower()
        if tone not in ("accent", "accent2", "ink", "surface"):
            tone = "accent"
        draw_stroke = bool(merged_props.get("draw_stroke", False))
        if draw_stroke and item.get("motion", {}).get("entrance") is None:
            motion["entrance"] = "draw"
        return {
            "type": "IconBadge",
            "props": {
                "name": icon_name,
                "size": size,
                "tone": tone,
                "draw_stroke": draw_stroke,
            },
            "motion": motion,
        }

    if ptype == "StatCallout":
        raw_val = merged_props.get("value")
        has_explicit_val = bool(str(raw_val if raw_val is not None else "").strip())
        value = _clamp_str(
            raw_val if has_explicit_val else "99.9%",
            CHAR_LIMITS["StatCallout"]["value"],
            default="99.9%",
        )
        label = _clamp_str(
            merged_props.get("label") or merged_props.get("title") or "VERIFIED PROTECTION",
            CHAR_LIMITS["StatCallout"]["label"],
            default="VERIFIED PROTECTION",
        )
        delta = _clamp_str(merged_props.get("delta", ""), CHAR_LIMITS["StatCallout"]["delta"])
        meter = str(merged_props.get("meter") or "none").strip().lower()
        if meter not in ("none", "bar", "arc"):
            meter = "none"
        raw_prog = merged_props.get("progress")
        has_valid_progress = False
        progress = 0.88
        if raw_prog is not None and raw_prog != "":
            try:
                p_val = float(raw_prog)
                if p_val > 0.0:
                    has_valid_progress = True
                    progress = max(0.05, min(1.0, p_val))
            except (TypeError, ValueError):
                has_valid_progress = False
        elif meter == "bar":
            has_valid_progress = True

        # Guard against empty/non-numeric arc ring: only draw arc gauge when value is a compact
        # numeric metric (has at least one digit, <= 8 chars) and valid positive progress was supplied.
        is_numeric_stat = bool(has_explicit_val and re.search(r"\d", value) and len(value) <= 8)
        if meter == "arc" and (not has_valid_progress or not is_numeric_stat):
            meter = "none"
        elif meter == "bar" and not has_valid_progress:
            meter = "none"

        props = {
            "value": value,
            "label": label,
            "meter": meter,
            "progress": round(progress, 3),
        }
        if delta:
            props["delta"] = delta
        return {"type": "StatCallout", "props": props, "motion": motion}

    if ptype == "ChecklistStack":
        raw_items = merged_props.get("items")
        if not isinstance(raw_items, list):
            raw_items = []
        items: list[dict[str, Any]] = []
        for entry in raw_items:
            if isinstance(entry, str):
                entry = {"label": entry, "state": "check"}
            if not isinstance(entry, dict):
                continue
            lbl = _clamp_str(
                entry.get("label") or entry.get("text") or entry.get("title"),
                CHAR_LIMITS["ChecklistStack"]["item_label"],
            )
            if not lbl:
                continue
            st = str(entry.get("state") or "check").strip().lower()
            if st not in ("check", "alert", "bullet", "x"):
                st = "check"
            it_cue = re.sub(r"[^a-zA-Z0-9]+", "", str(entry.get("cue") or "").strip().lower())
            items.append({"label": lbl, "state": st, "cue": it_cue})
        _, max_items = CHILD_CAPS["ChecklistStack"]
        items = items[:max_items]
        if not items:
            items = [
                {"label": "Real-time caller verification", "state": "check", "cue": ""},
                {"label": "Automated scam blocking", "state": "check", "cue": ""},
            ]
        return {"type": "ChecklistStack", "props": {"items": items}, "motion": motion}

    if ptype == "CtaButton":
        label = _clean_cta_label(
            merged_props.get("label") if "label" in merged_props else merged_props.get("text"),
            fallback=cta_fallback,
        )
        icon = _validate_icon_name(merged_props.get("icon"))
        tone = str(merged_props.get("tone") or "ink").strip().lower()
        if tone not in ("ink", "accent", "accent2", "surface"):
            tone = "ink"
        emphasis = str(
            merged_props.get("emphasis") or motion.get("emphasis") or "pulse"
        ).strip().lower()
        if emphasis not in EMPHASIS_VERBS:
            emphasis = "pulse"
        motion["emphasis"] = emphasis
        props = {"label": label, "tone": tone, "emphasis": emphasis}
        if icon:
            props["icon"] = icon
        return {"type": "CtaButton", "props": props, "motion": motion}

    return None


def validate_scene_spec(
    raw_spec: Any,
    *,
    style: str = "flat-explainer",
    strict: bool = False,
    ensure_brand_header: bool = False,
    scene_text: str = "",
) -> dict[str, Any]:
    """Validate, clamp lengths, enforce child caps, and drop unknown primitives/icons on a `SceneSpec`."""
    if not isinstance(raw_spec, dict):
        raise SceneSpecValidationError(f"SceneSpec must be a JSON object/dict, got {type(raw_spec).__name__}.")

    # Unwrap if the LLM wrapped everything inside a StageSafeZone root object
    spec_data = dict(raw_spec)
    if spec_data.get("type") == "StageSafeZone" and isinstance(spec_data.get("props"), dict):
        inner = dict(spec_data["props"])
        if "primitives" not in inner and isinstance(spec_data.get("primitives"), list):
            inner["primitives"] = spec_data["primitives"]
        if "primitives" not in inner and isinstance(inner.get("children"), list):
            inner["primitives"] = inner["children"]
        spec_data = inner

    raw_primitives = spec_data.get("primitives")
    if raw_primitives is None and isinstance(spec_data.get("children"), list):
        raw_primitives = spec_data["children"]

    if not isinstance(raw_primitives, list):
        if strict:
            raise SceneSpecValidationError("SceneSpec must contain a 'primitives' list.")
        raw_primitives = []

    if strict and len(raw_primitives) == 0:
        raise SceneSpecValidationError("SceneSpec 'primitives' list cannot be empty.")

    resolved_style = str(spec_data.get("style") or style or "flat-explainer").strip()

    default_slot_by_style = {
        "flat-explainer": "phone-hero",
        "neo-brutal": "center-stack",
        "glass-ui": "phone-hero",
        "editorial-clinical": "editorial-grid",
        "kinetic-type": "kinetic-rows",
    }
    layout_slot = str(
        spec_data.get("layout_slot") or default_slot_by_style.get(resolved_style, "center-stack")
    ).strip().lower()
    if layout_slot not in LAYOUT_SLOTS:
        layout_slot = default_slot_by_style.get(resolved_style, "center-stack")

    gap = str(spec_data.get("gap") or "md").strip().lower()
    if gap not in GAP_TOKENS:
        gap = "md"

    default_bg_by_style = {
        "flat-explainer": "radial",
        "neo-brutal": "solid",
        "glass-ui": "orb-glow",
        "editorial-clinical": "solid",
        "kinetic-type": "solid",
    }
    bg_variant = str(
        spec_data.get("bg_variant") or default_bg_by_style.get(resolved_style, "solid")
    ).strip().lower()
    if bg_variant not in BG_VARIANTS:
        bg_variant = default_bg_by_style.get(resolved_style, "solid")

    cta_fallback = _resolve_cta_fallback(scene_text, default="Learn More")
    validated_primitives: list[dict[str, Any]] = []
    for item in raw_primitives:
        v_item = _validate_primitive(item, allow_containers=True, cta_fallback=cta_fallback)
        if v_item is not None:
            validated_primitives.append(v_item)

    if strict and len(validated_primitives) == 0:
        raise SceneSpecValidationError(
            "SceneSpec contained zero valid known primitives after schema validation."
        )

    has_content_primitive = any(
        p["type"] != "BrandHeader" for p in validated_primitives
    )
    if strict and not has_content_primitive:
        raise SceneSpecValidationError(
            "SceneSpec must contain at least one content primitive in addition to BrandHeader."
        )

    if ensure_brand_header and not any(p["type"] == "BrandHeader" for p in validated_primitives):
        validated_primitives.insert(
            0,
            {
                "type": "BrandHeader",
                "props": {"align": "center", "logo_plate": "auto"},
                "motion": {"entrance": "fade-up", "emphasis": "none", "cue": ""},
            },
        )

    _, max_stage_prims = CHILD_CAPS["stage_primitives"]
    validated_primitives = validated_primitives[:max_stage_prims]

    return {
        "style": resolved_style,
        "layout_slot": layout_slot,
        "gap": gap,
        "bg_variant": bg_variant,
        "primitives": validated_primitives,
    }


def extract_json_object(raw_content: str) -> str:
    """Strip markdown code fences (` ```json ... ``` `) and isolate the outermost JSON object."""
    text = (raw_content or "").strip()
    fence_match = re.search(
        r"```(?:json)?\s*(\{.*?\})\s*```",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if fence_match:
        return fence_match.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1].strip()
    return text


def parse_llm_scene_spec(
    raw_content: str,
    *,
    style: str = "flat-explainer",
    scene_text: str = "",
) -> dict[str, Any]:
    """Parse and strictly validate an LLM JSON `SceneSpec` string or raise `SceneSpecValidationError`."""
    cleaned = extract_json_object(raw_content)
    if not cleaned:
        raise SceneSpecValidationError("Empty LLM response when expecting SceneSpec JSON.")
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise SceneSpecValidationError(f"Invalid JSON in SceneSpec response: {exc}") from exc
    return validate_scene_spec(
        parsed,
        style=style,
        strict=True,
        ensure_brand_header=True,
        scene_text=scene_text,
    )


def _derive_scene_copy_parts(
    scene_text: str,
    words: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Extract concise headline, subhead, card copy, kinetic rows, and cue words from `scene_text`."""
    cleaned = " ".join((scene_text or "").strip().split())
    if not cleaned:
        cleaned = "Stop phone scams before they reach you."

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", cleaned) if s.strip()]
    first = sentences[0] if sentences else cleaned
    rest = " ".join(sentences[1:]) if len(sentences) > 1 else ""

    tokens = first.split()
    if len(tokens) <= 7:
        headline = first
        sub = rest if rest else "Real-time AI call screening that blocks threats before your phone rings."
    else:
        headline = " ".join(tokens[:6])
        tail = " ".join(tokens[6:])
        if tail and tail[0].islower():
            tail = tail[0].upper() + tail[1:]
        sub = tail + (" " + rest if rest else "")

    headline = _clamp_str(headline, CHAR_LIMITS["HeadlineBlock"]["headline"], "Stop Phone Scams Fast")
    sub = _clamp_str(sub, CHAR_LIMITS["HeadlineBlock"]["subhead"], "Verified protection on every call.")

    # Derive non-redundant FeatureCard title & body that complement the HeadlineBlock
    if len(sentences) > 1:
        s2_tokens = sentences[1].split()
        raw_card_title = " ".join(s2_tokens[:4]) if len(s2_tokens) >= 3 else "Real-Time Call Screening"
        raw_card_body = sentences[1]
    else:
        raw_card_title = "Real-Time Call Screening"
        raw_card_body = "Screens unknown callers and blocks scams before your phone rings."

    card_title = _clamp_str(
        raw_card_title,
        CHAR_LIMITS["FeatureCard"]["title"],
        "Real-Time Call Screening",
    )
    card_body = _clamp_str(
        raw_card_body,
        CHAR_LIMITS["FeatureCard"]["body"],
        "Caller verified and screened before you answer.",
    )

    alpha_words = [re.sub(r"[^A-Za-z0-9]+", "", w) for w in cleaned.split()]
    alpha_words = [w for w in alpha_words if len(w) >= 2]

    stop_words = {"the", "and", "for", "are", "with", "that", "this", "from", "have", "like"}
    content_words = [w.upper() for w in alpha_words if w.lower() not in stop_words]
    # Skip long leading brand token (e.g. "MALWAREBYTES") when building kinetic rows & tag
    # because BrandHeader already displays the brand wordmark right above the stack
    kinetic_candidates = [
        w for i, w in enumerate(content_words)
        if not (i == 0 and len(w) >= 11 and len(content_words) >= 5)
    ]
    if len(kinetic_candidates) < 4:
        kinetic_candidates = content_words or ["SCAM", "GUARD", "BLOCKS", "CALLS"]

    # Build 4 short, punchy kinetic rows (each <= 11 chars so font-size stays 114px inside pills)
    kinetic_rows: list[str] = []
    idx = 0
    while idx < len(kinetic_candidates) and len(kinetic_rows) < 4:
        w1 = kinetic_candidates[idx][:12]
        if idx + 1 < len(kinetic_candidates):
            w2 = kinetic_candidates[idx + 1][:12]
            combined = f"{w1} {w2}"
            if len(combined) <= 11 and len(kinetic_candidates) - idx > (4 - len(kinetic_rows)):
                kinetic_rows.append(combined)
                idx += 2
                continue
        kinetic_rows.append(w1)
        idx += 1

    fallback_rows = ["SCAM GUARD", "CHECKS CALLS", "BEFORE", "YOU ANSWER"]
    while len(kinetic_rows) < 4:
        kinetic_rows.append(fallback_rows[len(kinetic_rows)])

    short_keywords = [w for w in kinetic_candidates if 3 <= len(w) <= 8]
    tag_word = short_keywords[0] if short_keywords else "LIVE"
    tag = _clamp_str(f"{tag_word} SHIELD", CHAR_LIMITS["BrandHeader"]["tag_text"], "LIVE SHIELD")
    cta = "Try It Free"

    norm_words = list(words or [])
    if norm_words:
        w_tokens = [
            re.sub(r"[^a-z0-9]+", "", str(w.get("word", "")).lower())
            for w in norm_words
        ]
        w_tokens = [w for w in w_tokens if w]
    else:
        w_tokens = [w.lower() for w in alpha_words if w]

    if not w_tokens:
        w_tokens = ["scam", "guard", "call", "answer"]

    cue1 = w_tokens[0]
    cue2 = w_tokens[min(len(w_tokens) - 1, max(1, len(w_tokens) // 2))]
    row_cues = [
        w_tokens[min(len(w_tokens) - 1, int(round(i * (len(w_tokens) - 1) / 3.0)))]
        for i in range(4)
    ]

    return {
        "tag": tag,
        "headline": headline,
        "sub": sub,
        "card_title": card_title,
        "card_body": card_body,
        "cta": cta,
        "cue1": cue1,
        "cue2": cue2,
        "kinetic_rows": kinetic_rows[:4],
        "row_cues": row_cues,
    }


def _replace_placeholders_in_obj(obj: Any, replacements: dict[str, str]) -> Any:
    if isinstance(obj, str):
        out = obj
        for k, v in replacements.items():
            out = out.replace(k, v)
        return out
    if isinstance(obj, list):
        return [_replace_placeholders_in_obj(item, replacements) for item in obj]
    if isinstance(obj, dict):
        return {k: _replace_placeholders_in_obj(v, replacements) for k, v in obj.items()}
    return obj


def build_default_scene_spec(
    scene_text: str,
    style: str = "flat-explainer",
    *,
    brand: Any = None,
    duration_s: float = 5.0,
    words: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a deterministic, $0 `SceneSpec` for `style` from `scene_text` and `words`."""
    clean_style = (style or "flat-explainer").strip()
    spec_path = SPECS_STYLES_DIR / f"{clean_style}.json"
    if not spec_path.is_file():
        spec_path = SPECS_STYLES_DIR / "flat-explainer.json"

    template_spec = json.loads(spec_path.read_text(encoding="utf-8"))
    parts = _derive_scene_copy_parts(scene_text, words)

    replacements = {
        "__SCENE_TAG__": parts["tag"],
        "__SCENE_HEADLINE__": parts["headline"],
        "__SCENE_SUB__": parts["sub"],
        "__CARD_TITLE__": parts["card_title"],
        "__CARD_BODY__": parts["card_body"],
        "__SCENE_CTA__": parts["cta"],
        "__CUE_WORD_1__": parts["cue1"],
        "__CUE_WORD_2__": parts["cue2"],
        "__KINETIC_ROW_1__": parts["kinetic_rows"][0],
        "__KINETIC_ROW_2__": parts["kinetic_rows"][1],
        "__KINETIC_ROW_3__": parts["kinetic_rows"][2],
        "__KINETIC_ROW_4__": parts["kinetic_rows"][3],
        "__CUE_ROW_1__": parts["row_cues"][0],
        "__CUE_ROW_2__": parts["row_cues"][1],
        "__CUE_ROW_3__": parts["row_cues"][2],
        "__CUE_ROW_4__": parts["row_cues"][3],
    }
    populated = _replace_placeholders_in_obj(copy.deepcopy(template_spec), replacements)
    populated["style"] = clean_style
    return validate_scene_spec(
        populated,
        style=clean_style,
        strict=False,
        ensure_brand_header=True,
        scene_text=scene_text,
    )


def resolve_brand_header_contrast(
    norm_brand: dict[str, Any],
    *,
    logo_plate: str = "auto",
) -> dict[str, Any]:
    """Compute contrast-verified plate and wordmark colors for `BrandHeader`.

    Rule:
    - Whenever `logo_plate == "pill"` OR (`logo_plate == "auto"` and the brand's raw/logo
      ink or normalized ink has WCAG contrast < 4.5 against `bg`), places the logo+wordmark
      on a high-contrast pill plate (`#FFFFFF` on dark bg, `#0A1628` on light bg).
    - Wordmark text color is always picked via `pick_contrasting_text(..., effective_bg, min_ratio=4.5)`
      so `contrast(wordmark_color, effective_bg) >= 4.5` in every style and brand polarity.
    """
    from parallax.html_motion import _relative_luminance, pick_contrasting_text, wcag_contrast_ratio

    palette = norm_brand["palette"]
    bg_hex = palette["bg"]
    raw_brand_ink = palette.get("brand_ink") or palette["ink"]
    norm_ink = palette["ink"]

    raw_contrast_vs_bg = wcag_contrast_ratio(raw_brand_ink, bg_hex)
    norm_contrast_vs_bg = wcag_contrast_ratio(norm_ink, bg_hex)

    mode = (logo_plate or "auto").strip().lower()
    needs_plate = (
        mode == "pill"
        or (mode == "auto" and (raw_contrast_vs_bg < 4.5 or norm_contrast_vs_bg < 4.5))
    )

    if needs_plate:
        if _relative_luminance(bg_hex) < 0.4:
            plate_bg = "#FFFFFF"
        else:
            plate_bg = "#0A1628"
        wordmark_color = pick_contrasting_text(raw_brand_ink, plate_bg, min_ratio=4.5)
        effective_bg = plate_bg
    else:
        plate_bg = bg_hex
        wordmark_color = pick_contrasting_text(raw_brand_ink, bg_hex, min_ratio=4.5)
        effective_bg = bg_hex

    return {
        "has_plate": needs_plate,
        "plate_bg": plate_bg,
        "effective_bg": effective_bg,
        "wordmark_color": wordmark_color,
        "contrast_ratio": round(wcag_contrast_ratio(wordmark_color, effective_bg), 2),
    }


def resolve_preset_tokens(
    preset: dict[str, Any],
    norm_brand: dict[str, Any],
    *,
    gap: str = "md",
) -> dict[str, str]:
    """Resolve all CSS custom-property tokens for `preset` and `norm_brand`."""
    palette = norm_brand["palette"]
    fonts = norm_brand["fonts"]
    raw_tokens = dict(preset.get("tokens") or {})
    vocab = dict(preset.get("motion_vocabulary") or preset.get("motion") or {})
    eases = list(vocab.get("eases") or ["power3.out", "back.out(1.6)"])
    durations = dict(vocab.get("durations") or {}) if isinstance(vocab.get("durations"), dict) else {}

    ease_enter = str(eases[0]) if eases else "power3.out"
    ease_pop = next(( str(e) for e in eases if "back" in str(e) or "expo" in str(e) ), ease_enter)
    dur_enter = str(durations.get("entrance", 0.36))

    surf_r, surf_g, surf_b = _parse_hex_rgb_local(palette["surface"])
    surf_alpha = float(raw_tokens.get("surface_opacity", 1.0))
    if surf_alpha < 0.99:
        # For glassmorphic presets, keep surface background translucent while ensuring
        # high-contrast legibility against the dark canvas
        surface_css = f"rgba({surf_r}, {surf_g}, {surf_b}, {max(0.55, surf_alpha):.2f})"
    else:
        surface_css = palette["surface"]

    header_contrast = resolve_brand_header_contrast(norm_brand, logo_plate="auto")
    if gap == "md" and raw_tokens.get("slot_gap"):
        slot_gap = str(raw_tokens["slot_gap"])
    else:
        slot_gap = GAP_TOKENS.get(gap, str(raw_tokens.get("slot_gap", "34px")))
    inner_gap = str(raw_tokens.get("inner_gap", "26px"))

    return {
        "--bg": palette["bg"],
        "--surface": palette["surface"],
        "--surface-css": surface_css,
        "--ink": palette["ink"],
        "--accent": palette["accent"],
        "--accent2": palette["accent2"],
        "--on-bg": palette.get("on_bg", palette["ink"]),
        "--on-surface": palette.get("on_surface", palette["ink"]),
        "--on-accent": palette.get("on_accent", "#FFFFFF"),
        "--on-accent2": palette.get("on_accent2", "#FFFFFF"),
        "--on-ink": palette.get("on_ink", "#FFFFFF"),
        "--brand-plate-bg": header_contrast["effective_bg"],
        "--brand-wordmark-color": header_contrast["wordmark_color"],
        "--font-heading": fonts["heading"],
        "--font-body": fonts["body"],
        "--radius-card": str(raw_tokens.get("radius_card", "24px")),
        "--radius-phone": str(raw_tokens.get("radius_phone", "48px")),
        "--radius-pill": str(raw_tokens.get("radius_pill", "999px")),
        "--border-card": str(raw_tokens.get("border_card", "2px solid rgba(10, 22, 40, 0.10)")),
        "--border-phone": str(raw_tokens.get("border_phone", "8px solid var(--ink)")),
        "--shadow-card": str(raw_tokens.get("shadow_card", "0 16px 40px rgba(10, 22, 40, 0.12)")),
        "--shadow-phone": str(raw_tokens.get("shadow_phone", "0 24px 60px rgba(10, 22, 40, 0.16)")),
        "--glass-blur": str(raw_tokens.get("glass_blur", "0px")),
        "--surface-opacity": str(raw_tokens.get("surface_opacity", "1")),
        "--headline-weight": str(raw_tokens.get("headline_weight", "800")),
        "--headline-case": str(raw_tokens.get("headline_case", "none")),
        "--headline-letter-spacing": str(raw_tokens.get("headline_letter_spacing", "-0.02em")),
        "--stage-gap": slot_gap,
        "--inner-gap": inner_gap,
        "--pad-card": str(raw_tokens.get("pad_card", "39px 46px")),
        "--pad-phone": str(raw_tokens.get("pad_phone", "42px 50px")),
        "--pad-button": str(raw_tokens.get("pad_button", "32px 62px")),
        "--pad-pill": str(raw_tokens.get("pad_pill", "26px 50px")),
        "--pad-stat": str(raw_tokens.get("pad_stat", "35px 46px")),
        "--pad-checklist": str(raw_tokens.get("pad_checklist", "32px 48px")),
        "--pad-badge": str(raw_tokens.get("pad_badge", "28px")),
        "--pad-brand-plate": str(raw_tokens.get("pad_brand_plate", "18px 44px")),
        "--pad-kinetic-row": str(raw_tokens.get("pad_kinetic_row", "24px 54px")),
        "--ease-enter": ease_enter,
        "--ease-pop": ease_pop,
        "--dur-enter": dur_enter,
    }


def _parse_hex_rgb_local(hex_color: str) -> tuple[int, int, int]:
    s = (hex_color or "").strip().lstrip("#")
    if len(s) == 3 and all(c in "0123456789abcdefABCDEF" for c in s):
        s = "".join(c * 2 for c in s)
    if len(s) >= 6 and all(c in "0123456789abcdefABCDEF" for c in s[:6]):
        return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    return (15, 23, 42)


def _render_svg_icon(icon_name: str | None, size_px: int = 36) -> str:
    if not icon_name or icon_name not in ICON_REGISTRY:
        return ""
    inner = ICON_REGISTRY[icon_name]
    return (
        f'<svg class="prim-icon" data-icon="{icon_name}" width="{size_px}" height="{size_px}" '
        f'viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">'
        f"{inner}</svg>"
    )


def _tone_style_vars(tone: str) -> tuple[str, str]:
    """Return `(bg_var, fg_var)` CSS expressions for a semantic tone."""
    t = (tone or "surface").strip().lower()
    if t == "accent":
        return ("var(--accent)", "var(--on-accent)")
    if t == "accent2":
        return ("var(--accent2)", "var(--on-accent2)")
    if t == "ink":
        return ("var(--ink)", "var(--on-ink)")
    return ("var(--surface)", "var(--on-surface)")


def _clean_display_brand_name(raw_name: str) -> str:
    cleaned = re.sub(r"\s*\(placeholder\)\s*$", "", str(raw_name or "").strip(), flags=re.IGNORECASE).strip()
    return cleaned or str(raw_name or "Brand").strip()


def _render_primitive_html(
    prim: dict[str, Any],
    *,
    idx_prefix: str,
    norm_brand: dict[str, Any],
    style_name: str,
    anim_Collector: list[dict[str, Any]],
    in_phone: bool = False,
    in_sparse_phone: bool = False,
) -> str:
    ptype = prim["type"]
    props = prim.get("props") or {}
    motion = normalize_motion_spec(prim.get("motion"))
    dom_id = f"prim-{idx_prefix}"
    fit_max_headline = 760 if in_phone else 860
    fit_max_card = 720 if in_phone else 800
    fit_max_pill = 680 if in_phone else 780
    fit_max_cta = 680 if in_phone else 760

    if ptype == "BrandHeader":
        logo_plate_mode = str(props.get("logo_plate") or "auto")
        hc = resolve_brand_header_contrast(norm_brand, logo_plate=logo_plate_mode)
        raw_brand_name = norm_brand["name"]
        display_wordmark = _clamp_str(
            props.get("wordmark") or _clean_display_brand_name(raw_brand_name),
            CHAR_LIMITS["BrandHeader"]["wordmark"],
            default=_clean_display_brand_name(raw_brand_name),
        )
        tag_text = _clamp_str(props.get("tag_text") or "", CHAR_LIMITS["BrandHeader"]["tag_text"])
        plate_class = "brand-plate is-pill" if hc["has_plate"] else "brand-plate is-flat"

        anim_Collector.append(
            {
                "id": dom_id,
                "type": "BrandHeader",
                "entrance": motion["entrance"],
                "emphasis": "none",
                "cue": motion["cue"],
                "early": True,
            }
        )
        tag_html = ""
        if tag_text:
            tag_html = f'<div class="brand-tag">{_escape_html(tag_text)}</div>'

        return (
            f'<header id="{dom_id}" class="primitive brand-header" data-primitive="BrandHeader" '
            f'data-brand-name="{_escape_html(raw_brand_name)}">'
            f'<div class="{plate_class}" data-has-plate="{str(hc["has_plate"]).lower()}" '
            f'data-effective-bg="{hc["effective_bg"]}" '
            f'style="background-color: {hc["effective_bg"]}; color: {hc["wordmark_color"]};">'
            f'<span class="brand-logo-wrap">'
            f'<img id="brand-logo" src="{norm_brand["logo_data_uri"]}" alt="{_escape_html(raw_brand_name)}">'
            f"</span>"
            f'<span class="brand-wordmark" data-wordmark-color="{hc["wordmark_color"]}" '
            f'style="color: {hc["wordmark_color"]};">{_escape_html(display_wordmark)}</span>'
            f"</div>"
            f"{tag_html}"
            f"</header>"
        )

    if ptype == "HeadlineBlock":
        size = str(props.get("size") or "lg").strip().lower()
        if in_sparse_phone and size in ("md", "lg"):
            size = "hero"
        min_hl_size = 88 if size == "hero" else 76
        eyebrow = _clamp_str(props.get("eyebrow") or "", CHAR_LIMITS["HeadlineBlock"]["eyebrow"])
        headline = _clamp_str(
            props.get("headline") or "",
            CHAR_LIMITS["HeadlineBlock"]["headline"],
            default="Real-Time Protection",
        )
        subhead = _clamp_str(props.get("subhead") or "", CHAR_LIMITS["HeadlineBlock"]["subhead"])
        anim_Collector.append(
            {
                "id": dom_id,
                "type": "HeadlineBlock",
                "entrance": motion["entrance"],
                "emphasis": motion["emphasis"],
                "cue": motion["cue"],
                "is_headline": True,
            }
        )
        eyebrow_html = f'<div class="headline-eyebrow">{_escape_html(eyebrow)}</div>' if eyebrow else ""
        sub_html = f'<p class="headline-sub">{_escape_html(subhead)}</p>' if subhead else ""
        return (
            f'<section id="{dom_id}" class="primitive headline-block size-{size}" data-primitive="HeadlineBlock">'
            f"{eyebrow_html}"
            f'<h1 class="headline headline-main fit-text" data-max-width="{fit_max_headline}" data-min-size="{min_hl_size}">{_escape_html(headline)}</h1>'
            f"{sub_html}"
            f"</section>"
        )

    if ptype == "KineticWordStack":
        rows = props.get("rows") or []
        row_htmls: list[str] = []
        for r_idx, row in enumerate(rows):
            row_id = f"{dom_id}-row-{r_idx}"
            r_style = row.get("style") or "plain"
            r_text = row.get("text") or ""
            r_cue = row.get("cue") or motion["cue"]
            anim_Collector.append(
                {
                    "id": row_id,
                    "type": "KineticRow",
                    "entrance": motion["entrance"] if motion["entrance"] != "fade-up" else "pop",
                    "emphasis": "none",
                    "cue": r_cue,
                    "is_headline": (r_idx == 0),
                    "row_index": r_idx,
                }
            )
            row_htmls.append(
                f'<div id="{row_id}" class="kinetic-row style-{r_style}" '
                f'data-primitive="KineticRow" data-row-style="{r_style}">'
                f"{_escape_html(r_text)}</div>"
            )
        return (
            f'<section id="{dom_id}" class="primitive kinetic-stack" data-primitive="KineticWordStack">'
            + "".join(row_htmls)
            + "</section>"
        )

    if ptype == "PhoneFrame":
        header_label = _clamp_str(
            props.get("header_label") or "LIVE PROTECTION",
            CHAR_LIMITS["PhoneFrame"]["header_label"],
            default="LIVE PROTECTION",
        )
        children = props.get("children") or []
        is_sparse = len(children) <= 2
        sparse_cls = " is-sparse" if is_sparse else ""
        anim_Collector.append(
            {
                "id": dom_id,
                "type": "PhoneFrame",
                "entrance": motion["entrance"],
                "emphasis": "none",
                "cue": motion["cue"],
                "early": True,
            }
        )
        child_htmls: list[str] = []
        for c_idx, ch in enumerate(children):
            child_htmls.append(
                _render_primitive_html(
                    ch,
                    idx_prefix=f"{idx_prefix}-{c_idx}",
                    norm_brand=norm_brand,
                    style_name=style_name,
                    anim_Collector=anim_Collector,
                    in_phone=True,
                    in_sparse_phone=is_sparse,
                )
            )
        return (
            f'<section id="{dom_id}" class="primitive phone-frame{sparse_cls}" '
            f'data-primitive="PhoneFrame" data-child-count="{len(children)}">'
            f'<div class="phone-speaker" aria-hidden="true"></div>'
            f'<div class="phone-header-label">{_escape_html(header_label)}</div>'
            f'<div class="phone-body">'
            + "".join(child_htmls)
            + "</div></section>"
        )

    if ptype == "LayoutSlot":
        direction = props.get("direction") or "column"
        children = props.get("children") or []
        child_htmls = [
            _render_primitive_html(
                ch,
                idx_prefix=f"{idx_prefix}-{c_idx}",
                norm_brand=norm_brand,
                style_name=style_name,
                anim_Collector=anim_Collector,
                in_phone=in_phone,
                in_sparse_phone=in_sparse_phone,
            )
            for c_idx, ch in enumerate(children)
        ]
        return (
            f'<div id="{dom_id}" class="primitive layout-slot dir-{direction}" data-primitive="LayoutSlot">'
            + "".join(child_htmls)
            + "</div>"
        )

    if ptype == "FeatureCard":
        title = _clamp_str(
            props.get("title") or "",
            CHAR_LIMITS["FeatureCard"]["title"],
            default="Verified Protection",
        )
        body = _clamp_str(props.get("body") or "", CHAR_LIMITS["FeatureCard"]["body"])
        icon = props.get("icon")
        tone = props.get("tone") or "surface"
        tilt = props.get("tilt") or "none"
        bg_var, fg_var = _tone_style_vars(tone)
        tilt_cls = f" tilt-{tilt}" if style_name == "neo-brutal" and tilt in ("left", "right") else ""

        anim_Collector.append(
            {
                "id": dom_id,
                "type": "FeatureCard",
                "entrance": motion["entrance"],
                "emphasis": motion["emphasis"],
                "cue": motion["cue"],
                "is_headline": True,
            }
        )
        icon_html = f'<div class="card-icon-wrap">{_render_svg_icon(icon, 58)}</div>' if icon else ""
        body_html = f'<div class="card-sub">{_escape_html(body)}</div>' if body else ""
        return (
            f'<article id="{dom_id}" class="primitive feature-card tone-{tone}{tilt_cls}" '
            f'data-primitive="FeatureCard" style="background-color: {bg_var}; color: {fg_var};">'
            f"{icon_html}"
            f'<div class="card-title fit-text" data-max-width="{fit_max_card}" data-min-size="44">{_escape_html(title)}</div>'
            f"{body_html}"
            f"</article>"
        )

    if ptype == "PillBadge":
        label = _clamp_str(
            props.get("label") or "",
            CHAR_LIMITS["PillBadge"]["label"],
            default="LIVE SHIELD",
        )
        icon = props.get("icon")
        tone = props.get("tone") or "accent"
        pulse = bool(props.get("pulse", False))
        bg_var, fg_var = _tone_style_vars(tone)
        anim_Collector.append(
            {
                "id": dom_id,
                "type": "PillBadge",
                "entrance": motion["entrance"] if motion["entrance"] != "fade-up" else "pop",
                "emphasis": "pulse" if pulse else motion["emphasis"],
                "cue": motion["cue"],
            }
        )
        pill_icon_px = 42 if in_sparse_phone else 38
        icon_html = _render_svg_icon(icon, pill_icon_px) if icon else ""
        return (
            f'<div id="{dom_id}" class="primitive pill-badge tone-{tone}" '
            f'data-primitive="PillBadge" style="background-color: {bg_var}; color: {fg_var};">'
            f"{icon_html}"
            f'<span class="pill-label fit-text" data-max-width="{fit_max_pill}" data-min-size="36">{_escape_html(label)}</span>'
            f"</div>"
        )

    if ptype == "IconBadge":
        name = props.get("name") or "shield"
        size = props.get("size") or "md"
        tone = props.get("tone") or "accent"
        draw_stroke = bool(props.get("draw_stroke", False))
        size_px = {"sm": 84, "md": 94, "lg": 108}.get(size, 94)
        if in_sparse_phone:
            size_px = max(size_px, 112)
        hero_badge_cls = " is-hero-badge" if (in_sparse_phone or not in_phone) else ""
        bg_var, fg_var = _tone_style_vars(tone)
        anim_Collector.append(
            {
                "id": dom_id,
                "type": "IconBadge",
                "entrance": "draw" if draw_stroke else motion["entrance"],
                "emphasis": motion["emphasis"],
                "cue": motion["cue"],
                "early": True,
            }
        )
        return (
            f'<div id="{dom_id}" class="primitive icon-badge size-{size} tone-{tone}{hero_badge_cls}" '
            f'data-primitive="IconBadge" data-icon="{name}" '
            f'style="background-color: {bg_var}; color: {fg_var};">'
            f"{_render_svg_icon(name, size_px)}"
            f"</div>"
        )

    if ptype == "StatCallout":
        raw_val = props.get("value")
        has_explicit_val = bool(str(raw_val if raw_val is not None else "").strip())
        value = _clamp_str(
            raw_val if has_explicit_val else "99.9%",
            CHAR_LIMITS["StatCallout"]["value"],
            default="99.9%",
        )
        label = _clamp_str(
            props.get("label") or "VERIFIED",
            CHAR_LIMITS["StatCallout"]["label"],
            default="VERIFIED",
        )
        delta = _clamp_str(props.get("delta") or "", CHAR_LIMITS["StatCallout"]["delta"])
        meter = str(props.get("meter") or "none").strip().lower()
        raw_prog = props.get("progress")
        has_valid_progress = False
        progress = 0.0
        if raw_prog is not None and raw_prog != "":
            try:
                p_val = float(raw_prog)
                if p_val > 0.0:
                    has_valid_progress = True
                    progress = max(0.05, min(1.0, p_val))
            except (TypeError, ValueError):
                has_valid_progress = False

        is_numeric_stat = bool(has_explicit_val and re.search(r"\d", value) and len(value) <= 8)
        if meter == "arc" and (not has_valid_progress or not is_numeric_stat):
            meter = "none"
        elif meter == "bar" and not has_valid_progress:
            meter = "none"

        anim_Collector.append(
            {
                "id": dom_id,
                "type": "StatCallout",
                "entrance": "draw" if meter in ("bar", "arc") else motion["entrance"],
                "emphasis": motion["emphasis"],
                "cue": motion["cue"],
                "early": True,
            }
        )
        delta_html = f'<span class="stat-delta">{_escape_html(delta)}</span>' if delta else ""
        if meter == "arc":
            circumference = 427
            dash = int(round(min(0.92, max(0.12, progress)) * circumference))
            return (
                f'<div id="{dom_id}" class="primitive stat-callout meter-arc" data-primitive="StatCallout" '
                f'style="background-color: var(--surface); color: var(--on-surface);">'
                f'<div class="stat-arc-wrap">'
                f'<svg class="stat-arc-svg" width="176" height="176" viewBox="0 0 168 168" fill="none" aria-hidden="true">'
                f'<circle class="stat-arc-track" cx="84" cy="84" r="68" stroke="currentColor" stroke-opacity="0.18" stroke-width="12" fill="none"/>'
                f'<circle class="stat-arc-progress svg-draw-path" cx="84" cy="84" r="68" stroke="var(--accent2)" '
                f'stroke-width="12" style="stroke-dasharray: {dash}px {circumference}px; stroke-dashoffset: 0;" '
                f'stroke-linecap="round" transform="rotate(-90 84 84)" fill="none"/>'
                f"</svg>"
                f'<span class="stat-value stat-arc-value fit-text" data-max-width="136" data-min-size="44">{_escape_html(value)}</span>'
                f"</div>"
                f'<div class="stat-main">'
                f"{delta_html}"
                f'<div class="stat-label">{_escape_html(label)}</div>'
                f"</div>"
                f"</div>"
            )

        meter_html = ""
        if meter == "bar":
            pct = int(round(progress * 100))
            meter_html = (
                '<div class="stat-bar-track">'
                f'<div class="stat-bar-fill" style="width: {pct}%;"></div>'
                "</div>"
            )
        return (
            f'<div id="{dom_id}" class="primitive stat-callout" data-primitive="StatCallout" '
            f'style="background-color: var(--surface); color: var(--on-surface);">'
            f"{meter_html}"
            f'<div class="stat-main">'
            f'<div class="stat-value-row">'
            f'<span class="stat-value fit-text" data-max-width="{fit_max_card}" data-min-size="44">{_escape_html(value)}</span>'
            f"{delta_html}"
            f"</div>"
            f'<div class="stat-label">{_escape_html(label)}</div>'
            f"</div>"
            f"</div>"
        )

    if ptype == "ChecklistStack":
        items = props.get("items") or []
        anim_Collector.append(
            {
                "id": dom_id,
                "type": "ChecklistStack",
                "entrance": motion["entrance"],
                "emphasis": "none",
                "cue": motion["cue"],
            }
        )
        rows_html: list[str] = []
        for i_idx, item in enumerate(items):
            state = item.get("state") or "check"
            icon_key = {"check": "check", "alert": "alert", "x": "x", "bullet": "spark"}.get(state, "check")
            rows_html.append(
                f'<div class="checklist-item" id="{dom_id}-item-{i_idx}">'
                f'<span class="checklist-icon">{_render_svg_icon(icon_key, 38)}</span>'
                f'<span class="checklist-label">{_escape_html(item.get("label") or "")}</span>'
                f"</div>"
            )
        return (
            f'<div id="{dom_id}" class="primitive checklist-stack" data-primitive="ChecklistStack" '
            f'style="background-color: var(--surface); color: var(--on-surface);">'
            + "".join(rows_html)
            + "</div>"
        )

    if ptype == "CtaButton":
        label = _clean_cta_label(props.get("label"), fallback="Learn More")
        icon = props.get("icon")
        tone = props.get("tone") or "ink"
        emphasis = props.get("emphasis") or motion["emphasis"] or "pulse"
        bg_var, fg_var = _tone_style_vars(tone)
        anim_Collector.append(
            {
                "id": dom_id,
                "type": "CtaButton",
                "entrance": motion["entrance"] if motion["entrance"] != "fade-up" else "pop",
                "emphasis": emphasis,
                "cue": motion["cue"],
            }
        )
        icon_html = _render_svg_icon(icon, 42) if icon else ""
        return (
            f'<div id="{dom_id}" class="primitive cta-button btn tone-{tone}" '
            f'data-primitive="CtaButton" style="background-color: {bg_var}; color: {fg_var};">'
            f"{icon_html}"
            f'<span class="cta-label fit-text" data-max-width="{fit_max_cta}" data-min-size="44">{_escape_html(label)}</span>'
            f"</div>"
        )

    return ""


def _build_motion_runtime_js(anim_items: list[dict[str, Any]]) -> str:
    """Build deterministic GSAP timeline construction script from collected primitive metadata."""
    # Mark the very first headline/kinetic element so it is guaranteed visible by t <= 0.6s
    marked_first_headline = False
    for entry in anim_items:
        if entry.get("is_headline") or entry.get("type") in ("HeadlineBlock", "KineticRow", "FeatureCard"):
            if not marked_first_headline:
                entry["force_early_headline"] = True
                marked_first_headline = True
                break
    if not marked_first_headline and anim_items:
        anim_items[0]["force_early_headline"] = True

    specs_json = json.dumps(anim_items, ensure_ascii=False)
    return f"""
(function() {{
  var rootEl = document.getElementById('root');
  var styleName = (rootEl && rootEl.getAttribute('data-style')) || '';
  var stage = document.getElementById('safe-stage');
  var SAFE_H = {STAGE_HEIGHT};
  var SAFE_W = {STAGE_WIDTH};

  // 1. Guard CtaButton labels: never allow an empty or icon-only button
  function guardCtaButtons() {{
    var ctas = document.querySelectorAll('.cta-button');
    for (var i = 0; i < ctas.length; i++) {{
      var btn = ctas[i];
      var lbl = btn.querySelector('.cta-label');
      if (!lbl) {{
        lbl = document.createElement('span');
        lbl.className = 'cta-label fit-text';
        btn.appendChild(lbl);
      }}
      var rawTxt = (lbl.textContent || '').replace(/\\s+/g, ' ').trim();
      var alnum = rawTxt.replace(/[^A-Za-z0-9]+/g, '');
      if (!rawTxt || alnum.length < 3 || rawTxt.length < 3) {{
        lbl.textContent = 'Learn More';
      }}
      lbl.style.display = 'inline-block';
      lbl.style.visibility = 'visible';
      lbl.style.opacity = '1';
    }}
  }}

    // 2. Effective zoom & font-size floor helpers
  function roundZoom(v) {{
    return Math.round(v * 1000) / 1000;
  }}

  function getEffectiveZoom(el) {{
    var z = 1.0;
    var cur = el;
    while (cur && cur !== document.body && cur !== document.documentElement) {{
      var sz = parseFloat(cur.style && cur.style.zoom);
      if (!isNaN(sz) && sz > 0) z *= sz;
      cur = cur.parentElement;
    }}
    return z;
  }}

  function getEffectiveFloor(el) {{
    if (!el || !el.classList) return 36;
    if (el.classList.contains('kinetic-row')) return 96;
    if (el.classList.contains('stat-arc-value')) return 36;
    if (el.classList.contains('headline-main')) {{
      var hb = el.closest('.headline-block');
      if (hb && hb.classList.contains('size-hero')) return 88;
      return 76;
    }}
    if (
      el.classList.contains('card-title') ||
      el.classList.contains('cta-label') ||
      el.classList.contains('cta-button') ||
      el.classList.contains('brand-wordmark') ||
      el.classList.contains('stat-value')
    ) {{
      return 44;
    }}
    if (el.classList.contains('phone-header-label')) return 38;
    if (el.classList.contains('headline-eyebrow')) return 30;
    return 36;
  }}

  function normalizeIconBadges() {{
    if (!stage) return;
    var badges = stage.querySelectorAll('.icon-badge');
    for (var i = 0; i < badges.length; i++) {{
      var b = badges[i];
      var z = getEffectiveZoom(b);
      var isHero = b.classList.contains('is-hero-badge') || !!b.closest('.phone-frame.is-sparse');
      var targetScreenPx = isHero ? 168 : 150;
      var localPx = Math.round(targetScreenPx / z);
      b.style.width = localPx + 'px';
      b.style.height = localPx + 'px';
      b.style.minWidth = localPx + 'px';
      b.style.minHeight = localPx + 'px';
      var svg = b.querySelector('svg');
      if (svg) {{
        var svgPx = Math.round(localPx * 0.58);
        svg.style.width = svgPx + 'px';
        svg.style.height = svgPx + 'px';
      }}
    }}
  }}

  // Fit text while respecting per-element effective font-size floors
  // (body/pill/badge >= 36px, title/cta >= 44px, headline lg >= 76px, hero >= 88px, kinetic >= 96px, eyebrow >= 28px)
  function fitAllText(enforceZoomFloors) {{
    guardCtaButtons();
    if (!stage) return;

    var textSelector = [
      '.fit-text',
      '.kinetic-row',
      '.headline-main',
      '.headline-sub',
      '.headline-eyebrow',
      '.card-title',
      '.card-sub',
      '.pill-badge',
      '.pill-label',
      '.checklist-item',
      '.checklist-label',
      '.cta-button',
      '.cta-label',
      '.stat-value',
      '.stat-arc-value',
      '.stat-label',
      '.stat-delta',
      '.brand-wordmark',
      '.brand-tag',
      '.phone-header-label'
    ].join(', ');

    var textNodes = stage.querySelectorAll(textSelector);
    for (var i = 0; i < textNodes.length; i++) {{
      var node = textNodes[i];
      var compSize = parseFloat(window.getComputedStyle(node).fontSize) || 36;
      if (!node.getAttribute('data-base-font-size')) {{
        node.setAttribute('data-base-font-size', String(compSize));
      }}
      var baseSize = parseFloat(node.getAttribute('data-base-font-size')) || compSize;
      var effFloor = getEffectiveFloor(node);
      var targetSize = Math.max(baseSize, effFloor);
      if (enforceZoomFloors) {{
        var z = getEffectiveZoom(node);
        if (z >= 0.70) {{
          var localFloor = Math.ceil((effFloor + 0.4) / z);
          targetSize = Math.max(targetSize, localFloor);
        }}
      }}
      node.style.fontSize = targetSize + 'px';
      node.style.letterSpacing = '';
    }}

    if (window.__fitText) {{
      window.__fitText('.kinetic-row', 900, 96);
    }}

    var singleLineNodes = stage.querySelectorAll('.brand-wordmark, .cta-label, .pill-label, .stat-value, .stat-arc-value');
    for (var s = 0; s < singleLineNodes.length; s++) {{
      var sNode = singleLineNodes[s];
      var sz = getEffectiveZoom(sNode);
      var sEffFloor = getEffectiveFloor(sNode);
      var sFloor = (enforceZoomFloors && sz >= 0.70 && !sNode.classList.contains('stat-arc-value'))
        ? Math.ceil((sEffFloor + 0.4) / sz)
        : (sNode.classList.contains('stat-arc-value') ? 28 : sEffFloor);
      var sCur = parseFloat(window.getComputedStyle(sNode).fontSize) || sFloor;
      var maxBoxW = sNode.classList.contains('stat-arc-value') ? 156 : 790;
      while (
        ((sNode.clientWidth > 0 && sNode.scrollWidth > sNode.clientWidth + 1) ||
          sNode.getBoundingClientRect().width > maxBoxW) &&
        sCur > sFloor
      ) {{
        sCur = Math.max(sFloor, sCur - 1);
        sNode.style.fontSize = sCur + 'px';
      }}
    }}

    var allNodes = stage.querySelectorAll('*');
    for (var j = 0; j < allNodes.length; j++) {{
      var tel = allNodes[j];
      if (tel.clientWidth > 0 && tel.scrollWidth > tel.clientWidth + 1) {{
        if (!tel.classList.contains('kinetic-row') && !tel.classList.contains('brand-wordmark') && !tel.classList.contains('stat-arc-value')) {{
          tel.style.whiteSpace = 'normal';
        }}
        var tz = getEffectiveZoom(tel);
        var curSize = parseFloat(window.getComputedStyle(tel).fontSize) || 36;
        var tEffFloor = getEffectiveFloor(tel);
        var floor = (enforceZoomFloors && tz >= 0.70 && !tel.classList.contains('stat-arc-value'))
          ? Math.ceil((tEffFloor + 0.4) / tz)
          : (tel.classList.contains('stat-arc-value') ? 26 : tEffFloor);
        while (tel.scrollWidth > tel.clientWidth + 1 && curSize > floor) {{
          curSize = Math.max(floor, curSize - 1);
          tel.style.fontSize = curSize + 'px';
        }}
        if (tel.scrollWidth > tel.clientWidth + 1) {{
          tel.style.letterSpacing = '-0.045em';
        }}
        if (tel.scrollWidth > tel.clientWidth + 1 && (tz < 0.70 || tel.classList.contains('stat-arc-value'))) {{
          var hardFloor = tel.classList.contains('stat-arc-value') ? 24 : Math.ceil(28 / Math.max(0.45, tz));
          while (tel.scrollWidth > tel.clientWidth + 1 && curSize > hardFloor) {{
            curSize = Math.max(hardFloor, curSize - 1);
            tel.style.fontSize = curSize + 'px';
          }}
        }}
      }}
    }}
  }}

  // 3. Measure natural/current stack bounding box inside #safe-stage
  function measureStack() {{
    if (!stage || !stage.children || stage.children.length === 0) {{
      return {{ height: 0, width: 0, top: 0, bottom: 0, left: 0, right: 0 }};
    }}
    var ch = stage.children;
    var minT = Infinity, maxB = -Infinity, minL = Infinity, maxR = -Infinity;
    for (var i = 0; i < ch.length; i++) {{
      var r = ch[i].getBoundingClientRect();
      if (r.width <= 0 || r.height <= 0) continue;
      if (r.top < minT) minT = r.top;
      if (r.bottom > maxB) maxB = r.bottom;
      if (r.left < minL) minL = r.left;
      if (r.right > maxR) maxR = r.right;
    }}
    if (minT === Infinity) {{
      return {{ height: 0, width: 0, top: 0, bottom: 0, left: 0, right: 0 }};
    }}
    return {{
      height: maxB - minT,
      width: maxR - minL,
      top: minT,
      bottom: maxB,
      left: minL,
      right: maxR
    }};
  }}

  function applyStageZoom(z, baseStageGap) {{
    if (!stage) return;
    var ch = stage.children;
    var isEditorialGrid = stage.classList.contains('slot-editorial-grid');
    var maxScreenW = isEditorialGrid ? 880 : (styleName === 'neo-brutal' ? 884 : 896);
    for (var i = 0; i < ch.length; i++) {{
      var el = ch[i];
      el.style.zoom = String(roundZoom(z));
      if (el.classList.contains('phone-frame')) {{
        var targetPhoneW = Math.min(maxScreenW, Math.max(844, Math.round(892 * z)));
        el.style.width = (targetPhoneW / z).toFixed(2) + 'px';
        el.style.maxWidth = (maxScreenW / z).toFixed(2) + 'px';
      }} else if (!el.classList.contains('icon-badge') && !el.classList.contains('pill-badge') && !el.classList.contains('kinetic-stack')) {{
        el.style.maxWidth = (maxScreenW / z).toFixed(2) + 'px';
        var baseW = parseFloat(el.getAttribute('data-base-width')) || 892;
        var minScreenW = Math.min(baseW, 800);
        var targetW = Math.min(maxScreenW, Math.max(minScreenW, Math.round(baseW * z)));
        el.style.width = (targetW / z).toFixed(2) + 'px';
      }}
    }}
    var scaledGap = z >= 1.0
      ? Math.max(baseStageGap, Math.min(64, Math.round(baseStageGap * Math.min(z, 1.32))))
      : Math.max(28, Math.round(baseStageGap * Math.max(0.75, z)));
    stage.style.gap = scaledGap + 'px';
    stage.setAttribute('data-auto-fit-zoom', String(roundZoom(z)));
    normalizeIconBadges();
  }}

  normalizeIconBadges();
  fitAllText(false);

  // 4. Auto-fit content block height to ~85% (accept 0.80-0.90) of SAFE_H (1240px)
  if (stage && stage.children.length > 0) {{
    var baseStageGap = parseFloat(window.getComputedStyle(stage).rowGap || window.getComputedStyle(stage).gap) || 34;
    for (var ci = 0; ci < stage.children.length; ci++) {{
      var cel = stage.children[ci];
      cel.setAttribute('data-base-width', String(cel.getBoundingClientRect().width || 892));
    }}

    var m0 = measureStack();
    var ratio0 = m0.height / SAFE_H;
    var currentZoom = 1.0;
    stage.setAttribute('data-auto-fit-zoom', '1');

    var isKineticScene = (styleName === 'kinetic-type' && !!stage.querySelector('.kinetic-stack'));
    if (isKineticScene) {{
      // Keep kinetic-type's look: adjust gaps/padding first so rows stay at zoom=1.0 (>= 96px)
      var kStack = stage.querySelector('.kinetic-stack');
      if (ratio0 > 0.90) {{
        if (kStack) kStack.style.gap = '20px';
        stage.style.gap = '28px';
        m0 = measureStack();
        ratio0 = m0.height / SAFE_H;
      }}
      if (ratio0 > 0.91 || m0.width > 912) {{
        var targetHK = 0.86 * SAFE_H;
        for (var kStep = 0; kStep < 4 && (measureStack().height > 0.91 * SAFE_H || measureStack().width > 912); kStep++) {{
          var mk = measureStack();
          var zk = Math.min(targetHK / Math.max(1, mk.height), 900 / Math.max(1, mk.width));
          currentZoom = Math.max(0.85, Math.min(1.0, currentZoom * zk));
          for (var ki = 0; ki < stage.children.length; ki++) {{
            stage.children[ki].style.zoom = String(roundZoom(currentZoom));
          }}
          stage.setAttribute('data-auto-fit-zoom', String(roundZoom(currentZoom)));
          normalizeIconBadges();
          fitAllText(true);
        }}
      }} else if (ratio0 < 0.80) {{
        var extraK = (0.84 * SAFE_H) - m0.height;
        if (kStack && extraK > 0) {{
          var kRows = kStack.querySelectorAll('.kinetic-row');
          var numKRows = Math.max(1, kRows.length);
          var padAddPerSide = Math.min(22, Math.round((extraK * 0.35) / (numKRows * 2)));
          for (var kri = 0; kri < kRows.length; kri++) {{
            var kr = kRows[kri];
            var kpt = parseFloat(window.getComputedStyle(kr).paddingTop) || 22;
            var kpb = parseFloat(window.getComputedStyle(kr).paddingBottom) || 22;
            kr.style.paddingTop = (kpt + padAddPerSide) + 'px';
            kr.style.paddingBottom = (kpb + padAddPerSide) + 'px';
          }}
          var mAfterPad = measureStack();
          var remK = Math.max(0, (0.84 * SAFE_H) - mAfterPad.height);
          var curKGap = parseFloat(window.getComputedStyle(kStack).rowGap || window.getComputedStyle(kStack).gap) || 24;
          var kGaps = Math.max(1, numKRows - 1);
          var addKGap = Math.min(76, Math.round((remK * 0.68) / kGaps));
          kStack.style.gap = (curKGap + addKGap) + 'px';
          var mAfterKGap = measureStack();
          var remStageK = Math.max(0, (0.84 * SAFE_H) - mAfterKGap.height);
          if (remStageK > 4 && stage.children.length > 1) {{
            var addStKGap = Math.min(84, Math.round(remStageK / (stage.children.length - 1)));
            stage.style.gap = (baseStageGap + addStKGap) + 'px';
          }}
        }}
      }}
      normalizeIconBadges();
      fitAllText(true);
    }} else {{
      var minTargetRatio = 0.81;
      var maxTargetRatio = 0.89;
      var idealGrowH = 0.85 * SAFE_H;   // ~1054px
      var idealShrinkH = 0.85 * SAFE_H; // ~1054px

      if (ratio0 < minTargetRatio) {{
        // Sparse or medium scene: scale UP via CSS zoom (capped at 1.32x; base widths are 892px)
        currentZoom = Math.min(1.32, Math.max(1.02, idealGrowH / Math.max(1, m0.height)));
        applyStageZoom(currentZoom, baseStageGap);
        fitAllText(false);

        var mGrow = measureStack();
        if (mGrow.height > maxTargetRatio * SAFE_H) {{
          // If text line-wrapping during zoom pushed height past 0.89, step zoom back to fit
          for (var gBack = 0; gBack < 5 && measureStack().height > maxTargetRatio * SAFE_H; gBack++) {{
            var mgb = measureStack();
            currentZoom = Math.max(0.65, currentZoom * (idealShrinkH / Math.max(1, mgb.height)));
            applyStageZoom(currentZoom, baseStageGap);
            fitAllText(false);
          }}
        }} else if (mGrow.height < minTargetRatio * SAFE_H) {{
          // Still below 81% after zoom: expand vertical breathing room (stage gap + phone/card vertical padding & body gap)
          var remH = idealGrowH - mGrow.height;
          var numStageGaps = Math.max(1, stage.children.length - 1);
          var curStGap = parseFloat(window.getComputedStyle(stage).rowGap || window.getComputedStyle(stage).gap) || baseStageGap;
          var stGapAdd = Math.min(44, Math.round((remH * 0.25) / numStageGaps));
          stage.style.gap = (curStGap + stGapAdd) + 'px';

          mGrow = measureStack();
          remH = Math.max(0, idealGrowH - mGrow.height);
          if (remH > 4) {{
            var phone = stage.querySelector('.phone-frame');
            var phoneBody = stage.querySelector('.phone-body');
            if (phone && phoneBody) {{
              var localRem = remH / currentZoom;
              var pChildren = phoneBody.children.length;
              var pGaps = Math.max(1, pChildren - 1);
              var curPhonePadT = parseFloat(window.getComputedStyle(phone).paddingTop) || 42;
              var curPhonePadB = parseFloat(window.getComputedStyle(phone).paddingBottom) || 42;
              var curBodyGap = parseFloat(window.getComputedStyle(phoneBody).rowGap || window.getComputedStyle(phoneBody).gap) || 26;

              var bodyGapAdd = Math.min(64, Math.round((localRem * 0.42) / pGaps));
              phoneBody.style.gap = (curBodyGap + bodyGapAdd) + 'px';
              var usedByGap = bodyGapAdd * (pChildren > 1 ? pGaps : 0);
              var padEach = Math.max(0, Math.round((localRem - usedByGap) / 2));
              phone.style.paddingTop = (curPhonePadT + padEach) + 'px';
              phone.style.paddingBottom = (curPhonePadB + padEach) + 'px';
            }} else {{
              var slotEl = stage.querySelector('.layout-slot');
              if (slotEl) {{
                var curSlotGap = parseFloat(window.getComputedStyle(slotEl).rowGap || window.getComputedStyle(slotEl).gap) || 26;
                var slotChildren = Math.max(1, slotEl.children.length - 1);
                var slotGapAdd = Math.min(48, Math.round(((remH * 0.35) / currentZoom) / slotChildren));
                slotEl.style.gap = (curSlotGap + slotGapAdd) + 'px';
              }}
              mGrow = measureStack();
              remH = Math.max(0, idealGrowH - mGrow.height);
              var blocks = stage.querySelectorAll('.feature-card, .stat-callout, .checklist-stack, .headline-block, .kinetic-stack');
              if (blocks.length > 0 && remH > 4) {{
                var localPerBlock = (remH / currentZoom) / (blocks.length * 2);
                for (var bi = 0; bi < blocks.length; bi++) {{
                  var bpt = parseFloat(window.getComputedStyle(blocks[bi]).paddingTop) || 28;
                  var bpb = parseFloat(window.getComputedStyle(blocks[bi]).paddingBottom) || 28;
                  blocks[bi].style.paddingTop = Math.round(bpt + localPerBlock) + 'px';
                  blocks[bi].style.paddingBottom = Math.round(bpb + localPerBlock) + 'px';
                }}
              }}
            }}
          }}
          fitAllText(false);
        }}
      }} else if (ratio0 > maxTargetRatio) {{
        // Dense / many-primitive spec: scale DOWN via CSS zoom so stack lands at ~85% of SAFE_H
        for (var sStep = 0; sStep < 6 && measureStack().height > maxTargetRatio * SAFE_H; sStep++) {{
          var ms = measureStack();
          currentZoom = Math.max(0.45, Math.min(1.0, currentZoom * (idealShrinkH / Math.max(1, ms.height))));
          applyStageZoom(currentZoom, baseStageGap);
          fitAllText(false);
        }}
      }}

      // Final clamp pass in case post-zoom text wrapping shifted stack height outside [0.80, 0.895]
      for (var fStep = 0; fStep < 6; fStep++) {{
        var mf = measureStack();
        var rf = mf.height / SAFE_H;
        if (rf > 0.895 || mf.bottom > 1318 || mf.top < 82) {{
          currentZoom = Math.max(0.45, currentZoom * Math.min(0.97, (0.85 * SAFE_H) / Math.max(1, mf.height)));
          applyStageZoom(currentZoom, baseStageGap);
          fitAllText(false);
        }} else if (rf < 0.80) {{
          if (currentZoom < 1.32) {{
            currentZoom = Math.min(1.32, currentZoom * ((0.85 * SAFE_H) / Math.max(1, mf.height)));
            applyStageZoom(currentZoom, baseStageGap);
            fitAllText(false);
          }} else {{
            var extraF = (0.84 * SAFE_H) - mf.height;
            var fPhone = stage.querySelector('.phone-frame');
            if (fPhone && extraF > 4) {{
              var curFMinH = fPhone.getBoundingClientRect().height / currentZoom;
              fPhone.style.minHeight = Math.ceil(curFMinH + (extraF / currentZoom)) + 'px';
            }} else {{
              var fBlocks = stage.querySelectorAll('.feature-card, .stat-callout, .checklist-stack, .headline-block');
              if (fBlocks.length > 0 && extraF > 4) {{
                var addPer = (extraF / currentZoom) / (fBlocks.length * 2);
                for (var fbi = 0; fbi < fBlocks.length; fbi++) {{
                  var fbpt = parseFloat(window.getComputedStyle(fBlocks[fbi]).paddingTop) || 28;
                  var fbpb = parseFloat(window.getComputedStyle(fBlocks[fbi]).paddingBottom) || 28;
                  fBlocks[fbi].style.paddingTop = Math.round(fbpt + addPer) + 'px';
                  fBlocks[fbi].style.paddingBottom = Math.round(fbpb + addPer) + 'px';
                }}
              }}
            }}
            fitAllText(false);
          }}
        }} else {{
          break;
        }}
      }}

      // Guarantee sparse phones (<=2 children) have rendered height >= 600px
      var sparsePhones = stage.querySelectorAll('.phone-frame.is-sparse');
      for (var spi = 0; spi < sparsePhones.length; spi++) {{
        var sp = sparsePhones[spi];
        var spZ = getEffectiveZoom(sp);
        if (stage.children.length >= 3) {{
          sp.style.minHeight = Math.ceil(608 / spZ) + 'px';
          sp.style.paddingTop = '42px';
          sp.style.paddingBottom = '42px';
          sp.style.gap = '26px';
          var spBody = sp.querySelector('.phone-body');
          if (spBody) {{
            spBody.style.gap = '26px';
            spBody.style.paddingTop = '4px';
            spBody.style.paddingBottom = '4px';
          }}
        }}
        var spRect = sp.getBoundingClientRect();
        if (spRect.height < 606) {{
          sp.style.minHeight = Math.ceil(612 / spZ) + 'px';
        }}
      }}

      // Enforce effective font-size floors after zoom settles
      normalizeIconBadges();
      fitAllText(true);
      for (var pStep = 0; pStep < 4; pStep++) {{
        var mPost = measureStack();
        var rPost = mPost.height / SAFE_H;
        if (rPost > 0.90) {{
          stage.style.gap = '28px';
          var pBody = stage.querySelector('.phone-body');
          if (pBody) pBody.style.gap = '22px';
          mPost = measureStack();
          if (mPost.height > 0.90 * SAFE_H) {{
            currentZoom = Math.max(0.45, currentZoom * ((0.86 * SAFE_H) / Math.max(1, mPost.height)));
            applyStageZoom(currentZoom, 28);
            var spAfter = stage.querySelector('.phone-frame.is-sparse');
            if (spAfter && spAfter.getBoundingClientRect().height < 606) {{
              spAfter.style.minHeight = Math.ceil(610 / getEffectiveZoom(spAfter)) + 'px';
            }}
            fitAllText(true);
          }}
        }} else if (rPost < 0.80) {{
          var extraPost = (0.84 * SAFE_H) - mPost.height;
          var pPhone = stage.querySelector('.phone-frame');
          if (pPhone && extraPost > 4) {{
            var curPH = pPhone.getBoundingClientRect().height / currentZoom;
            pPhone.style.minHeight = Math.ceil(curPH + (extraPost / currentZoom)) + 'px';
          }} else if (currentZoom < 1.32) {{
            currentZoom = Math.min(1.32, currentZoom * ((0.85 * SAFE_H) / Math.max(1, mPost.height)));
            applyStageZoom(currentZoom, baseStageGap);
            fitAllText(true);
          }}
        }} else {{
          break;
        }}
      }}

      if (currentZoom < 0.95) {{
        var minLocalPad = Math.ceil(18 / currentZoom);
        var padContainers = stage.querySelectorAll('.feature-card, .cta-button, .pill-badge, .stat-callout, .checklist-stack');
        for (var pi = 0; pi < padContainers.length; pi++) {{
          var pc = padContainers[pi];
          var pst = window.getComputedStyle(pc);
          if ((parseFloat(pst.paddingTop) || 0) < minLocalPad) pc.style.paddingTop = minLocalPad + 'px';
          if ((parseFloat(pst.paddingBottom) || 0) < minLocalPad) pc.style.paddingBottom = minLocalPad + 'px';
          if ((parseFloat(pst.paddingLeft) || 0) < minLocalPad) pc.style.paddingLeft = minLocalPad + 'px';
          if ((parseFloat(pst.paddingRight) || 0) < minLocalPad) pc.style.paddingRight = minLocalPad + 'px';
        }}
      }}
    }}
  }}

  var timing = window.__TIMING || {{ duration: 5.0, fps: 30, words: [], cue: function(_, fb) {{ return fb || 0; }} }};
  var totalDur = Math.max(0.5, Number(timing.duration) || 5.0);
  var rootStyle = window.getComputedStyle(document.documentElement);
  var easeEnter = (rootStyle.getPropertyValue('--ease-enter') || 'power3.out').trim();
  var easePop = (rootStyle.getPropertyValue('--ease-pop') || 'back.out(1.6)').trim();
  var baseDur = parseFloat(rootStyle.getPropertyValue('--dur-enter')) || 0.35;

  window.__MOTION_VERBS = {{
    'fade-up': function(tl, el, start, dur) {{
      tl.fromTo(el, {{ opacity: 0, y: 18 }}, {{ opacity: 1, y: 0, duration: dur, ease: easeEnter }}, start);
    }},
    'pop': function(tl, el, start, dur) {{
      tl.fromTo(el, {{ opacity: 0, scale: 0.88 }}, {{ opacity: 1, scale: 1, duration: dur, ease: easePop }}, start);
    }},
    'slide-up': function(tl, el, start, dur) {{
      tl.fromTo(el, {{ opacity: 0, y: 26 }}, {{ opacity: 1, y: 0, duration: dur, ease: easeEnter }}, start);
    }},
    'stamp': function(tl, el, start, dur) {{
      tl.fromTo(el, {{ opacity: 0, scale: 1.06, y: -10 }}, {{ opacity: 1, scale: 1, y: 0, duration: dur, ease: easePop }}, start);
    }},
    'wipe': function(tl, el, start, dur) {{
      tl.fromTo(el, {{ opacity: 0, scaleX: 0.90, y: 8, transformOrigin: '50% 50%' }}, {{ opacity: 1, scaleX: 1, y: 0, duration: dur, ease: easeEnter }}, start);
    }},
    'draw': function(tl, el, start, dur) {{
      tl.fromTo(el, {{ opacity: 0, scale: 0.92 }}, {{ opacity: 1, scale: 1, duration: dur, ease: easeEnter }}, start);
      var paths = el.querySelectorAll('.svg-draw-path');
      if (paths && paths.length > 0) {{
        tl.fromTo(paths, {{ strokeDashoffset: 180 }}, {{ strokeDashoffset: 0, duration: dur, ease: 'power2.out' }}, start);
      }}
    }}
  }};

  var items = {specs_json};
  var tl = gsap.timeline({{ paused: true }});

  for (var i = 0; i < items.length; i++) {{
    var item = items[i];
    var el = document.getElementById(item.id);
    if (!el) continue;
    var verb = window.__MOTION_VERBS[item.entrance] || window.__MOTION_VERBS['fade-up'];
    var dur = Math.min(baseDur, 0.36);
    var fallbackTime = Math.min(0.05 + i * 0.06, Math.max(0.08, totalDur * 0.32));
    var startTime = timing.cue ? timing.cue(item.cue, fallbackTime) : fallbackTime;

    if (item.type === 'BrandHeader') {{
      startTime = 0.0;
      dur = Math.min(dur, 0.28);
    }} else if (item.early || item.force_early_headline) {{
      // Rule: first headline/kinetic element and its container must be 100% visible by t <= 0.6s regardless of cue
      dur = Math.min(dur, 0.34);
      startTime = Math.min(startTime, Math.max(0.02, 0.52 - dur));
    }} else {{
      // Ensure every element finishes its entrance well before the 50% layout audit sample
      var latestStart = Math.max(0.10, Math.min(totalDur * 0.42 - dur, totalDur - dur - 0.25));
      startTime = Math.max(0.04, Math.min(startTime, latestStart));
    }}

    verb(tl, el, startTime, dur);

    if (item.emphasis === 'pulse') {{
      var pStart = Math.min(startTime + dur + 0.06, Math.max(0.25, totalDur - 0.36));
      if (pStart + 0.28 < totalDur - 0.04) {{
        tl.to(el, {{ scale: 1.03, duration: 0.14, yoyo: true, repeat: 1, ease: 'sine.inOut' }}, pStart);
      }}
    }} else if (item.emphasis === 'shake') {{
      var sStart = Math.min(startTime + dur + 0.06, Math.max(0.25, totalDur - 0.32));
      if (sStart + 0.24 < totalDur - 0.04) {{
        tl.to(el, {{ x: 3, duration: 0.05, yoyo: true, repeat: 3, ease: 'none' }}, sStart);
      }}
    }}
  }}

  window.__seek = function(t) {{
    tl.seek(t, false);
  }};
  window.__seek(0);
}})();
"""


_EMBEDDED_SPEC_RE = re.compile(
    r'<script[^>]*id=["\']parallax-scene-spec["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)


def extract_embedded_scene_spec(html: str) -> dict[str, Any] | None:
    """Extract the embedded `SceneSpec` JSON from `<script id="parallax-scene-spec">` if present."""
    match = _EMBEDDED_SPEC_RE.search(html or "")
    if not match:
        return None
    try:
        data = json.loads(match.group(1).strip())
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def save_scene_spec(spec: dict[str, Any], out_path: str | Path) -> Path:
    """Save `spec` as `<stem>.spec.json` next to `out_path` (or directly if `out_path` ends with `.spec.json`)."""
    p = Path(out_path).expanduser()
    if not p.is_absolute():
        p = Path.cwd() / p
    if p.name.endswith(".spec.json"):
        target = p
    else:
        target = p.with_suffix(".spec.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return target


def load_scene_spec(path: str | Path, *, style: str | None = None) -> dict[str, Any]:
    """Load and validate a `SceneSpec` JSON file from disk."""
    spec_path = Path(path).expanduser()
    if not spec_path.is_absolute():
        spec_path = Path.cwd() / spec_path
    if not spec_path.is_file():
        raise FileNotFoundError(f"SceneSpec file not found: {spec_path}")
    raw = json.loads(spec_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise SceneSpecValidationError(f"SceneSpec file {spec_path} must contain a JSON object.")
    resolved_style = style or str(raw.get("style") or "flat-explainer")
    return validate_scene_spec(raw, style=resolved_style, strict=False, ensure_brand_header=True)


def render_scene_spec_html(
    spec: dict[str, Any],
    *,
    style: str | None = None,
    preset: dict[str, Any] | None = None,
    brand: Any = None,
    norm_brand: dict[str, Any] | None = None,
    duration_s: float = 5.0,
    words: Any = None,
    fps: int = 30,
) -> str:
    """Deterministically compile a `SceneSpec` into a self-contained 1080x1920 HTML/SVG + GSAP document."""
    from parallax.html_motion import (
        inline_gsap_script,
        load_style_preset,
        normalize_brand,
        normalize_words,
        patch_html_timing,
    )

    resolved_style = str(style or spec.get("style") or (preset or {}).get("name") or "flat-explainer").strip()
    if preset is None:
        preset = load_style_preset(resolved_style)
    if norm_brand is None:
        norm_brand = normalize_brand(brand, preset)

    validated = validate_scene_spec(
        spec,
        style=preset["name"],
        strict=False,
        ensure_brand_header=True,
    )
    norm_words = normalize_words(words)
    dur = max(0.2, float(duration_s or 5.0))

    tokens = resolve_preset_tokens(preset, norm_brand, gap=validated["gap"])
    css_vars = "\n".join(f"  {k}: {v};" for k, v in tokens.items())
    spec_json_inline = json.dumps(validated, ensure_ascii=False)

    anim_items: list[dict[str, Any]] = []
    primitive_html_blocks: list[str] = []
    for idx, prim in enumerate(validated["primitives"]):
        primitive_html_blocks.append(
            _render_primitive_html(
                prim,
                idx_prefix=str(idx),
                norm_brand=norm_brand,
                style_name=preset["name"],
                anim_Collector=anim_items,
            )
        )

    motion_js = _build_motion_runtime_js(anim_items)
    layout_slot = validated["layout_slot"]
    bg_variant = validated["bg_variant"]

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<script id="parallax-scene-spec" type="application/json">{spec_json_inline}</script>
<style>
:root {{
{css_vars}
}}
* {{
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}}
html, body {{
  width: {CANVAS_WIDTH}px;
  height: {CANVAS_HEIGHT}px;
  overflow: hidden;
  background-color: var(--bg);
  color: var(--on-bg);
  font-family: var(--font-body);
  -webkit-font-smoothing: antialiased;
}}
#root {{
  position: relative;
  width: {CANVAS_WIDTH}px;
  height: {CANVAS_HEIGHT}px;
  overflow: hidden;
  background-color: var(--bg);
}}
#root[data-bg-variant="radial"] {{
  background-image: radial-gradient(circle at 50% 34%, var(--surface) 0%, var(--bg) 78%);
}}
#root[data-bg-variant="orb-glow"] {{
  background-image:
    radial-gradient(circle at 50% 24%, var(--accent) 0%, transparent 58%),
    linear-gradient(165deg, var(--bg) 0%, var(--surface) 60%, var(--bg) 100%);
}}
#root[data-bg-variant="grid"] {{
  background-image:
    linear-gradient(to right, rgba(128, 128, 128, 0.08) 1px, transparent 1px),
    linear-gradient(to bottom, rgba(128, 128, 128, 0.08) 1px, transparent 1px);
  background-size: 60px 60px;
}}
#safe-stage {{
  position: absolute;
  left: {STAGE_LEFT}px;
  top: {STAGE_TOP}px;
  width: {STAGE_WIDTH}px;
  height: {STAGE_HEIGHT}px;
  padding: 12px 16px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--stage-gap, 34px);
  overflow: hidden;
}}
#safe-stage.slot-editorial-grid {{
  background-color: var(--surface);
  color: var(--on-surface);
  border: var(--border-card);
  border-radius: var(--radius-card);
  box-shadow: var(--shadow-card);
  padding: 24px 22px;
}}
.primitive {{
  margin-left: auto;
  margin-right: auto;
  max-width: 100%;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
}}
/* BrandHeader */
.brand-header {{
  width: 892px;
  max-width: 100%;
  box-sizing: border-box;
  gap: 16px;
  padding: 10px 18px;
  flex-shrink: 0;
}}
.brand-plate {{
  display: inline-flex;
  flex-direction: row;
  align-items: center;
  justify-content: center;
  gap: 20px;
  padding: var(--pad-brand-plate, 18px 44px);
  border-radius: 999px;
  margin: 0 auto;
  max-width: 100%;
  box-sizing: border-box;
}}
.brand-plate.is-pill {{
  box-shadow: 0 10px 28px rgba(0, 0, 0, 0.18);
  border: 2px solid rgba(128, 128, 128, 0.16);
}}
.brand-logo-wrap {{
  width: 58px;
  height: 58px;
  overflow: hidden;
  display: inline-flex;
  align-items: center;
  justify-content: flex-start;
  flex-shrink: 0;
  border-radius: 14px;
}}
#brand-logo {{
  height: 58px;
  width: auto;
  max-width: none;
  display: block;
}}
.brand-wordmark {{
  font-family: var(--font-heading);
  font-size: 46px;
  font-weight: 800;
  letter-spacing: -0.01em;
  line-height: 1.12;
  white-space: nowrap;
  max-width: 100%;
  overflow-wrap: break-word;
}}
.brand-tag {{
  display: inline-block;
  padding: 16px 34px;
  border-radius: 999px;
  background-color: var(--accent2);
  color: var(--on-accent2);
  font-family: var(--font-heading);
  font-size: 36px;
  font-weight: 800;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  margin: 0 auto;
  max-width: 100%;
  box-sizing: border-box;
  overflow-wrap: break-word;
}}
/* HeadlineBlock */
.headline-block {{
  width: 892px;
  max-width: 100%;
  box-sizing: border-box;
  gap: 16px;
  padding: 18px 26px;
}}
#safe-stage.slot-editorial-grid .headline-block {{
  border-bottom: 1.5px solid rgba(128, 128, 128, 0.22);
  padding-bottom: 24px;
}}
.headline-eyebrow {{
  font-size: 34px;
  font-weight: 800;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--accent);
  width: 100%;
  max-width: 100%;
  box-sizing: border-box;
  overflow-wrap: break-word;
}}
.headline-main {{
  font-family: var(--font-heading);
  font-weight: var(--headline-weight, 800);
  text-transform: var(--headline-case, none);
  letter-spacing: var(--headline-letter-spacing, -0.02em);
  line-height: 1.10;
  color: inherit;
  width: 100%;
  max-width: 100%;
  box-sizing: border-box;
  margin: 0 auto;
  overflow-wrap: break-word;
  word-break: normal;
}}
.headline-block.size-md .headline-main {{ font-size: 78px; }}
.headline-block.size-lg .headline-main {{ font-size: 82px; }}
.headline-block.size-hero .headline-main {{ font-size: 92px; }}
.phone-body .headline-block.size-md .headline-main {{ font-size: 78px; }}
.phone-body .headline-block.size-lg .headline-main {{ font-size: 80px; }}
.phone-body .headline-block.size-hero .headline-main {{ font-size: 90px; }}
.headline-sub {{
  font-size: 38px;
  font-weight: 600;
  line-height: 1.26;
  opacity: 0.92;
  width: 100%;
  max-width: 100%;
  box-sizing: border-box;
  margin: 0 auto;
  overflow-wrap: break-word;
  word-break: normal;
}}
/* KineticWordStack */
.kinetic-stack {{
  width: 900px;
  max-width: 100%;
  box-sizing: border-box;
  gap: 28px;
  padding: 16px 0;
}}
.kinetic-row {{
  font-family: var(--font-heading);
  font-size: 114px;
  font-weight: 900;
  line-height: 1.02;
  letter-spacing: -0.02em;
  text-transform: uppercase;
  text-align: center;
  padding: var(--pad-kinetic-row, 24px 54px);
  border-radius: 32px;
  max-width: 100%;
  width: fit-content;
  box-sizing: border-box;
  margin: 0 auto;
  white-space: nowrap;
  overflow-wrap: break-word;
}}
.kinetic-row.style-plain {{
  background-color: transparent;
  color: var(--on-bg);
}}
.kinetic-row.style-pill-accent {{
  background-color: var(--accent);
  color: var(--on-accent);
  border-radius: var(--radius-pill, 999px);
  box-shadow: var(--shadow-card);
}}
.kinetic-row.style-pill-accent2 {{
  background-color: var(--accent2);
  color: var(--on-accent2);
  border-radius: var(--radius-pill, 999px);
  box-shadow: var(--shadow-card);
}}
.kinetic-row.style-outline {{
  background-color: var(--surface);
  color: var(--on-surface);
  border: 5px solid var(--accent);
  border-radius: 28px;
}}
/* PhoneFrame */
.phone-frame {{
  width: 892px;
  max-width: 100%;
  box-sizing: border-box;
  background-color: var(--surface);
  color: var(--on-surface);
  border: var(--border-phone);
  border-radius: var(--radius-phone);
  box-shadow: var(--shadow-phone);
  backdrop-filter: blur(var(--glass-blur, 0px));
  padding: var(--pad-phone, 42px 50px);
  gap: var(--inner-gap, 26px);
}}
.phone-frame.is-sparse {{
  min-height: 660px;
  justify-content: space-evenly;
  padding: 52px 54px;
  gap: 36px;
}}
.phone-speaker {{
  width: 152px;
  height: 14px;
  border-radius: 999px;
  background-color: currentColor;
  opacity: 0.24;
  margin: 0 auto;
}}
.phone-header-label {{
  font-family: var(--font-heading);
  font-size: 40px;
  font-weight: 800;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  opacity: 0.90;
  max-width: 100%;
  overflow-wrap: break-word;
}}
.phone-frame.is-sparse .phone-header-label {{
  font-size: 42px;
}}
.phone-body {{
  width: 100%;
  max-width: 100%;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--inner-gap, 26px);
}}
.phone-frame.is-sparse .phone-body {{
  flex: 1 1 auto;
  justify-content: space-evenly;
  gap: 42px;
  padding: 14px 0;
}}
.phone-frame.is-sparse .pill-badge {{
  font-size: 42px;
  padding: 30px 58px;
}}
.phone-frame.is-sparse .headline-block .headline-main {{
  font-size: 92px;
  line-height: 1.08;
}}
.phone-frame.is-sparse .headline-sub {{
  font-size: 40px;
}}
.phone-frame.is-sparse .feature-card {{
  padding: 46px 52px;
  gap: 24px;
}}
/* FeatureCard */
.feature-card {{
  width: 892px;
  max-width: 100%;
  box-sizing: border-box;
  padding: var(--pad-card, 39px 46px);
  border: var(--border-card);
  border-radius: var(--radius-card);
  box-shadow: var(--shadow-card);
  backdrop-filter: blur(var(--glass-blur, 0px));
  gap: 22px;
}}
#safe-stage > .feature-card {{
  width: 892px;
  max-width: 100%;
}}
.feature-card.tilt-left,
.feature-card.tilt-right {{
  transform: none;
}}
.card-icon-wrap {{
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 6px;
}}
.card-title {{
  font-family: var(--font-heading);
  font-size: 48px;
  font-weight: var(--headline-weight, 800);
  line-height: 1.14;
  text-align: center;
  width: 100%;
  max-width: 100%;
  box-sizing: border-box;
  overflow-wrap: break-word;
  word-break: normal;
}}
.card-sub {{
  font-size: 38px;
  font-weight: 600;
  line-height: 1.26;
  opacity: 0.92;
  text-align: center;
  width: 100%;
  max-width: 100%;
  box-sizing: border-box;
  overflow-wrap: break-word;
  word-break: normal;
}}
/* PillBadge */
.pill-badge {{
  display: inline-flex;
  flex-direction: row;
  align-items: center;
  justify-content: center;
  gap: 20px;
  padding: var(--pad-pill, 26px 50px);
  border-radius: var(--radius-pill, 999px);
  border: var(--border-card);
  box-shadow: var(--shadow-card);
  font-family: var(--font-heading);
  font-size: 38px;
  font-weight: 800;
  width: fit-content;
  max-width: 100%;
  box-sizing: border-box;
}}
.pill-label {{
  min-width: 0;
  max-width: 100%;
  overflow-wrap: break-word;
  word-break: normal;
}}
/* IconBadge */
.icon-badge {{
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 150px;
  height: 150px;
  min-width: 144px;
  min-height: 144px;
  align-self: center;
  aspect-ratio: 1 / 1;
  padding: var(--pad-badge, 28px);
  border-radius: 36px;
  border: var(--border-card);
  box-shadow: var(--shadow-card);
  max-width: 100%;
  box-sizing: border-box;
}}
.icon-badge.is-hero-badge,
.phone-frame.is-sparse .icon-badge {{
  width: 168px;
  height: 168px;
  min-width: 156px;
  min-height: 156px;
  padding: 34px;
  border-radius: 40px;
}}
/* StatCallout */
.stat-callout {{
  width: 892px;
  max-width: 100%;
  box-sizing: border-box;
  padding: var(--pad-stat, 35px 46px);
  border: var(--border-card);
  border-radius: var(--radius-card);
  box-shadow: var(--shadow-card);
  gap: 20px;
}}
#safe-stage > .stat-callout {{
  width: 892px;
  max-width: 100%;
}}
.stat-arc-wrap {{
  position: relative;
  width: 196px;
  height: 196px;
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0 auto;
}}
.stat-arc-svg {{
  width: 196px;
  height: 196px;
  display: block;
}}
.stat-arc-value {{
  position: absolute;
  inset: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-family: var(--font-heading);
  font-size: 38px;
  font-weight: 900;
  line-height: 1.05;
  text-align: center;
  font-variant-numeric: tabular-nums;
}}
.stat-main {{
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 14px;
  width: 100%;
  max-width: 100%;
  box-sizing: border-box;
}}
.stat-value-row {{
  display: flex;
  flex-direction: row;
  align-items: center;
  justify-content: center;
  gap: 20px;
  flex-wrap: wrap;
  width: 100%;
  max-width: 100%;
}}
.stat-value {{
  font-family: var(--font-heading);
  font-size: 64px;
  font-weight: 900;
  line-height: 1.08;
  font-variant-numeric: tabular-nums;
  max-width: 100%;
  overflow-wrap: break-word;
  word-break: normal;
}}
.stat-delta {{
  padding: 16px 30px;
  border-radius: 999px;
  background-color: var(--accent);
  color: var(--on-accent);
  font-size: 36px;
  font-weight: 800;
  text-transform: uppercase;
  max-width: 100%;
  overflow-wrap: break-word;
  word-break: normal;
}}
.stat-label {{
  font-size: 36px;
  font-weight: 700;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  opacity: 0.90;
  width: 100%;
  max-width: 100%;
  overflow-wrap: break-word;
  word-break: normal;
}}
.stat-bar-track {{
  width: 100%;
  max-width: 100%;
  height: 18px;
  border-radius: 999px;
  background-color: rgba(128, 128, 128, 0.22);
  overflow: hidden;
}}
.stat-bar-fill {{
  height: 100%;
  border-radius: 999px;
  background-color: var(--accent);
}}
/* ChecklistStack */
.checklist-stack {{
  width: 892px;
  max-width: 100%;
  box-sizing: border-box;
  padding: var(--pad-checklist, 32px 48px);
  border: var(--border-card);
  border-radius: var(--radius-card);
  box-shadow: var(--shadow-card);
  gap: 20px;
}}
.checklist-item {{
  width: 100%;
  max-width: 100%;
  box-sizing: border-box;
  display: flex;
  flex-direction: row;
  align-items: center;
  justify-content: center;
  gap: 20px;
  padding: 16px 22px;
  border-bottom: 1px solid rgba(128, 128, 128, 0.18);
  font-size: 38px;
  font-weight: 700;
}}
.checklist-item:last-child {{
  border-bottom: none;
}}
.checklist-icon {{
  color: var(--accent);
  display: inline-flex;
  align-items: center;
  flex-shrink: 0;
}}
.checklist-label {{
  min-width: 0;
  max-width: 100%;
  overflow-wrap: break-word;
  word-break: normal;
}}
/* CtaButton */
.cta-button {{
  width: 820px;
  max-width: 100%;
  box-sizing: border-box;
  min-height: 112px;
  padding: var(--pad-button, 32px 62px);
  flex-direction: row;
  gap: 22px;
  border-radius: var(--radius-pill, 999px);
  border: var(--border-card);
  box-shadow: var(--shadow-card);
  font-family: var(--font-heading);
  font-size: 46px;
  font-weight: 800;
  flex-shrink: 0;
}}
.cta-label {{
  min-width: 0;
  max-width: 100%;
  overflow-wrap: break-word;
  word-break: normal;
}}
/* LayoutSlot */
.layout-slot {{
  width: 892px;
  max-width: 100%;
  box-sizing: border-box;
  gap: var(--inner-gap, 26px);
}}
.layout-slot.dir-row,
.layout-slot.dir-grid-2col {{
  flex-direction: row;
  flex-wrap: wrap;
}}
/* Nested primitive constraints: never exceed parent content box */
.phone-body .primitive:not(.icon-badge):not(.pill-badge),
.layout-slot .primitive:not(.icon-badge):not(.pill-badge) {{
  width: 100%;
  max-width: 100%;
  box-sizing: border-box;
}}
.phone-body .pill-badge,
.layout-slot .pill-badge {{
  width: fit-content;
  max-width: 100%;
  box-sizing: border-box;
}}
.phone-body .headline-block,
.phone-body .headline-main,
.phone-body .headline-sub,
.phone-body .headline-eyebrow,
.layout-slot .headline-block,
.layout-slot .headline-main,
.layout-slot .headline-sub,
.layout-slot .headline-eyebrow {{
  width: 100%;
  max-width: 100%;
  box-sizing: border-box;
}}
.prim-icon .svg-draw-path {{
  stroke-dasharray: 240;
  stroke-dashoffset: 0;
}}
</style>
<script>
/* __PARALLAX_GSAP_SCRIPT__ */
</script>
<script>
/* __PARALLAX_TIMING_BLOCK__ */
</script>
</head>
<body>
<div id="root" data-style="{_escape_html(preset["name"])}" data-bg-variant="{_escape_html(bg_variant)}">
  <main id="safe-stage" class="slot-{_escape_html(layout_slot)} gap-{_escape_html(validated["gap"])}" data-layout-slot="{_escape_html(layout_slot)}">
    {"".join(primitive_html_blocks)}
  </main>
</div>
<script>
{motion_js}
</script>
</body>
</html>"""

    html = inline_gsap_script(html)
    html = patch_html_timing(html, duration=dur, fps=fps, words=norm_words)
    return html
