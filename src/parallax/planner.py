"""parallax plan — deterministic brief.yaml -> plan.yaml translator.

The planner is a pure, mechanical projection from a `Brief` into a fully
resolved plan dict, plus a couple of CLI-facing planner-only fields
(`model`, `caption_style`, top-level `character_image`). It does NOT call
any LLM: per-scene `prompt:` / `motion_prompt:` strings authored in the
brief are written through to the plan as-is. LLM-driven prompt expansion
is a future enhancement.

Flow:
  1. Load + validate the brief (`Brief.from_yaml`).
  2. Validate every `provided` asset exists on disk.
  3. If anything is missing, write `questions.yaml` describing the gaps
     and return `PlanResult(ok=False, ...)`. The CLI exits non-zero.
  4. Otherwise materialize `Brief.to_plan_skeleton()`, decorate with
     planner-only fields, write `plan.yaml`, and return
     `PlanResult(ok=True, ...)`.

The CLI subcommand wiring (`parallax plan ...`) is added in a separate
pass; this module is the importable core.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .brief import Brief


# Plan-key ordering used when serializing to YAML. Keeping a deterministic
# order makes diffs across iterations readable.
_TOP_LEVEL_ORDER = (
    "aspect",
    "voice",
    "voice_speed",
    "trim_pauses",
    "voice_model",
    "image_model",
    "video_model",
    "caption_style",
    "character_image",
    "product_image",
    "scenes",
)

# Asset kinds that are wired into the plan. Any kind NOT in this set will
# trigger a WARNING at plan-time so the user knows the asset is ignored.
_WIRED_ASSET_KINDS = frozenset({"character_ref", "product_ref"})


@dataclass
class PlanResult:
    """Outcome of `plan_from_brief`.

    `ok=True` -> `plan_path` is written and `questions_path` is None.
    `ok=False` -> `questions_path` is written, `plan_path` is None, and
    `missing_assets` lists the absolute paths that were expected but not
    found on disk.
    """
    ok: bool
    plan_path: Path | None = None
    questions_path: Path | None = None
    missing_assets: list[str] = field(default_factory=list)
    scene_count: int = 0


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _resolve_provided_path(folder: Path, asset_path: str) -> Path:
    """Resolve a brief-provided asset path against the project folder."""
    p = Path(asset_path)
    return p if p.is_absolute() else (folder / p)


def _first_character_ref(brief: Brief, folder: Path) -> str | None:
    """Return the absolute path to the first `character_ref` provided
    asset, or None if the brief has none. Folder is used to resolve any
    relative asset path to an absolute on-disk location.
    """
    for asset in brief.assets.provided:
        if asset.kind == "character_ref":
            return str(_resolve_provided_path(folder, asset.path))
    return None


def _first_product_ref(brief: Brief, folder: Path) -> str | None:
    """Return the absolute path to the first `product_ref` provided
    asset, or None if the brief has none.
    """
    for asset in brief.assets.provided:
        if asset.kind == "product_ref":
            return str(_resolve_provided_path(folder, asset.path))
    return None


def _ordered_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Return a new dict with keys in `_TOP_LEVEL_ORDER`, then any extras
    in their original order (defensive — shouldn't happen for now)."""
    ordered: dict[str, Any] = {}
    for key in _TOP_LEVEL_ORDER:
        if key in plan:
            ordered[key] = plan[key]
    for key, value in plan.items():
        if key not in ordered:
            ordered[key] = value
    return ordered


