"""Image-model ladder — the only vocabulary the agent sees for `model`.

Prices verified from fal.ai directly on 2026-04-17. When prices drift, update
the table here; the agent-facing aliases stay stable. Resolving an unknown
alias raises ValueError (per config philosophy: fail fast, no silent fallback).

Pricing units:
- `megapixel`: billed per megapixel of output. v0 assumes 1MP output
  (1024x1024), so `price_usd_per_image` == `price_usd`.
- `image`: flat price per image regardless of resolution (within the model's
  supported range).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ModelSpec:
    alias: str
    fal_id: str
    tier: Literal["draft", "mid", "premium", "latest"]
    price_usd: float
    price_unit: Literal["megapixel", "image"]
    description: str
    # Reference-image support (optional). To enable refs for a model, set all four.
    edit_fal_id: str | None = None  # sibling endpoint used when refs are provided
    ref_param_name: str | None = None  # e.g. "image_url" (single) or "image_urls" (list)
    max_refs: int = 0  # max number of reference images accepted; 0 = not supported

    @property
    def price_usd_per_image(self) -> float:
        """Effective per-image cost assuming 1MP output (v0 default)."""
        return self.price_usd

    @property
    def supports_reference(self) -> bool:
        return self.edit_fal_id is not None and self.max_refs > 0

    @property
    def refs_are_list(self) -> bool:
        """Whether the edit endpoint takes refs as a list (True) or a single string (False)."""
        return self.max_refs != 1


MODELS: dict[str, ModelSpec] = {
    "draft": ModelSpec(
        alias="draft",
        fal_id="fal-ai/flux/schnell",
        tier="draft",
        price_usd=0.003,
        price_unit="megapixel",
        description="Cheapest and fastest. Use for exploration, drafts, and throwaways.",
    ),
    "mid": ModelSpec(
        alias="mid",
        fal_id="fal-ai/flux/dev",
        tier="mid",
        price_usd=0.025,
        price_unit="megapixel",
        description="Balanced quality and speed. Default choice when the user has not specified a tier.",
        edit_fal_id="fal-ai/flux/dev/image-to-image",
        ref_param_name="image_url",
        max_refs=1,
    ),
    "premium": ModelSpec(
        alias="premium",
        fal_id="fal-ai/flux-pro/v1.1",
        tier="premium",
        price_usd=0.04,
        price_unit="megapixel",
        description="Highest quality in the Flux lineage. Use for final deliverables.",
    ),
    "nano-banana": ModelSpec(
        alias="nano-banana",
        fal_id="fal-ai/gemini-25-flash-image",
        tier="latest",
        price_usd=0.039,
        price_unit="image",
        description="Google Gemini 2.5 Flash Image. Use when the user asks for it, for Google-lineage realism, or when multiple reference images need to be combined.",
        edit_fal_id="fal-ai/gemini-25-flash-image/edit",
        ref_param_name="image_urls",
        max_refs=8,
    ),
    "grok": ModelSpec(
        alias="grok",
        fal_id="xai/grok-imagine-image",
        tier="latest",
        price_usd=0.02,
        price_unit="image",
        description="xAI Grok Image (Aurora engine). Use when the user asks for it.",
    ),
}

ALIASES: tuple[str, ...] = tuple(MODELS.keys())


def resolve(alias: str) -> ModelSpec:
    """Look up a model by its agent-facing alias. Fail fast on unknowns."""
    try:
        return MODELS[alias]
    except KeyError:
        raise ValueError(
            f"Unknown model alias: {alias!r}. Must be one of: {', '.join(ALIASES)}."
        ) from None


def alias_guidance() -> str:
    """Formatted guidance string for the agent's system prompt."""
    lines = ["Available image models. Pass exactly one of these as the `model` argument:"]
    for spec in MODELS.values():
        if not spec.supports_reference:
            refs = ""
        elif spec.max_refs == 1:
            refs = " — supports reference_images (max 1)"
        else:
            refs = f" — supports reference_images (max {spec.max_refs})"
        lines.append(
            f"- {spec.alias}: {spec.description} (~${spec.price_usd_per_image:.3f}/image){refs}"
        )
    lines.append(
        "Never pass any value outside this list. If the user has not specified a tier, use 'mid'."
    )
    lines.append(
        "If the user provides or implies reference images (inputs to remix, edit, or condition on), "
        "you MUST use a model that supports reference_images. Pass the local file paths in reference_images."
    )
    return "\n".join(lines)
