"""Image generation via OpenRouter's chat/completions endpoint."""

from __future__ import annotations

import time as _time
from pathlib import Path
from typing import Any

from ..log import get_logger
from ..models import ModelSpec
from .client import (
    _check_key,
    _post,
    _raise_for_credits_or_status,
    _strip_or_prefix,
    note_billed_cost,
)

log = get_logger("openrouter")

# Default rendering size paired with each aspect — used to pre-crop reference
# images so a model's "match the reference aspect" instinct aligns with the
# requested aspect. The actual final video resolution comes from Settings.
_ASPECT_TO_REF_RES: dict[str, str] = {
    "9:16": "720x1280",
    "16:9": "1280x720",
    "1:1":  "1024x1024",
    "4:3":  "1024x768",
    "3:4":  "768x1024",
}

_REFERENCE_DESCRIBE_MODEL = "google/gemini-2.5-flash"
_REFERENCE_DESCRIBE_PROMPT = (
    "Describe this image as a reference for downstream image generation. "
    "If it shows a character (person, animal, stylized figure), describe "
    "the character's appearance: face, hair, build, clothing, expression. "
    "Note the visual style (photorealistic, illustrated, watercolor, "
    "anime, etc.) and dominant color palette. Be terse — 2 sentences max. "
    "No preamble, just the description."
)

_describe_reference_cache: dict[tuple[str, float], str] = {}


def _validate_refs(reference_images, spec: ModelSpec) -> list[Path]:
    if not reference_images:
        return []
    if not spec.supports_reference:
        raise ValueError(
            f"Model {spec.alias!r} does not support reference_images. "
            f"Use one that does (e.g. nano-banana, seedream)."
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


def _describe_reference_uncached(image_path: str) -> str:
    """Vision-LLM describe pass — caller-friendly wrapper hits OpenRouter
    with a vision-capable model and returns a short textual description
    used to anchor character / style consistency when only one reference
    image is being passed alongside the prompt.

    Failures fall back to an empty string so callers can degrade
    gracefully (the reference image still gets passed, just without an
    accompanying description).
    """
    import base64

    try:
        _check_key()
    except Exception:
        return ""
    p = Path(image_path)
    if not p.is_file():
        return ""
    suffix = p.suffix.lstrip(".").lower() or "png"
    b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    body = {
        "model": _REFERENCE_DESCRIBE_MODEL,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": _REFERENCE_DESCRIBE_PROMPT},
            {"type": "image_url", "image_url": {
                "url": f"data:image/{suffix};base64,{b64}"
            }},
        ]}],
    }
    try:
        resp = _post("/chat/completions", body, timeout=60.0)
        _raise_for_credits_or_status(resp)
        data = resp.json()
        note_billed_cost(data)
        content = data["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        return (content or "").strip()
    except Exception as e:
        log.warning("reference describe call failed (%s) — returning empty", e)
        return ""


def describe_reference(image_path: str | Path) -> str:
    """Cached single-image describe. Re-uses the result across an entire
    `parallax produce` run so a 9-scene plan with one shared character
    reference only triggers one vision call total.

    Cache key: (absolute_path, mtime). Editing the reference invalidates
    the cache automatically.
    """
    p = Path(image_path).resolve()
    try:
        mtime = p.stat().st_mtime
    except Exception:
        return ""
    key = (str(p), mtime)
    if key not in _describe_reference_cache:
        _describe_reference_cache[key] = _describe_reference_uncached(str(p))
    return _describe_reference_cache[key]


def analyze_image(
    image_path: str | Path,
    question: str | None = None,
    *,
    model: str = "google/gemini-2.5-flash",
) -> str:
    """Send an image to a vision model and return the text response.

    `question` is passed verbatim as the user prompt. When omitted a neutral
    describe prompt is used. `model` must be a raw OpenRouter model ID (not a
    parallax tier alias — this is a general-purpose vision call, not a
    generation call).

    Raises on API failure (unlike `describe_reference` which swallows errors
    for pipeline resilience — this is a user-facing command, so it should
    fail loud).
    """
    import base64

    _check_key()
    p = Path(image_path).expanduser()
    if not p.is_file():
        raise ValueError(f"Image not found: {p}")
    suffix = p.suffix.lstrip(".").lower() or "png"
    b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    prompt = question or "Describe this image in detail."
    body = {
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {
                "url": f"data:image/{suffix};base64,{b64}"
            }},
        ]}],
    }
    resp = _post("/chat/completions", body, timeout=60.0)
    _raise_for_credits_or_status(resp)
    data = resp.json()
    content = data["choices"][0]["message"]["content"]
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return (content or "").strip()


