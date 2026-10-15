"""parallax validate — dry-run brief and plan validation.

Runs schema checks and asset-existence checks without generating any assets
or spending any credits. Returns a structured result dict suitable for JSON
serialisation.
"""
from __future__ import annotations

import logging
from pathlib import Path

import yaml

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

    # Per-scene image_refs existence
    for scene in brief.script.scenes:
        for j, image_ref in enumerate(scene.image_refs):
            ref_path = folder / image_ref
            if not ref_path.is_file():
                log.warning("validate: missing image_refs[%d] %s on scene %d", j, ref_path, scene.index)
                errors.append({
                    "field": f"script.scenes[{scene.index}].image_refs[{j}]",
                    "message": f"file not found: {ref_path}",
                })

    from .pacing import estimate_timeline, review_opening_pacing
    timeline, basis = estimate_timeline([scene.model_dump() for scene in brief.script.scenes])
    pacing = review_opening_pacing(timeline, basis)
    warnings.extend(pacing["warnings"])
    return {**_result(errors, warnings), "opening_pacing": pacing}


def validate_plan(plan_path: str | Path, folder: str | Path) -> dict:
    """Validate a plan.yaml without spending any credits."""
    errors: list[dict] = []
    warnings: list[dict] = []

    folder = Path(folder).expanduser().resolve()
    plan_path = Path(plan_path).expanduser()

    log.info("validate: loading plan from %s", plan_path)

    if not plan_path.is_file():
        errors.append({"field": "plan", "message": f"plan not found: {plan_path}"})
        return _result(errors, warnings)

    # Raw YAML load + bare-colon check (before Pydantic).
    # A bare colon in a block-scalar value (e.g. `prompt: movement: she leans in`)
    # causes a YAML ScannerError. We detect this by: (a) catching the parse error and
    # checking its message, and (b) scanning raw text for "key: word: rest" patterns
    # in the free-text fields so we can name the offending field.
    _FREE_TEXT_FIELDS = ("prompt", "vo_text", "motion_prompt")
    raw: dict = {}
    try:
        with plan_path.open("r", encoding="utf-8") as fp:
            raw = yaml.safe_load(fp) or {}
    except yaml.YAMLError as exc:
        # Try to identify which field has the bare colon by scanning raw text.
        bare_colon_field: str | None = None
        try:
            with plan_path.open("r", encoding="utf-8") as fp:
                lines = fp.readlines()
            for line in lines:
                stripped = line.strip()
                for fname in _FREE_TEXT_FIELDS:
                    prefix = f"{fname}: "
                    if stripped.startswith(prefix):
                        remainder = stripped[len(prefix):]
                        # A bare colon in the remainder (not at the end, not a flow scalar)
                        # means it's a key: value situation.
                        if ": " in remainder or remainder.endswith(":"):
                            bare_colon_field = fname
                            break
                if bare_colon_field:
                    break
        except Exception:
            pass

        if bare_colon_field:
            errors.append({
                "field": f"scenes[?].{bare_colon_field}",
                "message": (
                    f"`{bare_colon_field}` contains a bare colon and was misparsed "
                    f"as a YAML mapping. Fix: wrap the value in double quotes: "
                    f'{bare_colon_field}: "value with: colon"'
                ),
            })
        else:
            errors.append({"field": "plan", "message": f"could not read plan YAML: {exc}"})
        return _result(errors, warnings)

    # Also check successfully-parsed YAML for dict-valued free-text fields
    # (can happen in flow-style YAML: `prompt: {key: val}`).
    for scene in raw.get("scenes", []):
        idx = scene.get("index", "?")
        for fname in _FREE_TEXT_FIELDS:
            val = scene.get(fname)
            if isinstance(val, dict):
                errors.append({
                    "field": f"scenes[{idx}].{fname}",
                    "message": (
                        f"scene {idx} — `{fname}` contains a bare colon and was misparsed "
                        f"as a YAML mapping. Fix: wrap the value in double quotes: "
                        f'{fname}: "value with: colon"'
                    ),
                })
    if errors:
        return _result(errors, warnings)

    # Pydantic schema validation
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

    # Model alias validation
    from . import models as _models
    from .models._spec import Kind as _Kind
    from typing import cast as _cast

    for field_name, kind in (("image_model", "image"), ("video_model", "video"), ("voice_model", "tts")):
        alias = getattr(plan, field_name, None)
        if alias is not None:
            try:
                _models.resolve(alias, kind=_cast(_Kind, kind))
            except ValueError as exc:
                errors.append({"field": field_name, "message": str(exc)})

    # Voice / voice_model mismatch check
    _check_voice_model(plan.voice, plan.voice_model, errors, warnings)

    # Per-scene checks: animate + reference
    for scene in plan.scenes:
        if scene.animate and not scene.motion_prompt:
            errors.append({
                "field": f"scenes[{scene.index}].animate",
                "message": f"scene {scene.index}: animate: true requires a motion_prompt",
            })
        if (
            scene.shot_type == "character"
            and scene.reference is True
            and plan.character_image is None
            and scene.reference_images is None
        ):
            errors.append({
                "field": f"scenes[{scene.index}].reference",
                "message": (
                    f"scene {scene.index}: reference: true requires character_image "
                    "or reference_images"
                ),
            })

    # Top-level locked paths
    _check_path(plan.audio_path, "audio_path", folder, errors)
    _check_path(plan.words_path, "words_path", folder, errors)
    _check_path(plan.character_image, "character_image", folder, errors)

    from .ffmpeg_utils import probe_duration as _probe_duration

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

        # Clip offset + duration must not exceed clip length
        offset = scene.clip_trim_start_s
        if (
            scene.clip_path is not None
            and isinstance(offset, (int, float))
            and offset > 0.0
            and scene.duration_s is not None
        ):
            cp = Path(scene.clip_path)
            clip_full = cp if cp.is_absolute() else folder / cp
            if clip_full.is_file():
                clip_dur = _probe_duration(clip_full)
                if clip_dur is not None and offset + scene.duration_s > clip_dur + 1e-3:
                    errors.append({
                        "field": f"scenes[{scene.index}].clip_offset_s",
                        "message": (
                            f"clip_offset_s ({offset}s) + duration_s ({scene.duration_s}s) = "
                            f"{offset + scene.duration_s:.3f}s exceeds clip length "
                            f"({clip_dur:.3f}s)"
                        ),
                    })

    from .pacing import review_opening_pacing
    timeline, basis, first_word = _plan_pacing_timeline(plan, folder)
    pacing = review_opening_pacing(timeline, basis, first_word_start_s=first_word)
    warnings.extend(pacing["warnings"])
    return {**_result(errors, warnings), "opening_pacing": pacing}



