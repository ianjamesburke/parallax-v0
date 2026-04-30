"""plan.yaml — single load-time validator (mirrors `Brief`).

The plan is the engine-facing iteration artifact. It carries fully
resolved per-scene prompts, locked asset paths, model picks, and the
production-time knobs (caption style, headline, avatar, etc.). It is
authored either by hand or by `parallax plan` from a brief.

This module declares the strict (`extra="forbid"`) Pydantic v2 schema
for the YAML file. `produce.run_plan` calls `Plan.from_yaml(path)`
exactly once, then `.to_dict()` to hand a plain dict to the rest of
the pipeline — so the runtime stays dict-shaped (call sites unchanged)
while load-time validation catches typos, renamed fields, and unknown
keys with a clear error.

Field-rename guard: the v3 cleanup pass renamed:
  - `model:`         -> `image_model:`
  - `animate_model:` -> `video_model:`
and added a new `voice_model:` (default `tts-mini`). A YAML using the
old names is rejected at load with a "rename to <new>" message; old
names do NOT silently work.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_ALLOWED_ASPECTS = ("9:16", "16:9", "1:1", "4:3", "3:4")


# Mapping of removed/renamed field names to their replacement. Used by both
# the top-level model and per-scene model so the same migration message can
# be emitted from either layer.
_RENAMED_PLAN_FIELDS: dict[str, str] = {
    "model": "image_model",
    "animate_model": "video_model",
    "speed": "voice_speed",
}


def _check_renamed(values: dict[str, Any], scope: str) -> None:
    """Raise ValueError for any old field name still present in `values`.

    `scope` is "plan" or "scene" — only used to make the error read well.
    Keys are removed from `values` before raising so the caller can see
    them all at once if multiple were used.
    """
    offenders = [k for k in _RENAMED_PLAN_FIELDS if k in values]
    if offenders:
        msg_parts = [
            f"  - rename `{old}:` to `{_RENAMED_PLAN_FIELDS[old]}:`"
            for old in offenders
        ]
        raise ValueError(
            f"{scope} uses removed field name(s):\n" + "\n".join(msg_parts)
        )


class Avatar(BaseModel):
    """Avatar overlay block — optional. Mirrors the keys read in
    `stages.stage_avatar`. Avatar generation was removed in Phase 1.2;
    `avatar_track` (a pre-recorded clip) is required when an avatar block
    is present at all.
    """
    model_config = ConfigDict(extra="forbid")

    avatar_track: str
    avatar_track_keyed: str | None = None
    track_start_s: float = 0.0
    position: str = "bottom_left"
    size: float = 0.40
    chroma_key: str | None = None
    chroma_similarity: float = 0.30
    chroma_blend: float = 0.03
    y_offset_pct: float | None = None
    crop_px: int = 0


class PlanScene(BaseModel):
    """One scene as authored in the plan. Per-scene `image_model`,
    `video_model`, and `voice_model` overrides win over the plan-level
    defaults.
    """
    model_config = ConfigDict(extra="forbid")

    index: int
    shot_type: Literal["character", "broll", "screen"] = "broll"
    vo_text: str = ""
    prompt: str = ""

    # Asset locks
    still_path: str | None = None
    clip_path: str | None = None
    end_frame_path: str | None = None
    reference: bool | None = None
    reference_images: list[str] | None = None
    video_references: list[str] | None = None

    # Animate
    animate: bool = False
    motion_prompt: str | None = None
    animate_resolution: str | None = None

    # Per-scene aspect override
    aspect: str | None = None

    # Per-scene model overrides
    image_model: str | None = None
    video_model: str | None = None
    voice_model: str | None = None
    # Per-scene speed override; must agree across all overriding scenes.
    voice_speed: float | None = None

    # Timing overrides
    duration_s: float | None = None
    start_offset_s: float | None = None
    fade_in_s: float | None = None
    fade_out_s: float | None = None

    # Ken Burns / zoom
    zoom_direction: str | None = None
    zoom_amount: float | None = None

    @model_validator(mode="before")
    @classmethod
    def _reject_renamed_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            _check_renamed(data, scope=f"scene {data.get('index', '?')}")
        return data

    @field_validator("aspect")
    @classmethod
    def _check_aspect(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if v not in _ALLOWED_ASPECTS:
            raise ValueError(
                f"scene aspect must be one of {_ALLOWED_ASPECTS}, got {v!r}"
            )
        return v


class Plan(BaseModel):
    """Top-level plan.yaml schema."""
    model_config = ConfigDict(extra="forbid")

    # Aspect / resolution
    aspect: Literal["9:16", "16:9", "1:1", "4:3", "3:4"] = "9:16"
    resolution: str | None = None

    # Voice
    voice: str = "Kore"
    voice_speed: float = 1.0
    voice_model: str = "tts-mini"
    style: str | None = None
    style_hint: str | None = None

    # Models
    image_model: str = "mid"
    video_model: str = "mid"

    # Captions
    caption_style: str | dict[str, Any] = "bangers"
    fontsize: int = 55
    words_per_chunk: int | str = "smart"
    captions: str | None = None  # "skip" disables; anything else enables
    caption_animation: Any | None = None
    caption_shift_s: float = 0.0

    # Headline
    headline: str | None = None
    headline_fontsize: int | None = None
    headline_bg: str | None = None
    headline_color: str | None = None

    # Character / avatar
    character_image: str | None = None
    avatar: Avatar | None = None

    # Voiceover audio locks
    audio_path: str | None = None
    words_path: str | None = None

    # Pipeline behavior
    stills_only: bool = False
    titles: list[dict[str, Any]] | None = None

    # Scenes
    scenes: list[PlanScene]

    @model_validator(mode="before")
    @classmethod
    def _reject_renamed_top_level(cls, data: Any) -> Any:
        if isinstance(data, dict):
            _check_renamed(data, scope="plan")
        return data

    # ---------------------------------------------------------------- I/O

    @classmethod
    def from_yaml(cls, path: str | Path) -> "Plan":
        """Load and validate a plan.yaml file. Raises on schema errors."""
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"plan not found: {p}")
        with p.open("r", encoding="utf-8") as fp:
            data = yaml.safe_load(fp) or {}
        if not isinstance(data, dict):
            raise ValueError(
                f"plan.yaml must be a mapping at the top level, got {type(data).__name__}"
            )
        return cls.model_validate(data)

    def to_dict(self) -> dict:
        """Round-trippable dict — what the rest of the pipeline consumes.

        `model_dump(exclude_none=True)` keeps the dict shape close to the
        original plan dict so downstream `plan.get(...)` calls behave the
        same as before validation was added.
        """
        return self.model_dump(mode="python", exclude_none=True)
