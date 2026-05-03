"""Unified media-generation client.

Three entry points: `generate_image`, `generate_video`, `generate_tts`. Each
resolves an alias from `models/`, dispatches to OpenRouter, and returns a
local file path (plus per-word timings for tts).

Backends:
  - test mode (`PARALLAX_TEST_MODE=1`): all three route through `shim.py`.
    No network, no spend, fully deterministic.
  - real mode: routes through OpenRouter (image + video via the chat /
    videos endpoints, TTS via `/api/v1/audio/speech`). OPENROUTER_API_KEY
    is the single required env var.

The fallback chain encoded in models.ModelSpec.fallback_alias is honored on
RuntimeError: if the primary errors, the resolver walks one step down and
retries. Test-mode never fails, so fallbacks are real-mode-only.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import httpx

from . import runlog
from . import usage as _usage
from .context import current_session_id
from .log import get_logger
from .models import Kind, ModelSpec, resolve, resolve_chain
from .shim import (
    is_test_mode,
    output_dir,
    render_mock_image,
    render_mock_tts,
    render_mock_video,
)

log = get_logger("openrouter")


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def generate_image(
    prompt: str,
    alias: str,
    *,
    reference_images: list[str] | list[Path] | None = None,
    out_dir: Path | None = None,
    size: str | None = None,
    aspect_ratio: str | None = None,
) -> Path:
    """Generate an image. `size` is a passthrough hint like '1080x720' or
    '1080x1920' — note that some models (e.g. google/gemini-2.5-flash-image)
    silently ignore size and always return 1024×1024. For exact dimensions,
    pick a model that respects size or post-process via `parallax.stills`.

    `aspect_ratio` (e.g. '9:16') is the user-chosen aspect; when set it is
    forwarded to the upstream provider as a top-level body field and used
    to drive the prompt cue + reference pre-crop.
    """
    spec = resolve(alias, kind="image")
    refs = _validate_refs(reference_images, spec)
    # Test-mode (shim) resolution: prefer explicit `size`, then derive from
    # `aspect_ratio` so non-9:16 runs render the correct shape under shim,
    # then fall back to the legacy 1080x1920 default.
    from .settings import _ASPECT_TO_RESOLUTION as _ASPECT_RES
    test_resolution = size or _ASPECT_RES.get(aspect_ratio or "", "1080x1920")
    return _dispatch(
        kind="image",
        alias=alias,
        primary_call=lambda s: _image_real(prompt, s, refs, out_dir, size=size, aspect_ratio=aspect_ratio),
        test_call=lambda s: render_mock_image(
            prompt=prompt, model=s.alias, out_dir=out_dir,
            resolution=test_resolution,
        ),
        prompt=prompt,
        out_dir=out_dir,
    )


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


def generate_video(
    prompt: str,
    alias: str,
    *,
    image_path: Path | None = None,
    end_image_path: Path | None = None,
    input_references: list[Path] | None = None,
    duration_s: float = 5.0,
    out_dir: Path | None = None,
    size: str | None = None,
    aspect_ratio: str | None = None,
) -> Path:
    """Generate a video. `size` is a passthrough like '1280x720' / '720x1280';
    `aspect_ratio` is e.g. '9:16'. Each video model has its own
    `supported_sizes` list (verifiable via `/api/v1/videos/models`); requesting
    a size outside that list will fail at submit time with a clear error.

    `end_image_path` — when set, is sent as a `last_frame` conditioning image
    alongside the `first_frame` start image, directing the model to interpolate
    between the two.

    `input_references` — character/style reference images for visual consistency
    throughout the clip. Mutually exclusive with `image_path` (frame_images):
    when `image_path` is set, the model uses frame_images and ignores
    input_references. Only pass input_references when there is no image_path.
    """
    from .settings import _ASPECT_TO_RESOLUTION as _ASPECT_RES
    test_resolution = size or _ASPECT_RES.get(aspect_ratio or "", "1080x1920")
    return _dispatch(
        kind="video",
        alias=alias,
        primary_call=lambda spec: _video_real(
            prompt, spec, image_path, duration_s, out_dir,
            size=size, aspect_ratio=aspect_ratio,
            end_image_path=end_image_path,
            input_references=input_references,
        ),
        test_call=lambda spec: render_mock_video(
            prompt=prompt, model=spec.alias, duration_s=duration_s, out_dir=out_dir,
            resolution=test_resolution,
        ),
        prompt=prompt,
        out_dir=out_dir,
    )


def generate_tts(
    text: str,
    alias: str,
    *,
    voice: str = "default",
    voice_description: str | None = None,
    out_dir: Path | None = None,
    style: str | None = None,
    style_hint: str | None = None,
) -> tuple[Path, list[dict[str, Any]], float]:
    """Returns (audio_path, words [{word, start, end}], total_duration_s).

    Two backends are supported depending on the alias:
      chat_audio (tts-mini): OpenAI gpt-audio-mini via chat-completions audio
        modality. Inline [emotional] tags are stripped before sending.
      speech (tts-gemini): Gemini TTS via /api/v1/audio/speech. Inline
        [emotional] tags are passed through to the model unchanged — Gemini
        interprets them natively for expressive delivery.

    Voice names are backend-specific; see `parallax models show <alias>`.
    """
    out = out_dir or output_dir()
    spec0 = resolve(alias, kind="tts")

    if voice and voice != "default" and spec0.voices and voice not in spec0.voices:
        raise ValueError(
            f"Voice {voice!r} is not available for {spec0.alias!r}. "
            f"Valid voices: {', '.join(spec0.voices)}"
        )

    if is_test_mode():
        runlog.event("openrouter.tts.test", alias=alias, chars=len(text), voice=voice)
        result = render_mock_tts(text=text, voice=voice, out_dir=out)
        _record_usage(spec0, text, str(result[0]), duration_ms=0, cost_usd=0.0, test=True)
        return result

    def _tts_call(spec):
        model_id = _strip_or_prefix(spec.model_id)
        if spec.tts_backend == "speech":
            chosen_voice = voice if voice and voice != "default" else _GEMINI_TTS_DEFAULT_VOICE
            # Gemini: pass emotional tags through unchanged
            return _tts_real_speech(text, voice=chosen_voice, out_dir=out, model=model_id)
        else:
            chosen_voice = voice if voice and voice != "default" else _TTS_DEFAULT_VOICE
            # Default style applies only when neither style nor style_hint was
            # explicitly passed — passing style=None means "natural" (no prefix).
            effective_style = style if (style or style_hint) else _TTS_DEFAULT_STYLE
            # chat_audio backends don't interpret inline tags — strip them so the
            # model reads clean text rather than pronouncing the brackets literally.
            return _tts_real(
                strip_emotional_tags(text), voice=chosen_voice, out_dir=out,
                style=effective_style, style_hint=style_hint, model=model_id,
            )

    return _with_fallback(kind="tts", alias=alias, primary_call=_tts_call)


# ---------------------------------------------------------------------------
# Dispatcher with fallback chain
# ---------------------------------------------------------------------------

def _dispatch(
    *,
    kind: Kind,
    alias: str,
    primary_call,
    test_call,
    prompt: str,
    out_dir: Path | None,
) -> Path:
    spec0 = resolve(alias, kind=kind)  # validates kind early
    if is_test_mode():
        runlog.event(f"openrouter.{kind}.test", alias=alias, prompt_preview=prompt[:120])
        path = test_call(spec0)
        _record_usage(spec0, prompt, str(path), duration_ms=0, cost_usd=0.0, test=True)
        return path
    return _with_fallback(kind=kind, alias=alias, primary_call=primary_call)


def _with_fallback(*, kind: Kind, alias: str, primary_call):
    chain = resolve_chain(alias, kind=kind)
    last_error: Exception | None = None
    for spec in chain:
        try:
            result = _call_with_transient_retry(kind=kind, spec=spec, primary_call=primary_call)
            return result
        except InsufficientCreditsError:
            # 402 hits the wallet, not the model — every fallback is on the same
            # wallet, so trying alternates is a guaranteed waste. Re-raise loud.
            raise
        except Exception as e:
            last_error = e
            log.warning("%s alias %s failed (%s); falling back", kind, spec.alias, type(e).__name__)
    # Exhausted chain
    raise RuntimeError(
        f"openrouter: all fallbacks exhausted for {kind} alias {alias!r}: "
        f"{type(last_error).__name__}: {last_error}"
    ) from last_error


def _call_with_transient_retry(*, kind: Kind, spec, primary_call):
    """Run `primary_call(spec)` with retry-on-transient-network-error.

    Three attempts, exponential backoff (1s, 2s). A transient TLS / connection
    / read / 5xx error is the network's fault, not the model's — falling
    straight through to a different model in `_with_fallback` would (a) waste
    the chance to recover the original model and (b) silently change visual
    style mid-run. Non-transient errors (validation, safety, no images
    returned) raise immediately so `_with_fallback` can move on.
    """
    last_err: Exception | None = None
    for attempt in range(3):
        t0 = time.monotonic()
        try:
            runlog.event(
                f"openrouter.{kind}.call",
                alias=spec.alias, model_id=spec.model_id,
                attempt=attempt + 1,
            )
            result = primary_call(spec)
            duration_ms = int((time.monotonic() - t0) * 1000)
            runlog.event(
                f"openrouter.{kind}.response",
                alias=spec.alias, model_id=spec.model_id,
                duration_ms=duration_ms, ok=True,
                attempt=attempt + 1,
            )
            _record_usage(spec, "", _path_of(result), duration_ms=duration_ms,
                          cost_usd=spec.cost_usd, test=False)
            return result
        except InsufficientCreditsError:
            raise
        except Exception as e:
            duration_ms = int((time.monotonic() - t0) * 1000)
            transient = _is_transient_network_error(e)
            runlog.event(
                f"openrouter.{kind}.error",
                level="WARN",
                alias=spec.alias, model_id=spec.model_id,
                duration_ms=duration_ms,
                error=f"{type(e).__name__}: {e}",
                attempt=attempt + 1, transient=transient,
                fallback_alias=spec.fallback_alias,
            )
            if not transient or attempt == 2:
                raise
            last_err = e
            log.warning(
                "%s alias %s transient %s on attempt %d/3 — retrying",
                kind, spec.alias, type(e).__name__, attempt + 1,
            )
            time.sleep(2 ** attempt)  # 1s, 2s
    assert False, f"unreachable: retries exhausted ({last_err})"


def _is_transient_network_error(e: Exception) -> bool:
    """True for TLS / connection / read / 5xx errors worth retrying.

    Match by class-name + message rather than importing httpx types — the
    concrete class can shift across versions and call layers. False for
    auth (401/403), validation, safety, and "no images returned" errors —
    those won't be fixed by waiting.
    """
    name = type(e).__name__
    msg = str(e).lower()
    transient_class_markers = (
        "ReadError", "ReadTimeout", "ConnectError", "ConnectTimeout",
        "RemoteProtocolError", "SSLError", "SSLZeroReturnError",
        "ProtocolError", "IncompleteRead", "Timeout",
    )
    if any(m in name for m in transient_class_markers):
        return True
    transient_msg_markers = (
        "ssl", "tls", "connection reset", "connection aborted",
        "temporarily unavailable", "bad gateway", "gateway timeout",
        "service unavailable", "internal server error",
        " 502", " 503", " 504",
    )
    return any(m in msg for m in transient_msg_markers)


# ---------------------------------------------------------------------------
# Real-mode implementations — pending HTTP contract verification
# ---------------------------------------------------------------------------

def _check_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is required for real-mode media calls. "
            "Set it, or export PARALLAX_TEST_MODE=1 to use stubs."
        )
    return key


# ---------------------------------------------------------------------------
# Shared HTTP client config — single source of truth for endpoint URL +
# auth headers. Every callsite that hits OpenRouter goes through these
# helpers so the URL, attribution headers, and key handling live in
# exactly one place.
# ---------------------------------------------------------------------------

_BASE = "https://openrouter.ai/api/v1"
_BASE_HEADERS = {
    "HTTP-Referer": "https://github.com/ianjamesburke/parallax-v0",
    "X-Title": "parallax",
}


def _auth_headers() -> dict[str, str]:
    return {
        **_BASE_HEADERS,
        "Authorization": f"Bearer {_check_key()}",
        "Content-Type": "application/json",
    }


def _post(path: str, body: dict, *, timeout: float = 300.0) -> "httpx.Response":
    """POST JSON to <_BASE>/<path>. Returns the raw response."""
    import httpx
    return httpx.post(f"{_BASE}{path}", headers=_auth_headers(), json=body, timeout=timeout)


def _get(path: str, *, timeout: float = 30.0, auth_only: bool = False) -> "httpx.Response":
    """GET <_BASE>/<path>. `auth_only` strips Content-Type from headers
    (some GETs to OpenRouter / signed-URL fetches reject it)."""
    import httpx
    headers = _auth_headers()
    if auth_only:
        headers.pop("Content-Type", None)
    return httpx.get(f"{_BASE}{path}", headers=headers, timeout=timeout)


def _stream_post(path: str, body: dict, *, timeout: float = 300.0):
    """Stream a POST to <_BASE>/<path>. Caller uses it as a context manager
    (`with _stream_post(...) as resp: ...`) and iterates resp.iter_lines()."""
    import httpx
    return httpx.stream(
        "POST", f"{_BASE}{path}", headers=_auth_headers(), json=body, timeout=timeout,
    )


class InsufficientCreditsError(RuntimeError):
    """Raised when OpenRouter rejects a call with HTTP 402 or pre-flight credits
    check shows balance below the minimum. Carries the top-up URL so the CLI
    layer can render a clean operator-facing error.
    """


@dataclass(frozen=True)
class CreditsBalance:
    total: float
    used: float
    remaining: float


def check_credits(min_balance_usd: float = 0.50) -> CreditsBalance:
    """Pre-flight credits check. Hits /api/v1/credits and raises
    `InsufficientCreditsError` when remaining balance is below `min_balance_usd`.

    Returns the `CreditsBalance` on success so callers can log the headroom.

    `min_balance_usd` default 0.50 covers a 4-scene produce run (~$1-2 worth
    of stills + i2v + TTS); raise it for longer projects. Set to 0.0 to make
    the check informational only.
    """
    resp = _get("/credits", timeout=15.0)
    _raise_for_credits_or_status(resp)
    data = resp.json().get("data") or {}
    total = float(data.get("total_credits", 0.0))
    used = float(data.get("total_usage", 0.0))
    remaining = total - used
    balance = CreditsBalance(total=total, used=used, remaining=remaining)
    if remaining < min_balance_usd:
        raise InsufficientCreditsError(
            f"OpenRouter credits ${remaining:.2f} (below threshold "
            f"${min_balance_usd:.2f}). Total ${total:.2f}, used ${used:.2f}. "
            f"Top up at https://openrouter.ai/settings/credits"
        )
    return balance


def _raise_for_credits_or_status(resp: "httpx.Response") -> None:
    """Specialized response check: 402 → InsufficientCreditsError (no retries,
    no fallback), other 4xx/5xx → standard `HTTPStatusError` for the caller's
    retry/fallback machinery to inspect.
    """
    if resp.status_code == 402:
        try:
            msg = (resp.json().get("error") or {}).get("message", "")
        except Exception:
            msg = resp.text[:200]
        raise InsufficientCreditsError(
            f"OpenRouter rejected with 402: {msg or 'Insufficient credits'}. "
            f"Top up at https://openrouter.ai/settings/credits"
        )
    resp.raise_for_status()


_ASPECT_CUE_TEXTS: dict[str, str] = {
    "9:16": "Vertical 9:16 portrait orientation, taller than wide, ",
    "16:9": "Horizontal 16:9 widescreen orientation, wider than tall, ",
    "1:1":  "Square 1:1 orientation, equal width and height, ",
    "4:3":  "Landscape 4:3 orientation, wider than tall, ",
    "3:4":  "Portrait 3:4 orientation, taller than wide, ",
}


# Default rendering size paired with each aspect — used to pre-crop reference
# images so Gemini's "match the reference aspect" instinct aligns with the
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
        content = data["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        return (content or "").strip()
    except Exception as e:
        log.warning("reference describe call failed (%s) — returning empty", e)
        return ""


_describe_reference_cache: dict[tuple[str, float], str] = {}


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


def _aspect_cue(aspect_ratio: str | None) -> str:
    """Textual prefix to nudge image models that ignore the body `aspect_ratio` field.

    Returns "" when aspect is unknown or unset (no cue prepended). Returns the
    explicit human-readable directive otherwise. Belt-and-suspenders only — the
    body field (when honored) is still authoritative; this cue gets the model
    to look at the request twice.
    """
    if not aspect_ratio:
        return ""
    return _ASPECT_CUE_TEXTS.get(aspect_ratio, "")


def _strip_or_prefix(model_id: str) -> str:
    """Strip the leading `openrouter/` namespace from a pricing model_id.

    pricing.py prefixes everything with `openrouter/` so the dispatcher can
    tell at a glance which backend a row belongs to. The OpenRouter API
    itself takes plain `<vendor>/<model>` slugs.
    """
    return model_id[len("openrouter/"):] if model_id.startswith("openrouter/") else model_id


def _image_real(
    prompt: str, spec: ModelSpec, refs: list[Path], out_dir: Path | None,
    *, size: str | None = None, aspect_ratio: str | None = None,
) -> Path:
    """Generate an image via OpenRouter's chat/completions endpoint.

    OpenRouter exposes image generation through the OpenAI-compatible chat
    endpoint with `modalities=["image","text"]`. The response message has
    an `images` list whose entries carry `image_url.url` as a base64 data
    URL. Reference images (for image-edit / nano-banana style models) are
    encoded into the user message as `image_url` content parts alongside
    the prompt text.

    `aspect_ratio` is the caller-supplied target ratio (e.g. '9:16'). When
    set, it is sent on the request body as `aspect_ratio` (Gemini honors
    this), prepended to the prompt as a textual cue, and used to pre-crop
    every reference image to the matching shape.
    """
    import base64
    import time as _time

    _check_key()
    out = out_dir or output_dir()
    out.mkdir(parents=True, exist_ok=True)
    model_id = _strip_or_prefix(spec.model_id)

    # Textual aspect cue — belt-and-suspenders alongside the body field below.
    # Gemini image models occasionally ignore the `aspect_ratio` body field for
    # certain prompt types and return landscape. Prepending a strong textual
    # cue dramatically improves compliance without affecting models that
    # already honor the body field (it's just extra context to them).
    aspect_cue = _aspect_cue(aspect_ratio)
    cued_prompt = f"{aspect_cue}{prompt}" if aspect_cue else prompt

    # Style consistency anchor. Image-gen models drop visual style cues
    # from reference images when the prompt describes a different aesthetic
    # than the reference embodies. Verified live: a Simpsons-style cartoon
    # reference produced photorealistic output because every scene prompt
    # described photoreal subjects. Mitigation: vision-LLM describe the
    # first (style) reference once per run (cached), prepend the description
    # to the prompt so the textual instruction reinforces what the image
    # is showing. The combination (image + matching textual description)
    # holds the line even when the image alone gets ignored. Always-on,
    # regardless of ref count — when 2-3 refs are passed, the first is
    # still the style anchor and the textual cue still helps.
    if refs:
        try:
            ref_desc = describe_reference(refs[0])
        except Exception:
            ref_desc = ""
        if ref_desc:
            cued_prompt = (
                f"STYLE REFERENCE (the FIRST attached image is the canonical "
                f"character & visual-style reference — every generated image "
                f"MUST match this character's appearance and this exact visual "
                f"style; do NOT default to photoreal if the reference is "
                f"stylized): {ref_desc}\n\n"
                f"{cued_prompt}"
            )

    # Pre-crop reference images to the target aspect. Gemini (and most
    # image-edit models) bias their output aspect to match the reference,
    # which silently overrides the body `aspect_ratio` field and the
    # prompt cue both. Verified: a 398x510 (~4:5) reference produced
    # 896x1152 (~4:5) output even with explicit "9:16 portrait" in the
    # prompt. Pre-cropping each ref to the chosen aspect makes Gemini's
    # "match the reference" instinct align with the requested aspect.
    target_aspect_res: str | None = (
        _ASPECT_TO_REF_RES.get(aspect_ratio) if aspect_ratio else None
    )

    user_content: list[dict[str, Any]] = [{"type": "text", "text": cued_prompt}]
    for ref in refs:
        ref_path = Path(ref)
        if target_aspect_res:
            from .stills import crop_to_aspect
            try:
                ref_path = crop_to_aspect(ref_path, target_aspect_res)
            except Exception as e:
                log.warning(
                    "ref pre-crop failed for %s (%s) — passing original",
                    ref, e,
                )
        ref_b64 = base64.b64encode(ref_path.read_bytes()).decode("ascii")
        suffix = ref_path.suffix.lstrip(".").lower() or "png"
        user_content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/{suffix};base64,{ref_b64}"},
        })

    body: dict[str, Any] = {
        "model": model_id,
        "messages": [{"role": "user", "content": user_content}],
        "modalities": ["image", "text"],
    }
    if aspect_ratio:
        # Top-level body field — OpenRouter passes unknown fields through to
        # the upstream provider. Gemini image models honor `aspect_ratio`.
        body["aspect_ratio"] = aspect_ratio
    if size:
        # Passthrough hint. NB: gemini-2.5-flash-image historically ignored
        # `size` and always returned 1024×1024 — `aspect_ratio` (set above)
        # is the param Gemini actually honors.
        body["size"] = size
    resp = _post("/chat/completions", body, timeout=300.0)
    _raise_for_credits_or_status(resp)
    data = resp.json()

    images = data.get("choices", [{}])[0].get("message", {}).get("images") or []
    if not images:
        raise RuntimeError(
            f"OpenRouter {model_id} returned no images for prompt {prompt[:60]!r}"
        )
    url = images[0].get("image_url", {}).get("url", "")
    if not url.startswith("data:"):
        raise RuntimeError(
            f"OpenRouter {model_id} returned non-data-URL image: {url[:80]!r}"
        )
    header, _, b64 = url.partition(",")
    if not b64:
        raise RuntimeError(f"OpenRouter {model_id} image data URL missing payload: {header}")
    suffix = "png" if "png" in header else ("jpg" if "jpeg" in header or "jpg" in header else "png")
    out_path = out / f"openrouter_{spec.alias}_{int(_time.time()*1000)}.{suffix}"
    out_path.write_bytes(base64.b64decode(b64))
    return out_path


# Async video generation typically takes 30s – 5 min. Cap the wait so a stuck
# job surfaces as a clear timeout rather than hanging the producer subprocess.
_VIDEO_POLL_INTERVAL_S = 5.0
_VIDEO_POLL_TIMEOUT_S = 600.0  # 10 min


def _video_real(
    prompt: str, spec: ModelSpec, image_path: Path | None, duration_s: float, out_dir: Path | None,
    *, size: str | None = None, aspect_ratio: str | None = None,
    end_image_path: Path | None = None,
    input_references: list[Path] | None = None,
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
    import time as _time
    import httpx

    _check_key()
    out = out_dir or output_dir()
    out.mkdir(parents=True, exist_ok=True)
    model_id = _strip_or_prefix(spec.model_id)

    body: dict[str, Any] = {
        "model": model_id,
        "prompt": prompt,
        "duration": int(round(duration_s)),
    }
    if size:
        body["size"] = size
    if aspect_ratio:
        body["aspect_ratio"] = aspect_ratio
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
        # Per OpenRouter docs, frame_images takes precedence and silently suppresses
        # input_references when both are present; the guard above makes this explicit.
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
    polling_url = job.get("polling_url")
    if not polling_url:
        raise RuntimeError(f"OpenRouter video submit returned no polling_url: {job!r}")

    # Polling URLs are absolute (the server returns them); they are signed
    # off the same OpenRouter backend so we send the auth header but not
    # Content-Type. Same for the unsigned download URLs below.
    poll_headers = {"Authorization": _auth_headers()["Authorization"]}
    deadline = _time.monotonic() + _VIDEO_POLL_TIMEOUT_S
    last_status: dict[str, Any] = job
    while _time.monotonic() < deadline:
        _time.sleep(_VIDEO_POLL_INTERVAL_S)
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


# ---------------------------------------------------------------------------
# TTS — OpenAI gpt-audio-mini via OpenRouter chat-completions audio modality
# ---------------------------------------------------------------------------
#
# OpenRouter exposes OpenAI's gpt-audio-mini through `/chat/completions`
# with `modalities=["text", "audio"]` and `audio={"voice": ..., "format":
# "pcm16"}`. Audio output is delivered in SSE chunks; we accumulate the
# base64-encoded pcm16 frames, write a 24kHz mono WAV, and run forced
# alignment on it for word timestamps.

_TTS_DEFAULT_VOICE = "nova"
_TTS_DEFAULT_MODEL = "openai/gpt-audio-mini"
_TTS_PCM_SAMPLE_RATE = 24_000
_TTS_PCM_BYTES_PER_SAMPLE = 2  # 16-bit

_GEMINI_TTS_DEFAULT_VOICE = "Kore"

# Matches [tag] tokens anywhere in text — used to strip inline emotional
# tags before sending to backends that don't interpret them (chat_audio).
_EMOTIONAL_TAG_RE = re.compile(r"\[[^\]]+\]")


def strip_emotional_tags(text: str) -> str:
    """Remove inline [emotional] tags and normalize whitespace.

    Used for chat_audio backends (e.g. gpt-audio-mini) that would pronounce
    brackets literally. Gemini TTS (speech backend) receives tags unchanged.
    """
    return re.sub(r" {2,}", " ", _EMOTIONAL_TAG_RE.sub("", text)).strip()


# Style presets prepend a directive to the spoken text. gpt-audio-mini
# follows freeform delivery hints in the user message.
_TTS_STYLE_PRESETS: dict[str, str] = {
    "rapid_fire": (
        "Read this as a rapid-fire commercial — talk fast, no pauses, urgent, "
        "energetic. Speak quickly: "
    ),
    "fast": "Say this quickly, with high energy, like a fast-paced TikTok ad: ",
    "calm": "Read this in a calm, measured, conversational tone: ",
    "natural": "",  # baseline — no directive
}
_TTS_DEFAULT_STYLE = "rapid_fire"


def _tts_resolve_directive(*, style: str | None, style_hint: str | None) -> str:
    if style_hint:
        return style_hint if style_hint.endswith(": ") else style_hint.rstrip() + " "
    if style is None:
        return ""
    if style not in _TTS_STYLE_PRESETS:
        raise ValueError(
            f"Unknown TTS style {style!r}. Available presets: "
            f"{sorted(_TTS_STYLE_PRESETS)}, or pass `style_hint` for a freeform directive."
        )
    return _TTS_STYLE_PRESETS[style]


def _tts_evenly_distributed_words(text: str, duration_s: float) -> list[dict[str, Any]]:
    tokens = text.split()
    if not tokens or duration_s <= 0:
        return []
    per_word = duration_s / len(tokens)
    return [
        {
            "word": t,
            "start": round(i * per_word, 3),
            "end": round((i + 1) * per_word, 3),
        }
        for i, t in enumerate(tokens)
    ]


def _tts_real(
    text: str,
    *,
    voice: str = _TTS_DEFAULT_VOICE,
    out_dir: Path,
    model: str = _TTS_DEFAULT_MODEL,
    style: str | None = None,
    style_hint: str | None = None,
) -> tuple[Path, list[dict[str, Any]], float]:
    """Synthesize via gpt-audio-mini through OpenRouter. Returns (wav, words, duration)."""
    import base64
    import json as _json
    import time as _time
    import wave

    out_dir.mkdir(parents=True, exist_ok=True)
    _check_key()

    directive = _tts_resolve_directive(style=style, style_hint=style_hint)
    spoken = directive + text if directive else text
    # Anchor the user content so the model says the script literally
    # rather than treating it as conversational input.
    user_message = f"Say this exactly, with no preamble or commentary: {spoken}"

    body = {
        "model": model,
        "messages": [{"role": "user", "content": user_message}],
        "modalities": ["text", "audio"],
        "audio": {"voice": voice, "format": "pcm16"},
        "stream": True,
    }

    pcm_bytes = bytearray()
    transcript = ""
    with _stream_post("/chat/completions", body, timeout=300.0) as response:
        if response.status_code != 200:
            raise RuntimeError(
                f"OpenRouter TTS request failed ({response.status_code}) for "
                f"model={model!r} voice={voice!r}: "
                f"{response.read().decode('utf-8', 'replace')[:500]}"
            )
        for line in response.iter_lines():
            if not line.startswith("data: "):
                continue
            payload = line[6:]
            if payload == "[DONE]":
                break
            try:
                event = _json.loads(payload)
            except _json.JSONDecodeError:
                continue
            choices = event.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            audio = delta.get("audio")
            if not isinstance(audio, dict):
                continue
            data_b64 = audio.get("data")
            if data_b64:
                pcm_bytes.extend(base64.b64decode(data_b64))
            tx = audio.get("transcript")
            if tx:
                transcript += tx

    if not pcm_bytes:
        raise RuntimeError(
            f"OpenRouter TTS stream produced no audio for {text[:60]!r}"
        )

    safe_voice = "".join(c for c in voice if c.isalnum() or c in ("-", "_")).strip() or "voice"
    wav_path = out_dir / f"openrouter_tts_{safe_voice}_{int(_time.time()*1000)}.wav"
    with wave.open(str(wav_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(_TTS_PCM_BYTES_PER_SAMPLE)
        w.setframerate(_TTS_PCM_SAMPLE_RATE)
        w.writeframes(bytes(pcm_bytes))

    duration_s = len(pcm_bytes) / (_TTS_PCM_SAMPLE_RATE * _TTS_PCM_BYTES_PER_SAMPLE)

    # Forced alignment via WhisperX gives ~50ms-precise word boundaries from
    # the produced wav. Falls back to evenly-distributed timings only on
    # alignment failure (loudly logged so it's not silent).
    try:
        from . import forced_align
        words = forced_align.align_words(wav_path)
    except Exception as exc:
        import logging
        logging.getLogger("parallax.openrouter.tts").warning(
            "forced_align failed (%s); falling back to evenly-distributed word timings",
            exc,
        )
        words = _tts_evenly_distributed_words(transcript or text, duration_s)

    return wav_path, words, duration_s


def _tts_real_speech(
    text: str,
    *,
    voice: str,
    out_dir: Path,
    model: str,
) -> tuple[Path, list[dict[str, Any]], float]:
    """Synthesize via /api/v1/audio/speech (Gemini TTS on OpenRouter).

    Inline [emotional] tags are passed through unchanged — the Gemini model
    interprets them natively for expressive delivery. Use single-word
    gerund/adjective/adverb form: [dramatically], [whispering], [rapidly],
    [excitedly], [softly].

    OpenRouter's /audio/speech only accepts response_format "mp3" or "pcm"
    (verified live 2026-04-30 — "wav" returns ZodError 400). We request "pcm"
    and wrap the raw bytes into a WAV ourselves, consistent with _tts_real.

    Returns (wav_path, words, duration_s). Word timings come from forced
    alignment (WhisperX), falling back to evenly-distributed if alignment
    fails.
    """
    import time as _time
    import wave

    out_dir.mkdir(parents=True, exist_ok=True)
    _check_key()

    body = {
        "model": model,
        "input": text,
        "voice": voice,
        "response_format": "pcm",
    }

    resp = _post("/audio/speech", body, timeout=120.0)
    _raise_for_credits_or_status(resp)

    if not resp.content:
        raise RuntimeError(
            f"OpenRouter Gemini TTS returned empty audio for {text[:60]!r}"
        )

    safe_voice = "".join(c for c in voice if c.isalnum() or c in ("-", "_")).strip() or "voice"
    wav_path = out_dir / f"openrouter_tts_{safe_voice}_{int(_time.time()*1000)}.wav"
    with wave.open(str(wav_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(_TTS_PCM_BYTES_PER_SAMPLE)
        w.setframerate(_TTS_PCM_SAMPLE_RATE)
        w.writeframes(resp.content)

    duration_s = len(resp.content) / (_TTS_PCM_SAMPLE_RATE * _TTS_PCM_BYTES_PER_SAMPLE)

    # Forced alignment for word timestamps, same as chat_audio backend.
    try:
        from . import forced_align
        words = forced_align.align_words(wav_path)
    except Exception as exc:
        import logging
        logging.getLogger("parallax.openrouter.tts").warning(
            "forced_align failed (%s); falling back to evenly-distributed word timings",
            exc,
        )
        words = _tts_evenly_distributed_words(
            strip_emotional_tags(text),  # strip tags for word-count accuracy
            duration_s,
        )

    return wav_path, words, duration_s


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _path_of(result: Any) -> str:
    if isinstance(result, tuple) and result and isinstance(result[0], (str, Path)):
        return str(result[0])
    if isinstance(result, (str, Path)):
        return str(result)
    return ""


def _record_usage(
    spec: ModelSpec, prompt: str, output_path: str, *, duration_ms: int, cost_usd: float, test: bool,
) -> None:
    _usage.record(
        session_id=current_session_id.get(),
        backend="openrouter" if not test else "shim",
        alias=spec.alias,
        fal_id=spec.model_id,
        tier=spec.tier,
        prompt=prompt[:120],
        output_path=output_path,
        duration_ms=duration_ms,
        cost_usd=cost_usd,
        test_mode=test,
    )
