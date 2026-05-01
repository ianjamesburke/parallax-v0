"""Caption rendering subpackage.

Public surface re-exported here so callers can import from `parallax.captions`
directly. Internal helpers (`_smart_chunk_words`, `_expand_animation_keyframes`,
`_style_drawtext_filter`, `_burn_captions_drawtext`) remain importable for
tests pinning current behaviour.
"""

from __future__ import annotations

from .animation import _expand_animation_keyframes
from .burn import burn_captions
from .chunker import _smart_chunk_words
from .drawtext import _burn_captions_drawtext, _style_drawtext_filter
from .styles import CAPTION_STYLES, _FONTS_DIR, resolve_caption_style

__all__ = [
    "CAPTION_STYLES",
    "_FONTS_DIR",
    "_burn_captions_drawtext",
    "_expand_animation_keyframes",
    "_smart_chunk_words",
    "_style_drawtext_filter",
    "burn_captions",
    "resolve_caption_style",
]
