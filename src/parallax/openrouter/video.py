"""Video generation via OpenRouter's /api/v1/videos async endpoint."""

from __future__ import annotations

import sys
import time as _time
from pathlib import Path
from typing import Any

from ..models import ModelSpec
from .client import (
    _auth_headers,
    _check_key,
    _post,
    _raise_for_credits_or_status,
    _strip_or_prefix,
    note_billed_cost,
    note_generation_id,
)

# Async video generation typically takes 30s – 5 min. Cap the wait so a stuck
# job surfaces as a clear timeout rather than hanging the producer subprocess.
# NOTE: _VIDEO_POLL_INTERVAL_S is defined on the package __init__ so that
# monkeypatch.setattr(openrouter, "_VIDEO_POLL_INTERVAL_S", 0.0) works in
# tests. This module reads it from the package namespace at runtime.
_VIDEO_POLL_TIMEOUT_S = 600.0  # 10 min


def _resolution_for_size(size: str | None, spec: ModelSpec) -> str | None:
    """Return a declared provider tier when a pixel size would contradict it.

    Catalog defaults are provider API tiers, while ``size`` is a WxH output
    geometry. In particular, do not send Veo Lite's economical 720p default
    alongside an explicit 1080-short-side size.
    """
    if not size:
        return None
    try:
        width, height = (int(part) for part in size.lower().split("x"))
    except ValueError:
        return None
    tier = f"{min(width, height)}p"
    return tier if tier in spec.resolutions else None

def resume_video_job(job_path: str | Path, out_dir: Path) -> Path:
    """Finish polling a previously accepted OpenRouter video job without resubmitting."""
    import httpx
    job = __import__("json").loads(Path(job_path).read_text())
    polling_url = job.get("polling_url")
    if not polling_url: raise ValueError(f"job receipt has no polling_url: {job_path}")
    headers = {"Authorization": _auth_headers()["Authorization"]}
    deadline = _time.monotonic() + _VIDEO_POLL_TIMEOUT_S
    last = job
    while _time.monotonic() < deadline:
        _time.sleep(getattr(sys.modules.get("parallax.openrouter"), "_VIDEO_POLL_INTERVAL_S", 5.0))
        response=httpx.get(polling_url, headers=headers, timeout=30.0); _raise_for_credits_or_status(response); last=response.json()
        if last.get("status") == "completed": break
        if last.get("status") in ("failed", "error"): raise RuntimeError(f"OpenRouter video job failed: {last!r}")
    else: raise RuntimeError(f"OpenRouter video poll timed out for {job.get('id')}")
    note_billed_cost(last); urls=last.get("unsigned_urls") or []
    if not urls: raise RuntimeError(f"completed job returned no unsigned_urls: {last!r}")
    download=httpx.get(urls[0],headers=headers,timeout=120.0); download.raise_for_status()
    out_dir.mkdir(parents=True,exist_ok=True); path=out_dir/f"openrouter_{job.get('model','video')}_{int(_time.time()*1000)}.mp4"; path.write_bytes(download.content)
    return path


