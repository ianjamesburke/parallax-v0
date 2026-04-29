"""Unified media-generation client.

Three entry points: `generate_image`, `generate_video`, `generate_tts`. Each
resolves an alias from `pricing.py`, dispatches to the right backend, and
returns a local file path (plus per-word timings for tts).

Backends:
  - test mode (`PARALLAX_TEST_MODE=1`): all three route through `shim.py`.
    No network, no spend, fully deterministic.
  - real mode: routes through OpenRouter's media APIs. The HTTP request shape
    varies per kind and is intentionally NOT yet implemented here — the
    contract gets verified once an OPENROUTER_API_KEY is in hand. Calling
    real-mode raises NotImplementedError with a clear message.
  - voice escape hatch: when `voice` starts with `eleven:`, `generate_tts`
    delegates to ElevenLabs directly (the brand-locked-voice path).

The fallback chain encoded in pricing.ModelSpec.fallback_alias is honored on
RuntimeError: if the primary errors, the resolver walks one step down and
retries. Test-mode never fails, so fallbacks are real-mode-only.
"""

from __future__ import annotations

import os
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
from .pricing import Kind, ModelSpec, resolve, resolve_chain
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
) -> Path:
    """Generate an image. `size` is a passthrough hint like '1080x720' or
    '1080x1920' — note that some models (e.g. google/gemini-2.5-flash-image)
    silently ignore size and always return 1024×1024. For exact dimensions,
    pick a model that respects size or post-process via tools_video.
    """
    spec = resolve(alias, kind="image")
    refs = _validate_refs(reference_images, spec)
    return _dispatch(
        kind="image",
        alias=alias,
        primary_call=lambda s: _image_real(prompt, s, refs, out_dir, size=size),
        test_call=lambda s: render_mock_image(
            prompt=prompt, model=s.alias, out_dir=out_dir,
            resolution=size or "1080x1920",
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
            resolution=size or "1080x1920",
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

    Routing rules:
      - `voice='eleven:<voice_id>'` → ElevenLabs (escape hatch for brand-locked
        voices; provides native per-word timestamps).
      - alias starts with `gemini` → Google Gemini Flash TTS direct API
        (primary path; PCM audio with evenly-distributed word timestamps).
      - any other alias → OpenRouter `_tts_real` (currently unsupported until
        OpenRouter ships a TTS model with alignment).

    The `voice` arg for Gemini is a prebuilt voice name (e.g. 'Kore', 'Puck').
    """
    if voice.startswith("eleven:"):
        return _tts_elevenlabs(text, voice_id=voice.split(":", 1)[1], out_dir=out_dir)

    out = out_dir or output_dir()
    if is_test_mode():
        spec = resolve(alias, kind="tts")
        runlog.event("openrouter.tts.test", alias=alias, chars=len(text), voice=voice)
        return render_mock_tts(text=text, voice=voice, out_dir=out)

    if alias.startswith("gemini"):
        from . import gemini_tts as _gtts
        gemini_voice = voice if voice and voice != "default" else _gtts.DEFAULT_VOICE
        # Default style applies only when neither style nor style_hint was
        # explicitly passed — passing style=None means "natural" (no prefix).
        effective_style = style if (style or style_hint) else _gtts.DEFAULT_STYLE
        runlog.event(
            "gemini.tts.call", alias=alias, voice=gemini_voice, chars=len(text),
            style=effective_style, style_hint=style_hint,
        )
        t0 = time.monotonic()
        path, words, duration = _gtts.synthesize(
            text, voice=gemini_voice, out_dir=out,
            style=effective_style, style_hint=style_hint,
        )
        duration_ms = int((time.monotonic() - t0) * 1000)
        runlog.event(
            "gemini.tts.response",
            alias=alias, voice=gemini_voice, duration_ms=duration_ms, ok=True,
            audio_seconds=round(duration, 3),
        )
        spec = resolve(alias, kind="tts")
        # Cost recording — Gemini Flash Preview TTS is free during preview;
        # set 0.0 here, revisit when GA pricing lands.
        _record_usage(spec, text, str(path), duration_ms=duration_ms, cost_usd=0.0, test=False)
        return path, words, duration

    return _with_fallback(
        kind="tts",
        alias=alias,
        primary_call=lambda spec: _tts_real(text, spec, voice, voice_description, out),
    )


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


_OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
_OPENROUTER_CREDITS_ENDPOINT = "https://openrouter.ai/api/v1/credits"
_OPENROUTER_HEADERS_EXTRA = {
    "HTTP-Referer": "https://github.com/ianjamesburke/parallax-v0",
    "X-Title": "parallax",
}


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
    import httpx
    key = _check_key()
    resp = httpx.get(
        _OPENROUTER_CREDITS_ENDPOINT,
        headers={"Authorization": f"Bearer {key}", **_OPENROUTER_HEADERS_EXTRA},
        timeout=15.0,
    )
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
    "9:16": (
        "Vertical 9:16 portrait orientation, taller than wide, "
        "subject centered for vertical mobile framing. "
    ),
    "16:9": (
        "Horizontal 16:9 landscape orientation, wider than tall, "
        "subject framed for cinematic landscape composition. "
    ),
    "1:1": "Square 1:1 composition, centered subject. ",
    "4:5": (
        "Vertical 4:5 portrait orientation, taller than wide, "
        "subject centered for portrait framing. "
    ),
    "3:4": (
        "Vertical 3:4 portrait orientation, taller than wide, "
        "subject centered for portrait framing. "
    ),
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
    import httpx

    try:
        key = _check_key()
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
        resp = httpx.post(
            _OPENROUTER_ENDPOINT,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                **_OPENROUTER_HEADERS_EXTRA,
            },
            json=body,
            timeout=60.0,
        )
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
    *, size: str | None = None,
) -> Path:
    """Generate an image via OpenRouter's chat/completions endpoint.

    OpenRouter exposes image generation through the OpenAI-compatible chat
    endpoint with `modalities=["image","text"]`. The response message has
    an `images` list whose entries carry `image_url.url` as a base64 data
    URL. Reference images (for image-edit / nano-banana style models) are
    encoded into the user message as `image_url` content parts alongside
    the prompt text.
    """
    import base64
    import time as _time
    import httpx

    key = _check_key()
    out = out_dir or output_dir()
    out.mkdir(parents=True, exist_ok=True)
    model_id = _strip_or_prefix(spec.model_id)

    # Textual aspect cue — belt-and-suspenders alongside the body field below.
    # Gemini image models occasionally ignore the `aspect_ratio` body field for
    # certain prompt types and return landscape. Prepending a strong textual
    # cue dramatically improves compliance without affecting models that
    # already honor the body field (it's just extra context to them).
    aspect_cue = _aspect_cue(str(spec.portrait_args["aspect_ratio"]) if spec.portrait_args and "aspect_ratio" in spec.portrait_args else None)
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
    # prompt. Pre-cropping each ref to 9:16 makes Gemini's "match the
    # reference" instinct align with the requested aspect.
    target_aspect_res: str | None = None
    if spec.portrait_args and spec.portrait_args.get("aspect_ratio") == "9:16":
        target_aspect_res = "720x1280"
    elif spec.portrait_args and spec.portrait_args.get("aspect_ratio") == "16:9":
        target_aspect_res = "1280x720"

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
    # Merge model-specific portrait_args (e.g. {"aspect_ratio": "9:16"} for
    # Gemini image models). Top-level body keys; OpenRouter passes unknown
    # fields through to the upstream provider.
    for k, v in (spec.portrait_args or {}).items():
        body[k] = v
    if size:
        # Passthrough hint. NB: gemini-2.5-flash-image historically ignored
        # `size` and always returned 1024×1024 — `aspect_ratio` (set above
        # via portrait_args) is the param Gemini actually honors.
        body["size"] = size
    resp = httpx.post(
        _OPENROUTER_ENDPOINT,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            **_OPENROUTER_HEADERS_EXTRA,
        },
        json=body,
        timeout=300.0,
    )
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


_VIDEO_SUBMIT_ENDPOINT = "https://openrouter.ai/api/v1/videos"
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

    key = _check_key()
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

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        **_OPENROUTER_HEADERS_EXTRA,
    }
    submit = httpx.post(_VIDEO_SUBMIT_ENDPOINT, headers=headers, json=body, timeout=60.0)
    _raise_for_credits_or_status(submit)
    job = submit.json()
    polling_url = job.get("polling_url")
    if not polling_url:
        raise RuntimeError(f"OpenRouter video submit returned no polling_url: {job!r}")

    deadline = _time.monotonic() + _VIDEO_POLL_TIMEOUT_S
    last_status: dict[str, Any] = job
    while _time.monotonic() < deadline:
        _time.sleep(_VIDEO_POLL_INTERVAL_S)
        poll = httpx.get(polling_url, headers={"Authorization": f"Bearer {key}"}, timeout=30.0)
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
    download = httpx.get(urls[0], headers={"Authorization": f"Bearer {key}"}, timeout=120.0)
    download.raise_for_status()
    out_path = out / f"openrouter_{spec.alias}_{int(_time.time()*1000)}.mp4"
    out_path.write_bytes(download.content)
    return out_path


def _tts_real(
    text: str, spec: ModelSpec, voice: str, voice_description: str | None, out_dir: Path,
) -> tuple[Path, list[dict[str, Any]], float]:
    """OpenRouter's audio-output models (gpt-audio, lyria) are conversational
    or musical, not per-word-aligned TTS. The parallax pipeline needs
    word-level timestamps for caption alignment, which only ElevenLabs
    currently provides.

    Use `voice='eleven:<voice_id>'` for production TTS. The
    `_tts_elevenlabs` path delegates to `parallax/elevenlabs.py:synthesize`.
    """
    _check_key()
    raise NotImplementedError(
        f"OpenRouter TTS aliases ({spec.alias!r}, model_id={spec.model_id!r}) "
        f"do not provide per-word-aligned timestamps. Use voice='eleven:<voice_id>' "
        f"for the parallax-pipeline TTS path, or PARALLAX_TEST_MODE=1 for stubs."
    )


# ---------------------------------------------------------------------------
# ElevenLabs direct (escape hatch for brand-locked voices)
# ---------------------------------------------------------------------------

def _tts_elevenlabs(
    text: str, *, voice_id: str, out_dir: Path | None,
) -> tuple[Path, list[dict[str, Any]], float]:
    out = out_dir or output_dir()
    out.mkdir(parents=True, exist_ok=True)

    if is_test_mode():
        runlog.event("openrouter.tts.test", alias="eleven", voice=voice_id, chars=len(text))
        return render_mock_tts(text=text, voice=f"eleven_{voice_id[:6]}", out_dir=out)

    key = os.environ.get("AI_VIDEO_ELEVENLABS_KEY") or os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise RuntimeError(
            "ElevenLabs requires AI_VIDEO_ELEVENLABS_KEY or ELEVENLABS_API_KEY for "
            "voice='eleven:<id>'."
        )

    from . import elevenlabs as _eleven
    runlog.event("openrouter.tts.call", alias="eleven", voice=voice_id, chars=len(text))
    t0 = time.monotonic()
    audio_path, words, duration = _eleven.synthesize(
        text=text, voice_id=voice_id, out_dir=out, api_key=key,
    )
    duration_ms = int((time.monotonic() - t0) * 1000)
    runlog.event(
        "openrouter.tts.response",
        alias="eleven", voice=voice_id, duration_ms=duration_ms, ok=True,
    )
    _usage.record(
        session_id=current_session_id.get(),
        backend="elevenlabs",
        alias="eleven_multilingual_v2",
        fal_id="",
        tier="standard",
        prompt=text[:120],
        output_path=str(audio_path),
        duration_ms=duration_ms,
        cost_usd=_eleven.cost_for(text),
        test_mode=False,
    )
    return audio_path, words, duration


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