def _dump_yaml(path: Path, data: dict[str, Any]) -> None:
    """Write a dict to YAML using the codebase's standard plan-write style."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fp:
        yaml.safe_dump(
            data,
            fp,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
            width=10000,
        )


def _build_questions(brief: Brief, folder: Path, missing: list[str]) -> dict[str, Any]:
    """Construct the questions.yaml payload describing every missing asset.

    For each missing absolute path we look back at the brief's provided
    list to recover `kind` and `description`, so the operator knows what
    file to drop in. If a missing path can't be matched (shouldn't
    happen), we fall back to placeholder fields rather than raise.
    """
    missing_set = set(missing)
    items: list[dict[str, Any]] = []
    for asset in brief.assets.provided:
        full = str(_resolve_provided_path(folder, asset.path))
        if full in missing_set:
            items.append({
                "path": full,
                "kind": asset.kind,
                "description": asset.description or "",
            })
            missing_set.discard(full)
    # Anything left in missing_set wasn't matchable — surface it anyway.
    for orphan in sorted(missing_set):
        items.append({
            "path": orphan,
            "kind": "unknown",
            "description": "",
        })
    return {
        "reason": (
            "Required assets are missing. Add them to the project folder "
            "and re-run `parallax plan`."
        ),
        "missing": items,
    }


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

def plan_from_brief(
    brief_path: str | Path,
    folder: str | Path,
    out_path: str | Path | None = None,
    *,
    image_model: str = "mid",
    video_model: str = "mid",
    voice_model: str = "tts-mini",
    caption_style: str = "anton",
) -> PlanResult:
    """Translate a brief.yaml into a fully-resolved plan.yaml.

    Args:
        brief_path: Path to the source brief.yaml.
        folder: Project root. Relative `provided` asset paths are
            resolved against this directory, and the default plan/
            questions output location is `<folder>/parallax/scratch/`.
        out_path: Optional override for plan.yaml destination. When
            provided, plan.yaml is written there instead of under
            `parallax/scratch/`.
        image_model: Image model alias to write into `plan.model`
            (planner-only field). Defaults to "mid".
        video_model: Video model alias. Currently unused at the plan
            level but accepted so callers can pass it without raising;
            reserved for the upcoming per-scene model split.
        caption_style: Caption preset name written into
            `plan.caption_style`. Defaults to "anton".

    Returns:
        PlanResult. On success, `plan_path` points at the written file.
        On missing assets, `questions_path` points at the written
        questions.yaml and no plan.yaml is written.
    """
    brief_path = Path(brief_path)
    folder = Path(folder).expanduser().resolve()

    brief = Brief.from_yaml(brief_path)

    # Validate provided assets first — fail fast if anything's missing.
    missing = brief.validate_assets(folder)
    scene_count = len(brief.script.scenes)

    if missing:
        questions_path = folder / "parallax" / "scratch" / "questions.yaml"
        payload = _build_questions(brief, folder, missing)
        _dump_yaml(questions_path, payload)
        return PlanResult(
            ok=False,
            plan_path=None,
            questions_path=questions_path,
            missing_assets=missing,
            scene_count=scene_count,
        )

    # All assets present — build the plan.
    plan = brief.to_plan_skeleton()
    plan["image_model"] = image_model
    plan["video_model"] = video_model
    plan["voice_model"] = voice_model
    plan["caption_style"] = caption_style

    character_image = _first_character_ref(brief, folder)
    if character_image is not None:
        plan["character_image"] = character_image
        for scene in plan["scenes"]:
            if scene.get("shot_type") == "character" and "still_path" not in scene:
                scene["reference"] = True

    product_image = _first_product_ref(brief, folder)
    if product_image is not None:
        plan["product_image"] = product_image

    # Warn about any provided asset kind that isn't wired into the plan.
    for asset in brief.assets.provided:
        if asset.kind not in _WIRED_ASSET_KINDS:
            print(
                f"WARNING: {asset.kind} asset '{asset.path}' provided but not wired into plan"
                f" — will be ignored",
                flush=True,
            )

    plan = _ordered_plan(plan)

    plan_path = (
        Path(out_path).expanduser().resolve()
        if out_path is not None
        else folder / "parallax" / "scratch" / "plan.yaml"
    )
    _dump_yaml(plan_path, plan)

    return PlanResult(
        ok=True,
        plan_path=plan_path,
        questions_path=None,
        missing_assets=[],
        scene_count=scene_count,
    )
