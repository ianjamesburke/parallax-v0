"""Caption style resolution + animation expansion tests.

Locks in:
  - resolve_caption_style accepts a preset name, an inline dict with
    `base: <preset>` for partial overrides, or a fully custom dict.
  - Each preset carries its own `animation` profile (none or pop).
  - _expand_animation_keyframes emits the right number of stepped
    drawtext entries for each animation type.
  - Chunks shorter than animation duration skip the animation cleanly.

This is the contract an agent relies on when synthesizing custom
caption styles inline — `caption_style: <dict>` in plan.yaml.
"""

from __future__ import annotations

from parallax.tools_video import (
    CAPTION_STYLES,
    _expand_animation_keyframes,
    resolve_caption_style,
)


def _chunk(text: str, start: float, end: float) -> dict:
    return {"text": text, "start": start, "end": end}


# ─── resolve_caption_style ───────────────────────────────────────────────


def test_resolve_by_preset_name_returns_preset():
    style = resolve_caption_style("bangers")
    assert style["fontfile"] == "Bangers-Regular.ttf"
    assert style["animation"]["type"] == "pop"


def test_resolve_unknown_preset_falls_back_to_bangers():
    style = resolve_caption_style("does-not-exist")
    assert style["fontfile"] == "Bangers-Regular.ttf"


def test_resolve_none_returns_bangers():
    style = resolve_caption_style(None)
    assert style["fontfile"] == "Bangers-Regular.ttf"


def test_resolve_inline_dict_with_base_merges_over_preset():
    """Agent can synthesize a one-off style: take bangers, swap the color."""
    style = resolve_caption_style({"base": "bangers", "fontcolor": "yellow"})
    assert style["fontcolor"] == "yellow"
    assert style["fontfile"] == "Bangers-Regular.ttf"  # inherited
    assert style["animation"]["type"] == "pop"  # inherited


def test_resolve_inline_dict_can_override_animation():
    style = resolve_caption_style({"base": "bangers", "animation": {"type": "none"}})
    assert style["animation"]["type"] == "none"
    assert style["fontfile"] == "Bangers-Regular.ttf"


def test_resolve_fully_custom_dict_no_base():
    style = resolve_caption_style({
        "fontfile": "Custom.ttf", "fontcolor": "red",
        "animation": {"type": "pop", "duration_s": 0.04, "scale_keys": [1.2, 1.0]},
    })
    assert style["fontfile"] == "Custom.ttf"
    assert style["fontcolor"] == "red"
    assert style["animation"]["scale_keys"] == [1.2, 1.0]


# ─── _expand_animation_keyframes ─────────────────────────────────────────


def test_expand_none_animation_passes_through():
    chunks = [_chunk("hi", 0.0, 0.5), _chunk("there", 0.5, 1.0)]
    out = _expand_animation_keyframes(chunks, fontsize=64, animation={"type": "none"})
    assert len(out) == 2
    assert all(c["fontsize"] == 64 for c in out)


def test_expand_pop_emits_one_drawtext_per_scale_key():
    """2-key pop becomes 2 drawtexts per chunk; final key extends to chunk end."""
    chunks = [_chunk("WORD", 1.00, 2.00)]
    anim = {"type": "pop", "duration_s": 0.06, "scale_keys": [1.10, 1.0]}
    out = _expand_animation_keyframes(chunks, fontsize=100, animation=anim)
    assert len(out) == 2
    assert out[0]["fontsize"] == 110  # 100 * 1.10
    assert out[1]["fontsize"] == 100  # 100 * 1.0
    assert out[0]["start"] == 1.0 and out[0]["end"] == 1.03
    assert out[1]["start"] == 1.03 and out[1]["end"] == 2.0


def test_expand_pop_three_keys():
    chunks = [_chunk("WORD", 0.0, 1.0)]
    anim = {"type": "pop", "duration_s": 0.09, "scale_keys": [0.85, 1.10, 1.0]}
    out = _expand_animation_keyframes(chunks, fontsize=100, animation=anim)
    assert len(out) == 3
    assert [c["fontsize"] for c in out] == [85, 110, 100]


def test_expand_pop_short_chunk_skips_animation():
    """Chunk shorter than animation duration renders at base size, no pop."""
    chunks = [_chunk("a", 0.0, 0.04)]  # 40 ms chunk, animation is 60 ms
    anim = {"type": "pop", "duration_s": 0.06, "scale_keys": [1.10, 1.0]}
    out = _expand_animation_keyframes(chunks, fontsize=64, animation=anim)
    assert len(out) == 1
    assert out[0]["fontsize"] == 64


def test_expand_unknown_animation_type_falls_back_to_static():
    chunks = [_chunk("hi", 0.0, 0.5)]
    out = _expand_animation_keyframes(chunks, fontsize=64, animation={"type": "wobble"})
    assert len(out) == 1
    assert out[0]["fontsize"] == 64


# ─── Preset registry sanity ──────────────────────────────────────────────


def test_every_preset_has_animation_field():
    """Adding a preset without an animation profile would fall through to
    the unknown-type warning at runtime — fail loudly in tests instead."""
    for name, style in CAPTION_STYLES.items():
        assert "animation" in style, f"preset {name!r} missing animation"
        anim_type = style["animation"].get("type")
        assert anim_type in ("none", "pop"), f"preset {name!r} has unsupported animation type {anim_type!r}"
