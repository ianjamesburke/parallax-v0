"""YAML → ModelSpec loader.

Reads the per-modality catalog files (`image.yaml`, `video.yaml`, `tts.yaml`)
shipped alongside this module and yields `ModelSpec` instances. The YAML
schema is the user-facing one (cleaner than the dataclass field names); this
loader translates between them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import yaml

from ._spec import Kind, ModelSpec

_HERE = Path(__file__).resolve().parent


def _to_spec(entry: dict[str, Any], kind: Kind) -> ModelSpec:
    """Translate one YAML entry into a `ModelSpec`."""
    caps = entry.get("capabilities") or {}
    aspect_ratios = caps.get("aspect_ratios") or ()
    if isinstance(aspect_ratios, list):
        aspect_ratios = tuple(aspect_ratios)
    inputs = entry.get("inputs") or ()
    if isinstance(inputs, list):
        inputs = tuple(inputs)
    voices = entry.get("voices") or ()
    if isinstance(voices, list):
        voices = tuple(voices)

    portrait_args: dict[str, object] = {}
    if "9:16" in aspect_ratios:
        # Preserve old hint so openrouter.py can keep passing aspect_ratio
        # verbatim until Phase 1.3 lands the explicit `--aspect` plumbing.
        portrait_args["aspect_ratio"] = "9:16"

    return ModelSpec(
        alias=entry["alias"],
        kind=kind,
        model_id=entry["model_id"],
        cost_usd=float(entry.get("cost", 0.0)),
        cost_unit=entry.get("unit", "image"),
        description=entry.get("description", ""),
        fallback_alias=entry.get("fallback"),
        tier=entry.get("tier", "default"),
        max_refs=int(caps.get("max_refs", 0)),
        portrait_args=portrait_args,
        aspect_ratios=aspect_ratios,
        start_frame=bool(caps.get("start_frame", False)),
        end_frame=bool(caps.get("end_frame", False)),
        inputs=inputs,
        voices=voices,
    )


def _load_kind(filename: str, kind: Kind) -> dict[str, ModelSpec]:
    path = _HERE / filename
    with path.open("r", encoding="utf-8") as fp:
        entries: Iterable[dict[str, Any]] = yaml.safe_load(fp) or []
    out: dict[str, ModelSpec] = {}
    for entry in entries:
        spec = _to_spec(entry, kind)
        if spec.alias in out:
            raise ValueError(f"Duplicate alias {spec.alias!r} in {filename}")
        out[spec.alias] = spec
    return out


def load_image() -> dict[str, ModelSpec]:
    return _load_kind("image.yaml", "image")


def load_video() -> dict[str, ModelSpec]:
    return _load_kind("video.yaml", "video")


def load_tts() -> dict[str, ModelSpec]:
    return _load_kind("tts.yaml", "tts")