def _plan_pacing_timeline(plan, folder: Path) -> tuple[list[dict], str, float | None]:
    """Use existing locked timing offline; keep estimates explicit when unavailable."""
    import json
    import math
    import re
    from copy import deepcopy
    from .pacing import estimate_timeline

    scenes = [scene.model_dump(exclude_none=True) for scene in plan.scenes]
    estimated, basis = estimate_timeline(scenes)
    if not plan.audio_path or not plan.words_path:
        return estimated, basis, None
    if plan.voice_postprocess is not None or plan.voice_speed != 1.0 or any(
        scene.voice_speed not in (None, 1.0) for scene in plan.scenes
    ):
        return estimated, basis + "; locked timing not used because audio transform settings are present", None
    try:
        audio = Path(plan.audio_path).expanduser()
        audio = audio if audio.is_absolute() else folder / audio
        if not audio.is_file():
            raise ValueError("locked audio file is missing")
        path = Path(plan.words_path).expanduser()
        path = path if path.is_absolute() else folder / path
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not payload.get("words") or "total_duration_s" not in payload:
            raise ValueError("locked words need words and total_duration_s")
        total = float(payload["total_duration_s"])
        words = payload["words"]
        if not math.isfinite(total) or total <= 0:
            raise ValueError("locked audio duration is invalid")
        for word in words:
            start, end = float(word["start"]), float(word["end"])
            if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end < start or end > total + 0.05:
                raise ValueError("locked word timing is invalid")
        if any(float(a["start"]) > float(b["start"]) for a, b in zip(words, words[1:])):
            raise ValueError("locked words are not chronological")
        from .assembly import align_scenes_obj, _norm_word
        from .text_expand import expand_digits
        from .stages import _apply_timing_overrides
        script = " ".join(scene.get("vo_text", "") for scene in scenes)
        script = re.sub(r"\[[^\]]*\]", "", expand_digits(script))
        expected = [_norm_word(word) for word in script.split() if _norm_word(word)]
        actual_text = expand_digits(" ".join(word["word"] for word in words))
        actual = [_norm_word(word) for word in actual_text.split() if _norm_word(word)]
        if expected != actual:
            raise ValueError("locked transcript does not match current scene narration")
        aligned = align_scenes_obj(deepcopy(scenes), deepcopy(payload))
        aligned = _apply_timing_overrides(aligned, scenes)
        return aligned, "aligned locked word timings (including explicit scene overrides)", float(words[0]["start"])
    except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
        return estimated, basis + f"; locked timing unavailable: {exc}", None


def _check_voice_model(
    voice: str | None,
    voice_model: str | None,
    errors: list[dict],
    warnings: list[dict],
) -> None:
    """Validate that `voice` is compatible with `voice_model`.

    - If the voice belongs to the OTHER model's list → error.
    - If the voice is in the CURRENT model's list → pass.
    - If the voice is unknown to both lists → warning.
    """
    if not voice or not voice_model:
        return
    from . import models as _models

    mini_voices: set[str] = set()
    gemini_voices: set[str] = set()
    try:
        mini_spec = _models.resolve("tts-mini", kind="tts")
        mini_voices = set(mini_spec.voices)
    except ValueError:
        pass
    try:
        gemini_spec = _models.resolve("tts-gemini", kind="tts")
        gemini_voices = set(gemini_spec.voices)
    except ValueError:
        pass

    if voice_model == "tts-mini":
        if voice in gemini_voices:
            errors.append({
                "field": "voice",
                "message": (
                    f"voice {voice!r} requires voice_model: tts-gemini "
                    f"(current: tts-mini)"
                ),
            })
        elif voice not in mini_voices:
            warnings.append({
                "field": "voice",
                "message": (
                    f"voice {voice!r} is not in the known tts-mini voice list — "
                    "proceeding anyway (custom or future voice)"
                ),
            })
    elif voice_model == "tts-gemini":
        if voice in mini_voices:
            errors.append({
                "field": "voice",
                "message": (
                    f"voice {voice!r} requires voice_model: tts-mini "
                    f"(current: tts-gemini)"
                ),
            })
        elif voice not in gemini_voices:
            warnings.append({
                "field": "voice",
                "message": (
                    f"voice {voice!r} is not in the known tts-gemini voice list — "
                    "proceeding anyway (custom or future voice)"
                ),
            })


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
