from __future__ import annotations

import time
from pathlib import Path

from . import fal, usage
from .context import current_session_id
from .log import get_logger
from .pricing import ModelSpec, resolve
from .shim import is_test_mode, render_mock_image

log = get_logger("tools")


def generate_image(
    prompt: str,
    model: str,
    reference_images: list[str] | None = None,
    out_dir: str | None = None,
) -> str:
    spec = resolve(model)
    refs = _validate_refs(reference_images, spec)
    test_mode = is_test_mode()
    out = Path(out_dir) if out_dir else None

    t0 = time.monotonic()
    if test_mode:
        output_path = str(render_mock_image(prompt=prompt, model=spec.alias, out_dir=out))
    else:
        output_path = str(fal.generate(prompt=prompt, spec=spec, reference_images=refs, out_dir=out))
    duration_ms = int((time.monotonic() - t0) * 1000)
    cost_usd = 0.0 if test_mode else spec.price_usd_per_image

    rec = usage.record(
        session_id=current_session_id.get(),
        backend="produce",
        alias=spec.alias,
        fal_id=spec.fal_id,
        tier=spec.tier,
        prompt=prompt,
        output_path=output_path,
        duration_ms=duration_ms,
        cost_usd=cost_usd,
        test_mode=test_mode,
    )
    log.info(
        "usage: alias=%s duration=%dms cost=$%.4f%s",
        rec.alias,
        rec.duration_ms,
        rec.cost_usd,
        " [test]" if test_mode else "",
    )
    return output_path


def _validate_refs(reference_images: list[str] | None, spec: ModelSpec) -> list[Path]:
    if not reference_images:
        return []
    if not spec.supports_reference:
        raise ValueError(
            f"Model {spec.alias!r} does not support reference_images. "
            f"Use one of: {', '.join(a for a, s in _ref_capable().items())}."
        )
    if len(reference_images) > spec.max_refs:
        raise ValueError(
            f"Model {spec.alias!r} accepts at most {spec.max_refs} reference image(s); "
            f"got {len(reference_images)}."
        )
    resolved: list[Path] = []
    for ref in reference_images:
        p = Path(ref).expanduser()
        if not p.is_file():
            raise ValueError(f"reference_images path not found or not a file: {ref!r}")
        resolved.append(p)
    return resolved


def _ref_capable() -> dict[str, ModelSpec]:
    from .pricing import MODELS
    return {a: s for a, s in MODELS.items() if s.supports_reference}
