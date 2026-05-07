"""ModelSpec — the per-model record used by the dispatcher and the agent.

The catalog files (`image.yaml`, `video.yaml`, `tts.yaml`) are loaded into
`ModelSpec` instances by `_loader.py`. Field names mirror the original
`pricing.ModelSpec` so existing call sites keep working; new capability
fields are additive.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Kind = Literal["image", "video", "tts"]
Tier = Literal["draft", "mid", "premium", "default"]
RefKind = Literal["style_ref", "product_ref", "character_ref"]
TtsBackend = Literal["chat_audio", "speech"]


@dataclass(frozen=True)
class ModelSpec:
    alias: str
    kind: Kind
    model_id: str
    cost_usd: float
    cost_unit: Literal["image", "second", "1k_chars", "megapixel"]
    description: str
    fallback_alias: str | None = None
    tier: Tier = "default"
    # Reference-image support (image kind).
    max_refs: int = 0
    # Provider-specific kwargs passed verbatim by openrouter.py — kept for
    # backwards compat with the old `portrait_args` mechanism. New code
    # should prefer the explicit `aspect_ratios` capability instead.
    portrait_args: dict[str, object] = field(default_factory=dict)
    # Capabilities — additive in V0.3. Defaults assume vertical-only generation
    # to preserve existing behavior; YAML overrides explicitly.
    aspect_ratios: tuple[str, ...] = ("9:16",)
    start_frame: bool = False
    end_frame: bool = False
    # Accepted reference-input types for the agent. Empty means "no refs".
    inputs: tuple[RefKind, ...] = ()
    # TTS voice list. Empty for non-TTS or models with a single fixed voice.
    voices: tuple[str, ...] = ()
    # TTS request format: "chat_audio" = OpenAI chat-completions audio modality
    # (gpt-audio-mini); "speech" = /api/v1/audio/speech endpoint (Gemini TTS).
    # Defaults to "chat_audio" so existing entries are unchanged.
    tts_backend: TtsBackend = "chat_audio"
    # Native generation resolution when it differs from the output resolution.
    # Only set for models that generate at a fixed lower resolution and get
    # upscaled during ffmpeg assembly (e.g. "480p" for Seedance).
    native_resolution: str | None = None

    @property
    def supports_reference(self) -> bool:
        return self.kind == "image" and self.max_refs > 0

    @property
    def fal_id(self) -> str:
        """Back-compat shim for any callsite still reading `.fal_id`."""
        return self.model_id

    @property
    def price_usd_per_image(self) -> float:
        return self.cost_usd

    def supports_aspect(self, ratio: str) -> bool:
        return "any" in self.aspect_ratios or ratio in self.aspect_ratios
