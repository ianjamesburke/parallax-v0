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

    @property
    def price_usd_per_image(self) -> float:
        """Effective per-image cost assuming 1MP output (v0 default)."""
        return self.price_usd


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
        description="Google Gemini 2.5 Flash Image. Use when the user asks for it or for Google-lineage realism.",
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
        lines.append(
            f"- {spec.alias}: {spec.description} (~${spec.price_usd_per_image:.3f}/image)"
        )
    lines.append(
        "Never pass any value outside this list. If the user has not specified a tier, use 'mid'."
    )
    return "\n".join(lines)
