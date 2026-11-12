"""Video generation via OpenRouter's /api/v1/videos async endpoint."""

from __future__ import annotations

import re
import sys
import time as _time
from pathlib import Path
from typing import Any

from ..models import ModelSpec
from .client import (
    _check_key,
    _download_url,
    _openrouter_get,
    _post,
    _raise_for_credits_or_status,
    _safe_url_for_message,
    _strip_or_prefix,
    UntrustedURLError,
    is_openrouter_url,
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

# ---------------------------------------------------------------------------
# Job receipts
# ---------------------------------------------------------------------------
# A receipt is written right after OpenRouter accepts a submit so an
# interrupted run can resume polling without paying twice. Receipts live in
# project folders that may be shared (Drive mirrors), so a receipt is untrusted
# input: it is only honoured if it carries Parallax's marker/schema, sits
# inside the expected root, names an OpenRouter polling URL, and its `out`
# stays inside that root.

JOB_RECEIPT_MARKER = "parallax.openrouter.video_job"
JOB_RECEIPT_SCHEMA = 1
_SAFE_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class JobReceiptError(ValueError):
    """A job receipt was not created by Parallax, is malformed, or points outside its root."""


def _is_within(path: Path, root: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root.resolve())
    except (OSError, RuntimeError):
        return False


def write_job_receipt(job_path: Path, job: dict[str, Any], *, out: Path, estimate_usd: float | None = None) -> dict[str, Any]:
    """Write a Parallax-marked receipt for an accepted OpenRouter video job."""
    payload: dict[str, Any] = {
        "parallax": JOB_RECEIPT_MARKER,
        "schema": JOB_RECEIPT_SCHEMA,
        "id": job.get("id"),
        "polling_url": job.get("polling_url"),
        "model": job.get("model"),
        "out": str(Path(out).resolve()),
        "created_at": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
    }
    if estimate_usd is not None:
        payload["estimate_usd"] = estimate_usd
    Path(job_path).write_text(__import__("json").dumps(payload, indent=2))
    return payload


def load_job_receipt(job_path: str | Path, *, root: str | Path) -> dict[str, Any]:
    """Load and validate a receipt; raise JobReceiptError unless it is safe to resume.

    `root` is the project / output directory the receipt must live in and
    whose subtree its `out` must stay inside. Relative `out` values resolve
    against `root`. The returned dict has `out` normalised to an absolute path.
    """
    import json

    path, root_path = Path(job_path), Path(root)
    if not path.is_file():
        raise JobReceiptError(f"job receipt not found: {path}")
    if not _is_within(path, root_path):
        raise JobReceiptError(f"job receipt {path} is outside the project directory {root_path.resolve()}; refusing to resume it")
    try:
        job = json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise JobReceiptError(f"job receipt {path} is not valid JSON: {exc}") from None
    if not isinstance(job, dict) or job.get("parallax") != JOB_RECEIPT_MARKER:
        raise JobReceiptError(f"{path} is not a Parallax video job receipt (missing marker); refusing to resume it")
    if job.get("schema") != JOB_RECEIPT_SCHEMA:
        raise JobReceiptError(f"job receipt {path} has unsupported schema {job.get('schema')!r}; refusing to resume it")
    for key in ("id", "polling_url", "model", "out"):
        if not isinstance(job.get(key), str) or not job[key]:
            raise JobReceiptError(f"job receipt {path} is missing or has an invalid {key!r}; refusing to resume it")
    if not is_openrouter_url(job["polling_url"]):
        raise JobReceiptError(
            f"job receipt {path} polls a non-OpenRouter URL ({_safe_url_for_message(job['polling_url'])}); "
            "refusing to resume it or send credentials there"
        )
    if not _SAFE_MODEL_RE.match(job["model"]):
        raise JobReceiptError(f"job receipt {path} has an invalid model name; refusing to resume it")
    out = Path(job["out"])
    out = out if out.is_absolute() else root_path / out
    if not _is_within(out, root_path):
        raise JobReceiptError(f"job receipt {path} writes outside the project directory {root_path.resolve()}; refusing to resume it")
    return {**job, "out": str(out.resolve())}


def _poll_and_download(polling_url: str, out_path: Path, *, label: str, first: dict[str, Any] | None = None) -> Path:
    """Poll an OpenRouter job to completion and download the first result.

    All network I/O goes through `_openrouter_get` (key only for OpenRouter
    hosts, refused otherwise) and `_download_url` (no key off-allowlist).
    """
    poll_interval = getattr(sys.modules.get("parallax.openrouter"), "_VIDEO_POLL_INTERVAL_S", 5.0)
    deadline = _time.monotonic() + _VIDEO_POLL_TIMEOUT_S
    last: dict[str, Any] = first or {}
    while _time.monotonic() < deadline:
        _time.sleep(poll_interval)
        poll = _openrouter_get(polling_url, timeout=30.0)
        _raise_for_credits_or_status(poll)
        last = poll.json()
        status = last.get("status")
        if status == "completed":
            break
        if status in ("failed", "error"):
            raise RuntimeError(f"OpenRouter video generation failed for {label}: {last!r}")
    else:
        raise RuntimeError(
            f"OpenRouter video poll timed out after {_VIDEO_POLL_TIMEOUT_S}s for {label} "
            f"(last status: {last.get('status')!r})"
        )
    note_billed_cost(last)
    urls = last.get("unsigned_urls") or []
    if not urls:
        raise RuntimeError(f"OpenRouter video {label} completed but returned no unsigned_urls: {last!r}")
    download = _download_url(urls[0], timeout=120.0)
    download.raise_for_status()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(download.content)
    return out_path


def resume_video_job(job_path: str | Path, out_dir: Path, *, root: str | Path) -> Path:
    """Finish polling a previously accepted OpenRouter video job without resubmitting.

    The receipt is validated with `load_job_receipt(job_path, root=root)` first;
    an untrusted receipt raises JobReceiptError before any network request.
    """
    job = load_job_receipt(job_path, root=root)
    out_path = Path(out_dir) / f"openrouter_{job['model']}_{int(_time.time()*1000)}.mp4"
    return _poll_and_download(job["polling_url"], out_path, label=job["id"], first=job)


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
    if not is_openrouter_url(polling_url):
        raise UntrustedURLError(
            f"OpenRouter video submit returned a non-OpenRouter polling_url "
            f"({_safe_url_for_message(polling_url)}); refusing to send credentials there"
        )
    if on_submit:
        on_submit({"id": job.get("id"), "polling_url": polling_url, "model": spec.alias})

    # Polling URLs are absolute (the server returns them). The shared helper
    # sends the key only to OpenRouter hosts and downloads CDN URLs without it.
    out_path = out / f"openrouter_{spec.alias}_{int(_time.time()*1000)}.mp4"
    return _poll_and_download(polling_url, out_path, label=model_id, first=job)