def _image_real(
    prompt: str, spec: ModelSpec, refs: list[Path], out_dir: Path | None,
    *, aspect_ratio: str | None = None, out_file: Path | None = None,
) -> Path:
    """Generate an image via OpenRouter's `/api/v1/images` endpoint.

    Request: `{model, prompt, aspect_ratio, input_references, **spec.request_params}`
    where `request_params` is the catalog's explicit `quality` / `resolution`
    choice (smallest output with a short side of at least 720 px). Response:
    `{data: [{b64_json, media_type}], usage: {cost}}` (verified live 2026-10-14).
    Reference images go in `input_references` as `image_url` parts.

    `aspect_ratio` (e.g. '9:16') must be one the catalog lists for the model;
    every reference is pre-cropped to that shape so the model's "match the
    reference aspect" instinct agrees with the request.
    """
    import base64

    from ..shim import output_dir

    _check_key()
    out = out_dir or output_dir()
    if out_file is None:
        out.mkdir(parents=True, exist_ok=True)
    model_id = _strip_or_prefix(spec.model_id)

    if aspect_ratio and not spec.supports_aspect(aspect_ratio):
        raise ValueError(
            f"Model {spec.alias!r} does not support aspect {aspect_ratio!r}; "
            f"supported: {', '.join(spec.aspect_ratios)}. Edit capabilities.aspect_ratios "
            f"in models/image.yaml only after verifying the live /api/v1/images/models list."
        )

    styled_prompt = prompt
    # Style consistency anchor via vision-LLM description of the first reference.
    if refs:
        try:
            ref_desc = describe_reference(refs[0])
        except Exception:
            ref_desc = ""
        if ref_desc:
            styled_prompt = (
                f"STYLE REFERENCE (the FIRST attached image is the canonical "
                f"character & visual-style reference — every generated image "
                f"MUST match this character's appearance and this exact visual "
                f"style; do NOT default to photoreal if the reference is "
                f"stylized): {ref_desc}\n\n"
                f"{prompt}"
            )

    target_aspect_res: str | None = (
        _ASPECT_TO_REF_RES.get(aspect_ratio) if aspect_ratio else None
    )

    input_references: list[dict[str, Any]] = []
    for ref in refs:
        ref_path = Path(ref)
        if target_aspect_res:
            from ..stills import crop_to_aspect
            try:
                ref_path = crop_to_aspect(ref_path, target_aspect_res)
            except Exception as e:
                log.warning(
                    "ref pre-crop failed for %s (%s) — passing original",
                    ref, e,
                )
        ref_b64 = base64.b64encode(ref_path.read_bytes()).decode("ascii")
        suffix = ref_path.suffix.lstrip(".").lower() or "png"
        input_references.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/{suffix};base64,{ref_b64}"},
        })

    body: dict[str, Any] = {"model": model_id, "prompt": styled_prompt, **spec.request_params}
    if aspect_ratio:
        body["aspect_ratio"] = aspect_ratio
    if input_references:
        body["input_references"] = input_references
    resp = _post("/images", body, timeout=300.0)
    _raise_for_credits_or_status(resp)
    data = resp.json()
    note_billed_cost(data)

    images = data.get("data") or []
    b64 = images[0].get("b64_json") if images else None
    if not b64:
        raise RuntimeError(
            f"OpenRouter {model_id} returned no image for prompt {prompt[:60]!r}"
        )
    media_type = images[0].get("media_type", "image/png")
    suffix = "jpg" if "jpeg" in media_type or "jpg" in media_type else "png"
    if out_file is not None:
        out_path = out_file
        out_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        out_path = out / f"openrouter_{spec.alias}_{int(_time.time()*1000)}.{suffix}"
    out_path.write_bytes(base64.b64decode(b64))
    return out_path
