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
from typing import Any

from .ffmpeg_utils import parse_resolution

_DEFAULT_RESOLUTION = "720x1280"


# Event-emitter signature: (event_name, fields_dict) -> None.
# `event_name` is a short stable identifier (e.g. "stage.stills.start"
# or, for free-form progress, "log"). `fields` carries arbitrary
# structured data — including a `msg` key for human-readable strings.
EventEmitter = Callable[[str, dict[str, Any]], None]


def _default_emitter(event: str, fields: dict[str, Any]) -> None:
    """Print emitter — matches the legacy `==> {msg}` format.

    Used when nothing else is injected (the production CLI path). For
    structured/test contexts (verify-suite, unit tests), pass a custom
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
    and `expected.yaml` cost assertions in verify-suite.
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
    run multiple modes in sequence (e.g. verify-suite).
    """

    REAL = "real"
    TEST = "test"


def _resolve_mode() -> ProductionMode:
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
    model: str
    resolution: str
    video_width: int
    video_height: int
    res_scale: float

    # Voice
    voice: str
    speed: float
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

    # Character / avatar
    character_image: str | None
    avatar_cfg: dict[str, Any] | None

    # Pipeline behaviour
    stills_only: bool
    mode: ProductionMode = ProductionMode.REAL
    events: EventEmitter = field(default=_default_emitter)
    usage: UsageSession = field(default_factory=UsageSession)
    titles_cfg: list[dict[str, Any]] = field(default_factory=list)


def _infer_project_resolution(plan: dict, folder: Path) -> str:
    """Pick an output resolution when the plan doesn't specify one.

    Probes every scene's `clip_path` (when present) and returns the
    largest width×height seen — so a project's downstream stages match
    the source video's natural resolution rather than upscaling. Falls
    back to 720x1280 (vertical) when no probeable clips exist.
    """
    import subprocess

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
        try:
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height",
                 "-of", "csv=p=0", str(path)],
                capture_output=True, text=True, check=True,
            )
            w_str, h_str = probe.stdout.strip().split(",")
            w, h = int(w_str), int(h_str)
        except Exception:
            continue
        if w * h > best_w * best_h:
            best_w, best_h = w, h
    if best_w and best_h:
        return f"{best_w}x{best_h}"
    return _DEFAULT_RESOLUTION


def resolve_settings(plan: dict[str, Any], folder: Path, plan_path: Path) -> Settings:
    """Resolve a plan + folder into a frozen `Settings` snapshot.

    Reads ~30 plan keys, applies defaults, and computes derived fields
    (resolution scaling, character_image absolute path, concept prefix).
    Raises FileNotFoundError if `character_image` is set but missing.
    """
    folder = Path(folder).expanduser().resolve()
    plan_path = Path(plan_path).expanduser().resolve()

    id_match = re.match(r"^(\d{4})", folder.name)
    concept_prefix = f"{id_match.group(1)}_" if id_match else ""

    model = plan.get("model", "mid")
    voice = plan.get("voice", "Kore")
    # ElevenLabs path defaults to 1.1 atempo; Gemini path stays at 1.0
    # (pacing is controlled via `style`). Plan can override either.
    default_speed = 1.1 if str(voice).startswith("eleven:") else 1.0
    speed = float(plan.get("speed", default_speed))
    style = plan.get("style")
    style_hint = plan.get("style_hint")

    resolution = plan.get("resolution") or _infer_project_resolution(plan, folder)
    video_width, video_height = parse_resolution(resolution)
    res_scale = video_width / 1080  # font sizes scale to output width

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

    return Settings(
        folder=folder,
        plan_path=plan_path,
        concept_prefix=concept_prefix,
        model=model,
        resolution=resolution,
        video_width=video_width,
        video_height=video_height,
        res_scale=res_scale,
        voice=voice,
        speed=speed,
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
        avatar_cfg=plan.get("avatar"),
        stills_only=bool(plan.get("stills_only", False)),
        mode=_resolve_mode(),
        titles_cfg=plan.get("titles", []) or [],
    )
