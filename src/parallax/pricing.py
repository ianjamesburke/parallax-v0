"""Model alias tables — the only vocabulary the CLI and agent see.

Three kinds of media generation, one resolver:

  - image: still frames (1080×1920 portrait by default)
  - video: short clips (5–8s, image-to-video or text-to-video)
  - tts:   speech audio (with optional natural-language voice description)

Every alias resolves to a `ModelSpec`. Real-mode calls go through
`openrouter.py` (or, for `voice: eleven:<id>`, ElevenLabs direct). Test-mode
calls go through `shim.py` and never touch the network.

Costs are point-in-time estimates for budgeting; the authoritative number
gets written to the per-call usage record at request time. Update the table
when the upstream pricing drifts.

Resolving an unknown alias raises ValueError (per the global config rule:
fail fast, no silent fallback).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Kind = Literal["image", "video", "tts"]


@dataclass(frozen=True)
class ModelSpec:
    alias: str
    kind: Kind
    model_id: str                       # OpenRouter slug (provider/model[/variant])
    cost_usd: float                      # per `cost_unit`
    cost_unit: Literal["image", "second", "1k_chars", "megapixel"]
    description: str
    fallback_alias: str | None = None    # used if primary model errors / quotas out
    tier: Literal["draft", "mid", "premium", "default"] = "default"
    # Reference-image support (image kind only).
    max_refs: int = 0
    # Portrait 9:16 hint for the provider — passed through verbatim by openrouter.py.
    portrait_args: dict[str, object] = field(default_factory=dict)

    @property
    def supports_reference(self) -> bool:
        return self.kind == "image" and self.max_refs > 0

    # Back-compat shim for callsites still reading `.fal_id`. Will be removed
    # once tools.py / tools_video.py are rewired through openrouter.py.
    @property
    def fal_id(self) -> str:
        return self.model_id

    @property
    def price_usd_per_image(self) -> float:
        return self.cost_usd


# ---------------------------------------------------------------------------
# Image aliases — portrait 1080×1920 unless overridden.
# ---------------------------------------------------------------------------

IMAGE_MODELS: dict[str, ModelSpec] = {
    "draft": ModelSpec(
        alias="draft",
        kind="image",
        model_id="openrouter/google/gemini-2.5-flash-image",
        cost_usd=0.005,
        cost_unit="image",
        tier="draft",
        description="Cheapest. For exploration and throwaways.",
        fallback_alias=None,
        max_refs=4,
        portrait_args={"aspect_ratio": "9:16"},
    ),
    "mid": ModelSpec(
        alias="mid",
        kind="image",
        model_id="openrouter/bytedance/seedream-4.5",
        cost_usd=0.025,
        cost_unit="image",
        tier="mid",
        description="Balanced quality and speed. Default when no tier specified.",
        fallback_alias="draft",
        max_refs=4,
        portrait_args={"aspect_ratio": "9:16"},
    ),
    "nano-banana": ModelSpec(
        alias="nano-banana",
        kind="image",
        model_id="openrouter/google/gemini-2.5-flash-image",
        cost_usd=0.039,
        cost_unit="image",
        tier="default",
        description="Google Nano Banana (Gemini 2.5 Flash Image). Multi-ref compositing.",
        fallback_alias="seedream",
        max_refs=8,
        portrait_args={"aspect_ratio": "9:16"},
    ),
    "seedream": ModelSpec(
        alias="seedream",
        kind="image",
        model_id="openrouter/bytedance/seedream-4.5",
        cost_usd=0.025,
        cost_unit="image",
        tier="default",
        description="ByteDance Seedream 4.5. Strong realism, good ref-conditioning.",
        fallback_alias="nano-banana",
        max_refs=4,
        portrait_args={"aspect_ratio": "9:16"},
    ),
    "premium": ModelSpec(
        alias="premium",
        kind="image",
        model_id="openrouter/google/nano-banana-pro",
        cost_usd=0.080,
        cost_unit="image",
        tier="premium",
        description="Nano Banana Pro. Best fidelity for final deliverables.",
        fallback_alias="nano-banana",
        max_refs=8,
        portrait_args={"aspect_ratio": "9:16"},
    ),
    "gemini-3-flash": ModelSpec(
        alias="gemini-3-flash",
        kind="image",
        model_id="openrouter/google/gemini-3.1-flash-image-preview",
        cost_usd=0.039,
        cost_unit="image",
        tier="default",
        description="Google Gemini 3.1 Flash Image (preview). Honors aspect_ratio natively.",
        fallback_alias="nano-banana",
        max_refs=8,
        portrait_args={"aspect_ratio": "9:16"},
    ),
    "gemini-3-pro": ModelSpec(
        alias="gemini-3-pro",
        kind="image",
        model_id="openrouter/google/gemini-3-pro-image-preview",
        cost_usd=0.080,
        cost_unit="image",
        tier="premium",
        description="Google Gemini 3 Pro Image (preview). Best fidelity; honors aspect_ratio.",
        # No fallback. Pro -> Flash silently degraded scene quality
        # (Flash ignores reference image style cues and produces photoreal
        # output for stylized references). Failing loud surfaces the
        # underlying Pro error (often a content filter on muscular-man
        # type prompts) so the operator can rephrase or accept.
        fallback_alias=None,
        max_refs=8,
        portrait_args={"aspect_ratio": "9:16"},
    ),
}


# ---------------------------------------------------------------------------
# Video aliases — image-to-video, ~5–8s clips.
# ---------------------------------------------------------------------------

VIDEO_MODELS: dict[str, ModelSpec] = {
    "kling": ModelSpec(
        alias="kling",
        kind="video",
        model_id="openrouter/kwaivgi/kling-video-o1",
        cost_usd=0.10,
        cost_unit="second",
        tier="mid",
        description="Kling Video O1. Strong motion and prompt adherence.",
        fallback_alias="seedance",
        portrait_args={"aspect_ratio": "9:16"},
    ),
    "veo": ModelSpec(
        alias="veo",
        kind="video",
        model_id="openrouter/google/veo-3.1",
        cost_usd=0.50,
        cost_unit="second",
        tier="premium",
        description="Google Veo 3.1. Premium quality; expensive.",
        fallback_alias="kling",
        portrait_args={"aspect_ratio": "9:16"},
    ),
    "seedance": ModelSpec(
        alias="seedance",
        kind="video",
        model_id="openrouter/bytedance/seedance-2.0-fast",
        cost_usd=0.06,
        cost_unit="second",
        tier="draft",
        description="ByteDance Seedance. Cheap and fast.",
        fallback_alias="wan",
        portrait_args={"aspect_ratio": "9:16"},
    ),
    "wan": ModelSpec(
        alias="wan",
        kind="video",
        model_id="openrouter/alibaba/wan-2.7",
        cost_usd=0.05,
        cost_unit="second",
        tier="draft",
        description="Alibaba Wan 2.7. Open-weights baseline.",
        fallback_alias="seedance",
        portrait_args={"aspect_ratio": "9:16"},
    ),
    "sora": ModelSpec(
        alias="sora",
        kind="video",
        model_id="openrouter/openai/sora-2-pro",
        cost_usd=0.40,
        cost_unit="second",
        tier="premium",
        description="OpenAI Sora 2 Pro. Premium quality; expensive.",
        fallback_alias="kling",
        portrait_args={"aspect_ratio": "9:16"},
    ),
    # Tier aliases — resolve to the canonical model for that quality band.
    "video-draft": ModelSpec(
        alias="video-draft",
        kind="video",
        model_id="openrouter/alibaba/wan-2.7",
        cost_usd=0.05,
        cost_unit="second",
        tier="draft",
        description="Alibaba Wan 2.7. Open-weights baseline.",
        fallback_alias="seedance",
        portrait_args={"aspect_ratio": "9:16"},
    ),
    "video-mid": ModelSpec(
        alias="video-mid",
        kind="video",
        model_id="openrouter/kwaivgi/kling-video-o1",
        cost_usd=0.10,
        cost_unit="second",
        tier="mid",
        description="Kling Video O1. Strong motion and prompt adherence.",
        fallback_alias="seedance",
        portrait_args={"aspect_ratio": "9:16"},
    ),
    "video-hq": ModelSpec(
        alias="video-hq",
        kind="video",
        model_id="openrouter/google/veo-3.1",
        cost_usd=0.50,
        cost_unit="second",
        tier="premium",
        description="Google Veo 3.1. Premium quality; expensive.",
        fallback_alias="kling",
        portrait_args={"aspect_ratio": "9:16"},
    ),
}


# ---------------------------------------------------------------------------
# TTS aliases.
# Primary: `gemini-flash-tts` — Google Gemini 2.5 Flash Preview TTS, called
# direct (NOT via OpenRouter; OpenRouter does not host Gemini TTS as of
# 2026-04-28). Returns 24kHz PCM mono audio. Word timestamps are evenly
# distributed (Gemini does not emit alignment); use `voice='eleven:<id>'`
# for brand-locked voices that need tighter caption sync.
# ---------------------------------------------------------------------------

TTS_MODELS: dict[str, ModelSpec] = {
    "gemini-flash-tts": ModelSpec(
        alias="gemini-flash-tts",
        kind="tts",
        # Sentinel — `_tts_real` routes by alias prefix, not by HTTP slug.
        # This identifier is recorded in usage logs only.
        model_id="gemini-direct/gemini-2.5-flash-preview-tts",
        cost_usd=0.0,
        cost_unit="1k_chars",
        description="Default TTS. Google Gemini Flash Preview TTS via direct API. Free during preview.",
        fallback_alias=None,
    ),
}


# Unified table for resolution. Aliases are unique across kinds.
MODELS: dict[str, ModelSpec] = {**IMAGE_MODELS, **VIDEO_MODELS, **TTS_MODELS}
ALIASES: tuple[str, ...] = tuple(MODELS.keys())


def resolve(alias: str, kind: Kind | None = None) -> ModelSpec:
    """Look up a model by alias. Optionally enforce the expected kind."""
    try:
        spec = MODELS[alias]
    except KeyError:
        raise ValueError(
            f"Unknown model alias: {alias!r}. Must be one of: {', '.join(ALIASES)}."
        ) from None
    if kind is not None and spec.kind != kind:
        raise ValueError(
            f"Alias {alias!r} is a {spec.kind} model; caller expected {kind}."
        )
    return spec


def resolve_chain(alias: str, kind: Kind | None = None) -> list[ModelSpec]:
    """Resolve `alias` and follow its fallback chain. Stops at the first cycle."""
    seen: set[str] = set()
    chain: list[ModelSpec] = []
    cur: str | None = alias
    while cur and cur not in seen:
        seen.add(cur)
        spec = resolve(cur, kind=kind)
        chain.append(spec)
        cur = spec.fallback_alias
    return chain


def alias_guidance() -> str:
    """Formatted guidance string for the agent's system prompt."""
    lines = ["Available models. Pass exactly one alias as the relevant arg."]
    for label, table in (("Image", IMAGE_MODELS), ("Video", VIDEO_MODELS), ("TTS", TTS_MODELS)):
        lines.append(f"\n{label}:")
        for s in table.values():
            refs = f" — refs up to {s.max_refs}" if s.supports_reference else ""
            fb = f" → fallback: {s.fallback_alias}" if s.fallback_alias else ""
            lines.append(
                f"  - {s.alias}: {s.description} (~${s.cost_usd:.3f}/{s.cost_unit}){refs}{fb}"
            )
    lines.append(
        "\nNever pass any value outside this list. If no tier is specified, use 'mid' (image) "
        "or 'gemini-flash-tts' (tts). For brand-locked voices, pass voice='eleven:<voice_id>'."
    )
    return "\n".join(lines)
