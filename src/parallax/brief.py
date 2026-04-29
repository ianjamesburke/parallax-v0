"""brief.yaml — first-class CLI primitive for video specs.

A brief describes WHAT to make ("goal", "aspect", "voice") and what raw
assets are required to make it, without committing to per-scene prompts
or model picks. `parallax plan --folder ...` consumes a brief and emits
a fully-resolved `plan.yaml` with prompts, locked references, and
generation steps. `parallax produce --brief ...` is a shortcut that
plans + produces in one shot.

The Brief is the iteration artifact for the human + agent. Plan.yaml
remains the iteration artifact for the engine. Both are YAML.

Schema (informal):

    goal: "Promote the new Lion energy drink"
    aspect: 9:16          # 9:16 | 16:9 | 1:1 | 4:3 | 3:4
    voice: Kore           # Gemini TTS voice name
    voice_speed: 1.0
    success_criteria:
      - "Hook lands in <2s"
      - "Product visible in scene 1"

    assets:
      provided:
        - path: brand/logo.png
          kind: product_ref
          description: "Lion can"
        - path: brand/founder.png
          kind: character_ref
      generated:                # optional inventory; the planner uses
        - kind: still            # this to decide what to call models for
          scene_index: 0
        - kind: video_clip
          scene_index: 1

    script:
      scenes:
        - index: 0
          shot_type: character
          vo_text: "Lions don't apologize."
          prompt: "Founder holding the can in golden hour..."
        - index: 1
          shot_type: broll
          animate: true
          vo_text: "..."
          prompt: "..."
          motion_prompt: "Slow zoom on..."

Provided assets must exist on disk relative to the project folder;
`Brief.validate_assets(folder)` enforces that before planning starts.
Generated assets are an inventory hint to the planner — they're allowed
to be missing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


_ALLOWED_ASPECTS = ("9:16", "16:9", "1:1", "4:3", "3:4")


class ProvidedAsset(BaseModel):
    """A file the user supplies. Must exist on disk before planning runs."""
    model_config = ConfigDict(extra="forbid")

    path: str
    kind: Literal["style_ref", "product_ref", "character_ref"]
    description: str | None = None


class GeneratedAsset(BaseModel):
    """An asset the pipeline will produce. Optional inventory hint."""
    model_config = ConfigDict(extra="forbid")

    kind: Literal["still", "video_clip", "voiceover"]
    scene_index: int | None = None


class Assets(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provided: list[ProvidedAsset] = Field(default_factory=list)
    generated: list[GeneratedAsset] = Field(default_factory=list)


class BriefScene(BaseModel):
    """One scene as authored in the brief. The planner expands these into
    full plan-scene records (model alias, locked still paths, etc.)."""
    model_config = ConfigDict(extra="forbid")

    index: int
    shot_type: Literal["character", "broll", "screen"] = "broll"
    vo_text: str
    prompt: str
    animate: bool = False
    motion_prompt: str | None = None
    aspect: str | None = None  # per-scene override; defaults to brief.aspect

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


class Script(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenes: list[BriefScene]


class Brief(BaseModel):
    """Top-level brief.yaml schema."""
    model_config = ConfigDict(extra="forbid")

    goal: str
    aspect: Literal["9:16", "16:9", "1:1", "4:3", "3:4"] = "9:16"
    voice: str = "Kore"
    voice_speed: float = 1.0
    success_criteria: list[str] = Field(default_factory=list)
    assets: Assets = Field(default_factory=Assets)
    script: Script

    # ---------------------------------------------------------------- I/O

    @classmethod
    def from_yaml(cls, path: str | Path) -> "Brief":
        """Load and validate a brief.yaml file. Raises on schema errors."""
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"brief not found: {p}")
        with p.open("r", encoding="utf-8") as fp:
            data = yaml.safe_load(fp) or {}
        if not isinstance(data, dict):
            raise ValueError(
                f"brief.yaml must be a mapping at the top level, got {type(data).__name__}"
            )
        return cls.model_validate(data)

    def to_dict(self) -> dict:
        """Round-trippable dict representation (JSON-safe)."""
        return self.model_dump(mode="json")

    # --------------------------------------------------------- Validation

    def validate_assets(self, folder: str | Path) -> list[str]:
        """Check every `provided` asset exists relative to `folder`.

        Returns a list of missing absolute paths (empty = all good).
        Generated assets are NOT checked — they're an inventory hint, not
        a precondition.
        """
        root = Path(folder).expanduser().resolve()
        missing: list[str] = []
        for asset in self.assets.provided:
            ap = Path(asset.path)
            full = ap if ap.is_absolute() else root / ap
            if not full.is_file():
                missing.append(str(full))
        return missing

    # ----------------------------------------------------- Plan derivation

    def to_plan_skeleton(self) -> dict:
        """Project the brief into a partial plan.yaml dict.

        The planner (`parallax plan`) is responsible for filling in
        per-scene model aliases, locked stills, and any
        provider-specific fields. This method gives it a starting point
        with the brief's deterministic fields already populated.
        """
        return {
            "aspect": self.aspect,
            "voice": self.voice,
            "speed": self.voice_speed,
            "scenes": [
                {
                    "index": s.index,
                    "shot_type": s.shot_type,
                    "vo_text": s.vo_text,
                    "prompt": s.prompt,
                    **({"animate": True} if s.animate else {}),
                    **({"motion_prompt": s.motion_prompt} if s.motion_prompt else {}),
                    **({"aspect": s.aspect} if s.aspect else {}),
                }
                for s in self.script.scenes
            ],
        }
