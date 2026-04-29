"""Model alias catalog — the only vocabulary the CLI and agent see.

Three kinds of media generation, one resolver:

  - image: still frames
  - video: short clips
  - tts:   speech audio

Every alias resolves to a `ModelSpec`. Real-mode calls go through
`openrouter.py`. Test-mode calls go through `shim.py` and never touch
the network.

Tier aliases are `draft`, `mid`, `premium` per modality. Named aliases
(`nano-banana`, `seedream`, `kling`, `veo`, ...) exist for power users.

Catalog data lives in adjacent YAML files (`image.yaml`, `video.yaml`,
`tts.yaml`); update those, not this module, when models change.

Resolving an unknown alias raises ValueError.
"""

from __future__ import annotations

from ._loader import load_image, load_tts, load_video
from ._spec import Kind, ModelSpec

# Eager-load on import — catalog is small and frozen at module scope.
IMAGE_MODELS: dict[str, ModelSpec] = load_image()
VIDEO_MODELS: dict[str, ModelSpec] = load_video()
TTS_MODELS: dict[str, ModelSpec] = load_tts()

# Unified table for resolution. Aliases are unique within a kind; if an alias
# is shared across kinds (e.g. `draft`), the per-kind dispatch in `resolve()`
# picks the right one when `kind=` is passed.
MODELS: dict[str, ModelSpec] = {}
_PER_KIND: dict[Kind, dict[str, ModelSpec]] = {
    "image": IMAGE_MODELS,
    "video": VIDEO_MODELS,
    "tts": TTS_MODELS,
}
# Build flat MODELS map, with kind-prefixed keys for shared aliases so callers
# without a `kind=` arg can still find them.
for _kind, _table in _PER_KIND.items():
    for _alias, _spec in _table.items():
        if _alias in MODELS:
            # Shared alias across kinds (e.g. "draft" exists in image + video).
            # Keep the first-loaded one as the bare alias; expose others via
            # kind-prefixed key for unambiguous lookup.
            MODELS[f"{_kind}:{_alias}"] = _spec
        else:
            MODELS[_alias] = _spec

ALIASES: tuple[str, ...] = tuple(MODELS.keys())


def resolve(alias: str, kind: Kind | None = None) -> ModelSpec:
    """Look up a model by alias. If `kind` is given, search only that kind's
    table — this disambiguates shared aliases (`draft` exists in both image
    and video). Without `kind`, falls back to the flat table and validates
    the kind matches if found."""
    if kind is not None:
        table = _PER_KIND[kind]
        try:
            return table[alias]
        except KeyError:
            # If the alias exists in another kind's table, surface the mismatch
            # explicitly — much more useful than "unknown alias".
            for other_kind, other_table in _PER_KIND.items():
                if alias in other_table:
                    raise ValueError(
                        f"Alias {alias!r} is a {other_kind} model; "
                        f"caller expected {kind}."
                    ) from None
            raise ValueError(
                f"Unknown {kind} model alias: {alias!r}. Must be one of: "
                f"{', '.join(sorted(table.keys()))}."
            ) from None
    try:
        spec = MODELS[alias]
    except KeyError:
        raise ValueError(
            f"Unknown model alias: {alias!r}. Must be one of: {', '.join(ALIASES)}."
        ) from None
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
        "\nNever pass any value outside this list. If no tier is specified, use "
        "'mid' (image/video) or 'tts-mini' (tts). For TTS, pass the voice "
        "name from the catalog (e.g. voice='Kore')."
    )
    return "\n".join(lines)


__all__ = [
    "Kind",
    "ModelSpec",
    "IMAGE_MODELS",
    "VIDEO_MODELS",
    "TTS_MODELS",
    "MODELS",
    "ALIASES",
    "resolve",
    "resolve_chain",
    "alias_guidance",
]
