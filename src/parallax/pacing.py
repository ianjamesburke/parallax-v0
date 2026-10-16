"""Advisory opening pacing checks for social videos; no creative quality score."""
from __future__ import annotations

import re
from typing import Any


VISUAL_REVIEW_PROMPTS = [
    "Do the opening shots change framing, subject scale, or viewpoint?",
    "Does each opening shot show a clear action or visual change rather than repeat a static composition?",
    "Do those changes support the narration and remain readable? Review the images and motion; timing alone cannot establish visual quality.",
]


def estimate_timeline(scenes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    """Project explicit durations or 150 wpm reading estimates, never production timing."""
    timeline = []
    cursor = 0.0
    estimated = False
    for position, scene in enumerate(scenes):
        duration = scene.get("duration_s")
        if duration is None:
            estimated = True
            text = re.sub(r"\[[^\]]*\]", "", scene.get("vo_text", ""))
            duration = len(text.split()) / 2.5
        duration = max(0.0, float(duration))
        timeline.append({"index": scene.get("index", position), "start_s": cursor, "end_s": cursor + duration})
        cursor += duration
    basis = "estimated (150 words/minute, with explicit durations where supplied)" if estimated else "declared scene durations"
    return timeline, basis


def review_opening_pacing(scenes: list[dict[str, Any]], timing_basis: str, first_word_start_s: float | None = None) -> dict[str, Any]:
    """Flag long early holds and fewer than three cuts in the first six seconds.

    This is a social-opening heuristic, not a universal requirement or an
    automated judgment of excitement. Scene cuts are a proxy for visual changes;
    action inside a shot and actual framing still require human review.
    """
    warnings = []
    if first_word_start_s is not None and first_word_start_s > 0.1 + 1e-6:
        warnings.append({
            "code": "opening_delayed_first_word", "field": "words[0].start",
            "first_word_start_s": first_word_start_s, "timing_basis": timing_basis,
            "message": f"First aligned word starts at {first_word_start_s:.3f}s (target by 0.100s). This is a word timestamp, not an acoustic onset measurement. Review `audio trim-leading-quiet --first-word-by 0.1` to remove only verified leading quiet; locked audio is unchanged.",
        })
    for position, scene in enumerate(scenes):
        start, end = float(scene["start_s"]), float(scene["end_s"])
        duration = end - start
        if 0 <= start < 12 and duration > 3.0 + 1e-6:
            warnings.append({
                "code": "opening_long_hold", "field": f"scenes[{scene.get('index', position)}]",
                "scene_index": scene.get("index", position), "start_s": round(start, 3),
                "duration_s": round(duration, 3), "timing_basis": timing_basis,
                "message": f"Scene {scene.get('index', position)} holds for {duration:.2f}s starting at {start:.2f}s in the first 12s. Review a split or meaningful action (social-opening advisory).",
            })
    cuts = sum(0 < float(scene["start_s"]) < 6 for scene in scenes[1:])
    duration = max((float(s["end_s"]) for s in scenes), default=0.0)
    if duration >= 6 and cuts < 3:
        warnings.append({
            "code": "opening_few_cuts", "field": "scenes", "cuts_before_6s": cuts,
            "timing_basis": timing_basis,
            "message": f"Only {cuts} scene cut(s) before 6s. For a social opening, review roughly 3–4 visual changes (at least 3 cuts / 4 shots); action within a shot may satisfy the creative intent.",
        })
    return {"timing_basis": timing_basis, "cuts_before_6s": cuts,
            "warnings": warnings, "visual_review_prompts": list(VISUAL_REVIEW_PROMPTS)}
