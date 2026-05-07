"""Pre-flight cost estimation for `parallax produce`.

Computes a per-scene cost table before any generation runs, then optionally
prompts the user to confirm before proceeding.

Public API:
  compute_preflight(plan, balance_usd=None) -> PreflightResult
  format_preflight(result) -> str
  prompt_proceed(result, yes=False) -> bool
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .log import get_logger
from .models import resolve

log = get_logger("preflight")


@dataclass
class PreflightScene:
    index: int
    kind: str           # "still" or "clip"
    model_alias: str
    duration_s: float   # 0.0 for stills
    locked: bool
    cost_usd: float
    will_overwrite: bool = False
    # Set when the model generates at a lower resolution than the output (e.g.
    # Seedance generates at 480p and is upscaled to the output resolution).
    native_resolution: str | None = None
    output_resolution: str | None = None


@dataclass
class PreflightResult:
    scenes: list[PreflightScene] = field(default_factory=list)
    voiceover_model: str = "tts-mini"
    voiceover_locked: bool = False
    voiceover_cost_usd: float = 0.0
    estimated_total_usd: float = 0.0
    balance_usd: float | None = None
    has_overwrites: bool = False


def compute_preflight(
    plan: dict[str, Any],
    balance_usd: float | None = None,
    folder: Path | None = None,
) -> PreflightResult:
    """Compute per-scene cost estimates from a plan dict.

    Does not make any network calls — cost comes from the model catalog.
    Locked assets (still_path / clip_path / audio_path set) contribute $0.

    When `folder` is provided, unlocked scenes are checked against
    ``folder/parallax/assets/`` for existing files that would be silently
    overwritten. Scenes with existing files get ``will_overwrite=True`` and
    the result carries ``has_overwrites=True``.
    """
    image_model = plan.get("image_model", "mid")
    video_model = plan.get("video_model", "mid")
    voice_model = plan.get("voice_model", "tts-mini")
    output_resolution = plan.get("resolution") or None

    assets_dir = (Path(folder) / "parallax" / "assets") if folder is not None else None

    scenes: list[PreflightScene] = []

    for s in plan.get("scenes", []):
        idx = s["index"]

        # --- Still ---
        still_alias = s.get("image_model") or image_model
        still_locked = bool(s.get("still_path"))
        if still_locked:
            still_cost = 0.0
        else:
            still_cost = resolve(still_alias, kind="image").cost_usd

        still_overwrite = (
            not still_locked
            and assets_dir is not None
            and (assets_dir / f"scene_{idx:02d}_still.png").exists()
        )

        scenes.append(PreflightScene(
            index=idx,
            kind="still",
            model_alias=still_alias,
            duration_s=0.0,
            locked=still_locked,
            cost_usd=still_cost,
            will_overwrite=still_overwrite,
        ))
        log.info("preflight scene %d: still (%s) locked=%s overwrite=%s ~$%.3f",
                 idx, still_alias, still_locked, still_overwrite, still_cost)

        # --- Clip (only if animate=True) ---
        if s.get("animate", False):
            clip_alias = s.get("video_model") or video_model
            clip_locked = bool(s.get("clip_path"))
            duration = float(s.get("duration_s") or 5.0)
            if clip_locked:
                clip_cost = 0.0
                clip_native_res = None
            else:
                clip_spec = resolve(clip_alias, kind="video")
                clip_cost = clip_spec.cost_usd * duration
                clip_native_res = clip_spec.native_resolution

            clip_overwrite = (
                not clip_locked
                and assets_dir is not None
                and (assets_dir / f"scene_{idx:02d}_animated.mp4").exists()
            )

            scenes.append(PreflightScene(
                index=idx,
                kind="clip",
                model_alias=clip_alias,
                duration_s=duration,
                locked=clip_locked,
                cost_usd=clip_cost,
                will_overwrite=clip_overwrite,
                native_resolution=clip_native_res,
                output_resolution=output_resolution,
            ))
            log.info("preflight scene %d: clip (%s, %.0fs) locked=%s overwrite=%s ~$%.3f",
                     idx, clip_alias, duration, clip_locked, clip_overwrite, clip_cost)

    # --- Voiceover ---
    vo_locked = bool(plan.get("audio_path"))
    vo_cost = 0.0  # Current TTS models have cost_usd=0

    estimated_total = sum(sc.cost_usd for sc in scenes) + vo_cost
    balance_str = f"${balance_usd:.2f}" if balance_usd is not None else "unknown"
    has_overwrites = any(sc.will_overwrite for sc in scenes)
    log.info("preflight: estimated_total=$%.3f balance=%s has_overwrites=%s",
             estimated_total, balance_str, has_overwrites)

    return PreflightResult(
        scenes=scenes,
        voiceover_model=voice_model,
        voiceover_locked=vo_locked,
        voiceover_cost_usd=vo_cost,
        estimated_total_usd=estimated_total,
        balance_usd=balance_usd,
        has_overwrites=has_overwrites,
    )


def _short_res(res: str) -> str:
    """Convert internal resolution string (e.g. "1080x1920") to short label (e.g. "1080p").

    Uses the width (first component) which matches how Seedance and OpenRouter
    label their resolution tiers for portrait (9:16) video.
    """
    return f"{res.split('x')[0]}p"


def format_preflight(result: PreflightResult) -> str:
    """Return the formatted pre-flight cost table as a multi-line string.

    Includes the header line, per-scene rows, separator, total, and balance.
    Does NOT include the "proceed?" prompt — that is printed by prompt_proceed.
    """
    lines: list[str] = ["==> pre-flight cost estimate"]

    if result.has_overwrites:
        lines.append("    ⚠ WARNING: existing files will be overwritten (use regenerate: true to be explicit)")

    for s in result.scenes:
        if s.locked:
            status = "[locked — skipping]"
        elif s.will_overwrite:
            status = "[will overwrite existing file!]"
        else:
            status = "[will regenerate]"
        if s.kind == "still":
            row = (
                f"    scene {s.index} — still ({s.model_alias})"
                f"       ~${s.cost_usd:.3f}   {status}"
            )
        else:
            res_note = ""
            if (
                s.native_resolution
                and s.output_resolution
                and s.native_resolution != _short_res(s.output_resolution)
            ):
                res_note = f" {s.native_resolution} → {_short_res(s.output_resolution)}"
            row = (
                f"    scene {s.index} — clip ({s.model_alias}{res_note}, {s.duration_s:.0f}s)"
                f"  ~${s.cost_usd:.3f}   {status}"
            )
        lines.append(row)

    # Voiceover row
    vo_status = "[locked — skipping]" if result.voiceover_locked else "[will regenerate]"
    lines.append(
        f"    voiceover ({result.voiceover_model})"
        f"      ~${result.voiceover_cost_usd:.3f}   {vo_status}"
    )

    lines.append("    ─────────────────────────────────────")
    lines.append(f"    estimated total:             ~${result.estimated_total_usd:.3f}")

    if result.balance_usd is not None:
        lines.append(f"    current balance:             ${result.balance_usd:.2f}")

    return "\n".join(lines)


def prompt_proceed(result: PreflightResult, yes: bool = False) -> bool:
    """Print the cost table and ask for confirmation.

    Returns True if production should proceed, False if the user cancelled.
    When `yes=True` or stdout is not a TTY, proceeds immediately without prompting.
    """
    print(format_preflight(result), flush=True)

    if yes or not sys.stdout.isatty():
        print("==> proceeding (--yes / non-interactive)", flush=True)
        return True

    print("==> proceed? [y/N] ", end="", flush=True)
    answer = input().strip().lower()
    return answer == "y"
