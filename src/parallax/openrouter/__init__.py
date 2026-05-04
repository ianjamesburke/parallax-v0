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

Submodules
----------
client  — HTTP primitives, auth headers, credits check
retry   — fallback chain and transient-network-error retry
image   — image generation, reference describe, aspect cue
video   — video generation and polling
tts     — TTS synthesis, emotional-tag stripping, style directives
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import runlog
from .. import usage as _usage
from ..context import current_session_id
from ..log import get_logger
from ..models import Kind, ModelSpec, resolve, resolve_chain
from ..shim import (
    is_test_mode,
    output_dir,
    render_mock_image,
    render_mock_tts,
    render_mock_video,
)

# ---------------------------------------------------------------------------
# Re-export submodule symbols so `from parallax.openrouter import X` keeps
# working and monkeypatching `openrouter.X` in tests patches the right binding.
# ---------------------------------------------------------------------------

from .client import (  # noqa: F401
    _BASE,
    _BASE_HEADERS,
    _auth_headers,
    _check_key,
    _get,
    _post,
    _raise_for_credits_or_status,
    _stream_post,
    _strip_or_prefix,
    CreditsBalance,
    InsufficientCreditsError,
    check_credits,
)

from .image import (  # noqa: F401
    _ASPECT_CUE_TEXTS,
    _ASPECT_TO_REF_RES,
    _REFERENCE_DESCRIBE_MODEL,
    _REFERENCE_DESCRIBE_PROMPT,
    _aspect_cue,
    _describe_reference_cache,
    _describe_reference_uncached,
    _image_real,
    _validate_refs,
    analyze_image,
    describe_reference,
)

from .tts import (  # noqa: F401
    _EMOTIONAL_TAG_RE,
    _GEMINI_TTS_DEFAULT_VOICE,
    _TTS_DEFAULT_MODEL,
    _TTS_DEFAULT_STYLE,
    _TTS_DEFAULT_VOICE,
    _TTS_PCM_BYTES_PER_SAMPLE,
    _TTS_PCM_SAMPLE_RATE,
    _TTS_STYLE_PRESETS,
    _tts_evenly_distributed_words,
    _tts_real,
    _tts_real_speech,
    _tts_resolve_directive,
    strip_emotional_tags,
)

from .video import (  # noqa: F401
    _VIDEO_POLL_TIMEOUT_S,
    _video_real,
)

from .retry import (  # noqa: F401
    _is_transient_network_error,
)

log = get_logger("openrouter")

# Poll interval lives here (not in video.py) so monkeypatching
# `openrouter._VIDEO_POLL_INTERVAL_S` in tests affects the value that
# _video_real reads from this module at runtime.
_VIDEO_POLL_INTERVAL_S = 5.0


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
    import time

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
    from ..settings import _ASPECT_TO_RESOLUTION as _ASPECT_RES
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
    from ..settings import _ASPECT_TO_RESOLUTION as _ASPECT_RES
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