def _video_real(
    prompt: str, spec: ModelSpec, image_path: Path | None, duration_s: float, out_dir: Path | None,
    *, size: str | None = None, aspect_ratio: str | None = None,
    resolution: str | None = None,
    end_image_path: Path | None = None,
    input_references: list[Path] | None = None,
    on_submit=None,
) -> Path:
    """Generate a video via OpenRouter's `/api/v1/videos` async endpoint.

    Three-step contract (verified live 2026-04-28):
      1. POST `/api/v1/videos` with `{model, prompt, duration, ...}` →
         202 `{id, polling_url, status: "pending"}`
      2. GET polling_url every few seconds until `status == "completed"`
         (or `"failed"` / `"error"`) → `{unsigned_urls: [...], usage: {cost, ...}}`
      3. GET each `unsigned_urls[i]` to download the mp4 bytes.

    Image conditioning (image-to-video) is supported via `frame_images` per
    the model's `supported_frame_images` capability. We pass it as a base64
    data URL on the `first_frame` slot when `image_path` is provided, and
    optionally on the `last_frame` slot when `end_image_path` is provided.
    """
    import base64
    import httpx

    from ..shim import output_dir

    _check_key()
    out = out_dir or output_dir()
    out.mkdir(parents=True, exist_ok=True)
    model_id = _strip_or_prefix(spec.model_id)

    body: dict[str, Any] = {
        "model": model_id,
        "prompt": prompt,
        "duration": spec.request_duration(duration_s),
        **spec.request_params,
    }
    if size:
        body["size"] = size
    if aspect_ratio:
        body["aspect_ratio"] = aspect_ratio
    # An explicit caller tier wins over the catalog default. Otherwise align a
    # declared tier with a supplied WxH size (e.g. 1080x1920 -> 1080p).
    # Only models with a catalog provider-tier default need automatic alignment;
    # this leaves every other model's established payload unchanged.
    requested_resolution = resolution or (
        _resolution_for_size(size, spec) if "resolution" in spec.request_params else None
    )
    if requested_resolution:
        body["resolution"] = requested_resolution
    if image_path is not None:
        ref_b64 = base64.b64encode(Path(image_path).read_bytes()).decode("ascii")
        suffix = Path(image_path).suffix.lstrip(".").lower() or "png"
        # frame_images schema: array of objects with `type`, `frame_type`,
        # and `image_url` (verified live 2026-04-28; an object instead of an
        # array, or `position` instead of `frame_type`, returns ZodError 400).
        frame_images: list[dict[str, Any]] = [{
            "type": "image_url",
            "frame_type": "first_frame",
            "image_url": {"url": f"data:image/{suffix};base64,{ref_b64}"},
        }]
        if end_image_path is not None:
            end_b64 = base64.b64encode(Path(end_image_path).read_bytes()).decode("ascii")
            end_suffix = Path(end_image_path).suffix.lstrip(".").lower() or "png"
            frame_images.append({
                "type": "image_url",
                "frame_type": "last_frame",
                "image_url": {"url": f"data:image/{end_suffix};base64,{end_b64}"},
            })
        body["frame_images"] = frame_images
    elif input_references:
        # input_references: character/style consistency anchors for text-to-video.
        # Mutually exclusive with frame_images — only sent when image_path is None.
        body["input_references"] = [
            {
                "type": "image_url",
                "image_url": {
                    "url": (
                        f"data:image/{Path(ref_path).suffix.lstrip('.').lower() or 'png'}"
                        f";base64,{base64.b64encode(Path(ref_path).read_bytes()).decode('ascii')}"
                    )
                },
            }
            for ref_path in input_references
        ]

    submit = _post("/videos", body, timeout=60.0)
    _raise_for_credits_or_status(submit)
    job = submit.json()
    note_generation_id(job.get("id"))
    polling_url = job.get("polling_url")
    if not polling_url:
        raise RuntimeError(f"OpenRouter video submit returned no polling_url: {job!r}")
    if on_submit:
        on_submit({"id": job.get("id"), "polling_url": polling_url, "model": spec.alias})

    # Polling URLs are absolute (the server returns them); they are signed
    # off the same OpenRouter backend so we send the auth header but not
    # Content-Type. Same for the unsigned download URLs below.
    poll_headers = {"Authorization": _auth_headers()["Authorization"]}

    # Read poll interval from the package namespace so monkeypatch works in tests.
    pkg = sys.modules.get("parallax.openrouter")
    poll_interval = getattr(pkg, "_VIDEO_POLL_INTERVAL_S", 5.0)

    deadline = _time.monotonic() + _VIDEO_POLL_TIMEOUT_S
    last_status: dict[str, Any] = job
    while _time.monotonic() < deadline:
        _time.sleep(poll_interval)
        poll = httpx.get(polling_url, headers=poll_headers, timeout=30.0)
        _raise_for_credits_or_status(poll)
        last_status = poll.json()
        status = last_status.get("status")
        if status == "completed":
            break
        if status in ("failed", "error"):
            raise RuntimeError(
                f"OpenRouter video generation failed for {model_id}: {last_status!r}"
            )
    else:
        raise RuntimeError(
            f"OpenRouter video poll timed out after {_VIDEO_POLL_TIMEOUT_S}s for {model_id} "
            f"(last status: {last_status.get('status')!r})"
        )

    note_billed_cost(last_status)
    urls = last_status.get("unsigned_urls") or []
    if not urls:
        raise RuntimeError(
            f"OpenRouter video {model_id} completed but returned no unsigned_urls: {last_status!r}"
        )
    download = httpx.get(urls[0], headers=poll_headers, timeout=120.0)
    download.raise_for_status()
    out_path = out / f"openrouter_{spec.alias}_{int(_time.time()*1000)}.mp4"
    out_path.write_bytes(download.content)
    return out_path
