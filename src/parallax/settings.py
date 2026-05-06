"""Frozen `Settings` dataclass — the resolved-once view of a plan + folder.

`run_plan` used to read ~30 plan keys at the top of its body, then pass
them downstream as positional args. That made every stage callable
unwieldy, signatures churn-prone, and stage-by-stage testing impossible
without re-deriving every field.

`resolve_settings(plan, folder) -> Settings` does the resolution exactly
once at the entry point. Stages take `settings` and read the fields they
need; the dataclass is frozen so nobody can mutate it mid-pipeline.

Mode threading and event-emitter / cost-session injection arrive in
later sub-deliverables of Phase 1.1; the dataclass is structured to grow
those fields without touching call sites.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .plan import Plan

from .ffmpeg_utils import parse_resolution, probe_resolution

# Aspect ratio is the user-facing knob. When `resolution:` is unset on the
# plan and there are no clips to probe, the resolution is derived from this
# table so the output matches the chosen aspect at a sensible default size.
_ASPECT_TO_RESOLUTION: dict[str, str] = {
    "9:16": "720x1280",
    "16:9": "1920x1080",
    "1:1":  "1080x1080",
    "4:3":  "1080x810",
    "3:4":  "810x1080",
}

# Default video-generation resolution by aspect. Cheaper than the output
# resolution — clips are upscaled to `resolution` during ffmpeg assembly.
# Seedance 2.0 Fast: 480p = $0.054/s, 720p = $0.121/s, 1080p = $0.272/s.
_ASPECT_TO_ANIMATE_RESOLUTION: dict[str, str] = {
    "9:16": "480x854",
    "16:9": "854x480",
    "1:1":  "480x480",
    "4:3":  "640x480",
    "3:4":  "480x640",
}

VALID_ASPECTS: frozenset[str] = frozenset(_ASPECT_TO_RESOLUTION.keys())


# Event-emitter signature: (event_name, fields_dict) -> None.
# `event_name` is a short stable identifier (e.g. "stage.stills.start"
# or, for free-form progress, "log"). `fields` carries arbitrary
# structured data — including a `msg` key for human-readable strings.
EventEmitter = Callable[[str, dict[str, Any]], None]


def _default_emitter(event: str, fields: dict[str, Any]) -> None:
    """Print emitter — matches the legacy `==> {msg}` format.

    Used when nothing else is injected (the production CLI path). For
    structured/test contexts (verify suite, unit tests), pass a custom
    callable into `Settings.events`.
    """
    msg = fields.get("msg")
    if msg is not None:
        print(f"==> {msg}", flush=True)
    else:
        print(f"==> {event} {fields}", flush=True)


class UsageSession:
    """Per-run cost aggregator backed by the global usage NDJSON log.

    Bound late to a `run_id` (because `runlog.start_run()` is called
    after `resolve_settings`). `total_cost_usd` queries the existing
    global log filtered by run_id, so the global sink keeps working
    unchanged — this class is purely a typed accessor for stage code
    and `expected.yaml` cost assertions in verify suite.
    """

    def __init__(self) -> None:
        self._run_id: str | None = None

    def bind(self, run_id: str) -> None:
        """Bind this session to a run_id. Called once per produce run."""
        self._run_id = run_id

    @property
    def run_id(self) -> str | None:
        return self._run_id

    @property
    def total_cost_usd(self) -> float:
        """Sum of `cost_usd` across every usage record for this run."""
        if self._run_id is None:
            return 0.0
        from . import usage as _usage
        return _usage.run_total(self._run_id)


class ProductionMode(Enum):
    """Pipeline execution mode.

    REAL: full external API calls (image gen, TTS, animate, etc.).
    TEST: deterministic stubs via parallax.shim — no network, no spend.

    Resolved once at `resolve_settings` time from the
    `PARALLAX_TEST_MODE` env var. Stages should read `settings.mode`
    instead of touching `os.environ` directly so a single process can
    run multiple modes in sequence (e.g. verify suite).
    """

    REAL = "real"
    TEST = "test"


def _resolve_mode(mode_override: "ProductionMode | None" = None) -> ProductionMode:
    if mode_override is not None:
        return mode_override
    raw = os.environ.get("PARALLAX_TEST_MODE", "").lower()
    return ProductionMode.TEST if raw in ("1", "true", "yes") else ProductionMode.REAL


@dataclass(frozen=True)
class Settings:
    """Resolved plan + folder configuration. Frozen by design.

    Stages should read what they need and never mutate. Anything that
    legitimately varies during a run (timing overrides, locked asset
    paths) lives on the in-flight `plan` dict and the per-scene entries,
    not here.
    """

    # Filesystem
    folder: Path
    plan_path: Path
    concept_prefix: str  # e.g. "0004_" or ""

    # Image / video
    image_model: str
    video_model: str
    aspect: str  # e.g. "9:16", "16:9", "1:1", "4:3", "3:4"
    resolution: str          # final output resolution (e.g. "1080x1920")
    animate_resolution: str  # video-gen resolution — upscaled to resolution: at assembly (e.g. "480x854")
    video_width: int
    video_height: int
    res_scale: float

    # Voice
    voice: str
    voice_model: str
    voice_speed: float
    style: str | None
    style_hint: str | None

    # Captions
    caption_style: str | dict[str, Any]
    fontsize: int
    words_per_chunk: int | str
    caption_animation_override: Any
    caption_shift_s: float
    skip_captions: bool

    # Headline
    headline: str | None
    headline_fontsize: int | None
    headline_bg: str | None
    headline_color: str | None

    # Character / avatar / product
    character_image: str | None
    product_image: str | None
    avatar_cfg: dict[str, Any] | None

    # Pipeline behaviour
    stills_only: bool
    trim_pauses: bool | float = True
    mode: ProductionMode = ProductionMode.REAL
    events: EventEmitter = field(default=_default_emitter)
    usage: UsageSession = field(default_factory=UsageSession)
    titles_cfg: list[dict[str, Any]] = field(default_factory=list)
    # Run identity — set late by `produce.run_plan` after `runlog.start_run()`.
    # Threaded through so stages can derive deterministic, traceable artifact
    # names (e.g. `<folder>-vN-<short_id>.mp4`).
    run_id: str | None = None


def _infer_project_resolution(plan: dict, folder: Path, aspect: str = "9:16") -> str:
    """Pick an output resolution when the plan doesn't specify one.

    Probes every scene's `clip_path` (when present) and returns the
    largest width×height seen — so a project's downstream stages match
    the source video's natural resolution rather than upscaling. When no
    probeable clips exist, falls back to the resolution paired with
    `aspect` in `_ASPECT_TO_RESOLUTION`.
    """
    best_w, best_h = 0, 0
    for scene in plan.get("scenes", []) or []:
        cp = scene.get("clip_path")
        if not cp:
            continue
        path = Path(cp)
        if not path.is_absolute():
            path = (folder / cp).resolve()
        if not path.exists():
            continue
        dims = probe_resolution(path)
        if dims is None:
            continue
        w, h = dims
        if w * h > best_w * best_h:
            best_w, best_h = w, h
    if best_w and best_h:
        return f"{best_w}x{best_h}"
    if aspect not in _ASPECT_TO_RESOLUTION:
        raise ValueError(
            f"_infer_project_resolution: unknown aspect {aspect!r}; "
            f"valid choices: {sorted(_ASPECT_TO_RESOLUTION)}"
        )
    return _ASPECT_TO_RESOLUTION[aspect]


def with_run_id(settings: "Settings", run_id: str) -> "Settings":
    """Return a copy of `settings` with `run_id` populated.

    `Settings` is frozen by design, so callers swap the whole object once
    `runlog.start_run()` returns the run id.
    """
    from dataclasses import replace
    return replace(settings, run_id=run_id)


def resolve_settings(
    plan: "Plan | dict[str, Any]",
    folder: Path,
    plan_path: Path,
    mode: "ProductionMode | None" = None,
) -> Settings:
    """Resolve a plan + folder into a frozen `Settings` snapshot.

    Accepts either a validated `Plan` Pydantic model or a raw dict (for
    legacy call sites such as `test_scene`). When a `Plan` is given, fields
    are read directly from the typed model; dict-shaped reads are preserved
    for backward compatibility with the raw dict path.

    Raises FileNotFoundError if `character_image` is set but missing.
    """
    from .plan import Plan as _Plan  # local import avoids circular at module level

    folder = Path(folder).expanduser().resolve()
    plan_path = Path(plan_path).expanduser().resolve()

    id_match = re.match(r"^(\d{4})", folder.name)
    concept_prefix = f"{id_match.group(1)}_" if id_match else ""

    if isinstance(plan, _Plan):
        return _resolve_settings_from_plan(plan, folder, plan_path, concept_prefix, mode=mode)
    return _resolve_settings_from_dict(plan, folder, plan_path, concept_prefix, mode=mode)


def _resolve_settings_from_plan(
    plan: "Plan",
    folder: Path,
    plan_path: Path,
    concept_prefix: str,
    mode: "ProductionMode | None" = None,
) -> Settings:
    """Read settings directly from a validated Plan model."""
    from .plan import Plan as _Plan  # keep import local

    aspect = plan.aspect
    resolution = plan.resolution or _infer_project_resolution(plan.model_dump(mode="python", exclude_none=True), folder, aspect)
    video_width, video_height = parse_resolution(resolution)
    res_scale = video_width / 1080

    animate_resolution = plan.animate_resolution or _ASPECT_TO_ANIMATE_RESOLUTION.get(aspect, "480x854")
    fontsize = max(12, int(plan.fontsize * res_scale))
    wpc_raw = plan.words_per_chunk
    words_per_chunk: int | str = wpc_raw if isinstance(wpc_raw, str) else int(wpc_raw)
    skip_captions = str(plan.captions or "").lower() == "skip"

    char_image_raw = plan.character_image
    character_image: str | None = None
    if char_image_raw:
        p = Path(char_image_raw)
        resolved = p if p.is_absolute() else (folder / p)
        if not resolved.is_file():
            variants = sorted(resolved.parent.glob(f"{resolved.stem}_a*x*.png"))
            if variants:
                resolved = variants[0]
            else:
                raise FileNotFoundError(f"character_image not found: {resolved}")
        character_image = str(resolved)

    prod_image_raw = plan.product_image
    product_image: str | None = None
    if prod_image_raw:
        p = Path(prod_image_raw)
        resolved = p if p.is_absolute() else (folder / p)
        if resolved.is_file():
            product_image = str(resolved)

    avatar_cfg: dict[str, Any] | None = None
    if plan.avatar is not None:
        avatar_cfg = plan.avatar.model_dump(mode="python", exclude_none=True)

    titles_cfg = plan.titles or []

    return Settings(
        folder=folder,
        plan_path=plan_path,
        concept_prefix=concept_prefix,
        image_model=plan.image_model,
        video_model=plan.video_model,
        aspect=aspect,
        resolution=resolution,
        animate_resolution=animate_resolution,
        video_width=video_width,
        video_height=video_height,
        res_scale=res_scale,
        voice=plan.voice,
        voice_model=plan.voice_model,
        voice_speed=plan.voice_speed,
        style=plan.style,
        style_hint=plan.style_hint,
        caption_style=plan.caption_style,
        fontsize=fontsize,
        words_per_chunk=words_per_chunk,
        caption_animation_override=plan.caption_animation,
        caption_shift_s=plan.caption_shift_s,
        skip_captions=skip_captions,
        headline=plan.headline,
        headline_fontsize=plan.headline_fontsize,
        headline_bg=plan.headline_bg,
        headline_color=plan.headline_color,
        character_image=character_image,
        product_image=product_image,
        avatar_cfg=avatar_cfg,
        stills_only=plan.stills_only,
        trim_pauses=plan.trim_pauses,
        mode=_resolve_mode(mode),
        titles_cfg=titles_cfg,
    )


def _resolve_settings_from_dict(
    plan: dict[str, Any],
    folder: Path,
    plan_path: Path,
    concept_prefix: str,
    mode: "ProductionMode | None" = None,
) -> Settings:
    """Legacy dict path — used by test_scene and any caller that hasn't migrated to Plan."""
    image_model = plan.get("image_model", "mid")
    video_model = plan.get("video_model", "mid")
    voice = plan.get("voice", "nova")
    voice_model = plan.get("voice_model", "tts-mini")
    # TTS pacing is controlled via `style` (e.g. rapid_fire); leave the
    # atempo speed knob neutral by default. Plans can still override.
    voice_speed = float(plan.get("voice_speed", 1.0))
    style = plan.get("style")
    style_hint = plan.get("style_hint")

    aspect = plan.get("aspect", "9:16")
    # YAML sexagesimal trap: unquoted `aspect: 9:16` parses as 556 (9*60+16).
    # Catch the int form and emit an actionable hint rather than the raw value.
    if isinstance(aspect, int):
        raise ValueError(
            f"plan.aspect parsed as int {aspect!r} — quote the value in YAML "
            f"(e.g. `aspect: \"9:16\"`). Choices: {sorted(VALID_ASPECTS)}"
        )
    if aspect not in VALID_ASPECTS:
        raise ValueError(
            f"plan.aspect={aspect!r} is not a supported aspect ratio. "
            f"Choices: {sorted(VALID_ASPECTS)}"
        )

    resolution = plan.get("resolution") or _infer_project_resolution(plan, folder, aspect)
    video_width, video_height = parse_resolution(resolution)
    res_scale = video_width / 1080  # font sizes scale to output width

    # animate_resolution: resolution passed to the video-gen model. Defaults to
    # 480p for the plan's aspect — cheaper than the output resolution and
    # upscaled by ffmpeg during assembly. Set explicitly in the plan to override.
    animate_resolution = (
        plan.get("animate_resolution")
        or _ASPECT_TO_ANIMATE_RESOLUTION.get(aspect, "480x854")
    )

    caption_style = plan.get("caption_style", "bangers")
    fontsize = max(12, int(plan.get("fontsize", 55) * res_scale))
    wpc_raw = plan.get("words_per_chunk", "smart")
    words_per_chunk: int | str = wpc_raw if isinstance(wpc_raw, str) else int(wpc_raw)
    caption_animation_override = plan.get("caption_animation")
    caption_shift_s = float(plan.get("caption_shift_s", 0.0))
    skip_captions = str(plan.get("captions", "")).lower() == "skip"

    headline = plan.get("headline")
    headline_fontsize = plan.get("headline_fontsize")
    headline_bg = plan.get("headline_bg")
    headline_color = plan.get("headline_color")

    char_image_raw = plan.get("character_image")
    character_image: str | None = None
    if char_image_raw:
        p = Path(char_image_raw)
        resolved = p if p.is_absolute() else (folder / p)
        if not resolved.is_file():
            # The original may have been deleted after crop_to_aspect wrote the
            # _aWxH variant. Recover by matching any sibling with that suffix.
            variants = sorted(resolved.parent.glob(f"{resolved.stem}_a*x*.png"))
            if variants:
                resolved = variants[0]
            else:
                raise FileNotFoundError(f"character_image not found: {resolved}")
        character_image = str(resolved)

    prod_image_raw = plan.get("product_image")
    product_image: str | None = None
    if prod_image_raw:
        p = Path(prod_image_raw)
        resolved = p if p.is_absolute() else (folder / p)
        if resolved.is_file():
            product_image = str(resolved)

    return Settings(
        folder=folder,
        plan_path=plan_path,
        concept_prefix=concept_prefix,
        image_model=image_model,
        video_model=video_model,
        aspect=aspect,
        resolution=resolution,
        animate_resolution=animate_resolution,
        video_width=video_width,
        video_height=video_height,
        res_scale=res_scale,
        voice=voice,
        voice_model=voice_model,
        voice_speed=voice_speed,
        style=style,
        style_hint=style_hint,
        caption_style=caption_style,
        fontsize=fontsize,
        words_per_chunk=words_per_chunk,
        caption_animation_override=caption_animation_override,
        caption_shift_s=caption_shift_s,
        skip_captions=skip_captions,
        headline=headline,
        headline_fontsize=headline_fontsize,
        headline_bg=headline_bg,
        headline_color=headline_color,
        character_image=character_image,
        product_image=product_image,
        avatar_cfg=plan.get("avatar"),
        stills_only=bool(plan.get("stills_only", False)),
        trim_pauses=plan.get("trim_pauses", True),
        mode=_resolve_mode(mode),
        titles_cfg=plan.get("titles", []) or [],
    )
