"""parallax validate — dry-run brief and plan validation.

Runs schema checks and asset-existence checks without generating any assets
or spending any credits. Returns a structured result dict suitable for JSON
serialisation.
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)


def validate_brief(brief_path: str | Path, folder: str | Path) -> dict:
    """Validate a brief.yaml without spending any credits."""
    errors: list[dict] = []
    warnings: list[dict] = []

    folder = Path(folder).expanduser().resolve()
    brief_path = Path(brief_path).expanduser()

    log.info("validate: loading brief from %s", brief_path)

    from .brief import Brief

    try:
        brief = Brief.from_yaml(brief_path)
    except FileNotFoundError as exc:
        errors.append({"field": "brief", "message": str(exc)})
        return _result(errors, warnings)
    except Exception as exc:
        errors.append({"field": "brief", "message": f"schema error: {exc}"})
        return _result(errors, warnings)

    log.info("validate: brief parsed, %d scene(s)", len(brief.script.scenes))

    # Provided asset existence
    for i, asset in enumerate(brief.assets.provided):
        ap = Path(asset.path)
        full = ap if ap.is_absolute() else folder / ap
        if not full.is_file():
            log.warning("validate: missing provided asset %s", full)
            errors.append({
                "field": f"assets.provided[{i}].path",
                "message": f"file not found: {full}",
            })

        # product_ref has no wiring yet (see #83)
        if asset.kind == "product_ref":
            warnings.append({
                "field": f"assets.provided[{i}]",
                "message": (
                    "product_ref provided but no wiring exists — "
                    "will be ignored (see #83)"
                ),
            })

    # Per-scene image_ref existence
    for scene in brief.script.scenes:
        if scene.image_ref is not None:
            ref_path = folder / scene.image_ref
            if not ref_path.is_file():
                log.warning("validate: missing image_ref %s on scene %d", ref_path, scene.index)
                errors.append({
                    "field": f"script.scenes[{scene.index}].image_ref",
                    "message": f"file not found: {ref_path}",
                })

    return _result(errors, warnings)


def validate_plan(plan_path: str | Path, folder: str | Path) -> dict:
    """Validate a plan.yaml without spending any credits."""
    errors: list[dict] = []
    warnings: list[dict] = []

    folder = Path(folder).expanduser().resolve()
    plan_path = Path(plan_path).expanduser()

    log.info("validate: loading plan from %s", plan_path)

    from .plan import Plan

    try:
        plan = Plan.from_yaml(plan_path)
    except FileNotFoundError as exc:
        errors.append({"field": "plan", "message": str(exc)})
        return _result(errors, warnings)
    except Exception as exc:
        errors.append({"field": "plan", "message": f"schema error: {exc}"})
        return _result(errors, warnings)

    log.info("validate: plan parsed, %d scene(s)", len(plan.scenes))

    # Top-level locked paths
    _check_path(plan.audio_path, "audio_path", folder, errors)
    _check_path(plan.words_path, "words_path", folder, errors)
    _check_path(plan.character_image, "character_image", folder, errors)

    # Per-scene locked paths
    for scene in plan.scenes:
        prefix = f"scenes[{scene.index}]"
        _check_path(scene.still_path, f"{prefix}.still_path", folder, errors)
        _check_path(scene.clip_path, f"{prefix}.clip_path", folder, errors)
        _check_path(scene.end_frame_path, f"{prefix}.end_frame_path", folder, errors)
        if scene.reference_images:
            for j, rp in enumerate(scene.reference_images):
                _check_path(rp, f"{prefix}.reference_images[{j}]", folder, errors)
        if scene.video_references:
            for j, vr in enumerate(scene.video_references):
                _check_path(vr, f"{prefix}.video_references[{j}]", folder, errors)

    return _result(errors, warnings)


def _check_path(value: str | None, field: str, folder: Path, errors: list) -> None:
    if value is None:
        return
    p = Path(value)
    full = p if p.is_absolute() else folder / p
    if not full.exists():
        log.warning("validate: missing path at field %s: %s", field, full)
        errors.append({"field": field, "message": f"file not found: {full}"})


def _result(errors: list[dict], warnings: list[dict]) -> dict:
    return {"valid": len(errors) == 0, "errors": errors, "warnings": warnings}
