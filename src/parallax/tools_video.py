"""Compat shim — the old monolith has been split into focused modules.

This file used to contain ~2000 lines covering captions, voiceover,
animation, assembly, headline, avatar, manifest, project scanning, and
ffmpeg helpers. Each domain now lives in its own module:

  - parallax.captions        (subpackage: styles, chunker, animation,
                              drawtext, pillow, burn)
  - parallax.assembly        (align_scenes, ken_burns_assemble,
                              assemble_clip_video, _zoom_filter,
                              _make_kb_clip, _make_clip_segment)
  - parallax.avatar          (generate_avatar_clips, key_avatar_track,
                              burn_avatar)
  - parallax.headline        (burn_titles, burn_headline)
  - parallax.voiceover       (generate_voiceover, _apply_atempo,
                              _trim_long_pauses, _mock_voiceover)
  - parallax.project         (scan_project_folder, animate_scenes)
  - parallax.manifest        (write_manifest, read_manifest)
  - parallax.ffmpeg_utils    (_get_ffmpeg, _ffmpeg_has_drawtext,
                              _probe_fps, _parse_color, _FFMPEG_FULL)

Every public name still imports from `parallax.tools_video` because
external callers may still depend on it; new code should import from the
extracted module directly.
"""

from __future__ import annotations

from .assembly import (  # noqa: F401
    _make_clip_segment,
    _make_kb_clip,
    _zoom_filter,
    align_scenes,
    assemble_clip_video,
    ken_burns_assemble,
)
from .avatar import burn_avatar, generate_avatar_clips, key_avatar_track  # noqa: F401
from .captions import (  # noqa: F401
    CAPTION_STYLES,
    _FONTS_DIR,
    _burn_captions_drawtext,
    _burn_captions_pillow,
    _expand_animation_keyframes,
    _smart_chunk_words,
    _style_drawtext_filter,
    burn_captions,
    resolve_caption_style,
)
from .ffmpeg_utils import (  # noqa: F401
    _FFMPEG_FULL,
    _ffmpeg_has_drawtext,
    _get_ffmpeg,
    _parse_color,
    _probe_fps,
)
from .headline import burn_headline, burn_titles  # noqa: F401
from .manifest import read_manifest, write_manifest  # noqa: F401
from .project import (  # noqa: F401
    _GROK_I2V_INPUT_FEE,
    _GROK_I2V_RATE_480P,
    _GROK_I2V_RATE_720P,
    IMAGE_EXTS,
    VIDEO_EXTS,
    animate_scenes,
    scan_project_folder,
)
from .voiceover import (  # noqa: F401
    _apply_atempo,
    _mock_voiceover,
    _trim_long_pauses,
    generate_voiceover,
)
