from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from . import fal, usage
from .context import current_backend, current_session_id
from .log import get_logger
from .pricing import ALIASES, ModelSpec, resolve
from .shim import is_test_mode, render_mock_image
from . import tools_video

log = get_logger("tools")

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "generate_image",
        "description": (
            "Generate an image from a text prompt via FAL. "
            "Returns the filesystem path to the generated image. "
            "Pass `model` as exactly one of: draft, mid, premium, nano-banana, grok. "
            "Never pass raw FAL model IDs. "
            "Pass `reference_images` as a list of local filesystem paths to condition on "
            "(only models marked as supporting reference_images accept this)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Description of the image to generate, or the edit instruction when reference_images are provided.",
                },
                "model": {
                    "type": "string",
                    "enum": list(ALIASES),
                    "description": (
                        "Agent-facing model alias. 'mid' is the default if the user did not "
                        "specify a tier. Must support reference_images when those are provided."
                    ),
                },
                "reference_images": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional list of local filesystem paths to use as reference / input images. "
                        "Only pass when the user has supplied or implied input images to edit, remix, or condition on."
                    ),
                },
            },
            "required": ["prompt", "model"],
        },
    },
    {
        "name": "scan_project_folder",
        "description": (
            "Scan a project folder for a script and either numbered clips or a character image. "
            "Creates a versioned output directory at {folder}/.parallax/output/v{N}/. "
            "Returns JSON with: mode, version, output_dir, script_path, script_text, "
            "character_image_path, clips (dict of number→path in video_clips mode), and folder. "
            "Call this first when given a folder path. Use output_dir for ALL subsequent tool outputs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "folder_path": {"type": "string", "description": "Absolute path to the project folder."},
            },
            "required": ["folder_path"],
        },
    },
    {
        "name": "generate_voiceover",
        "description": (
            "Generate a voiceover from text using ElevenLabs at a given speed. "
            "Returns JSON with audio_path, words_path, words (list of {word, start, end}), and total_duration_s. "
            "Default voice is 'george'. Default speed is 1.1. Available voices: george, rachel, domi, bella, daniel, arnold."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Script text to speak."},
                "voice": {"type": "string", "description": "Voice name. Default: george."},
                "speed": {"type": "number", "description": "Playback speed multiplier. Default: 1.1."},
                "out_dir": {"type": "string", "description": "Directory to save audio files. Default: output/."},
            },
            "required": ["text"],
        },
    },
    {
        "name": "align_scenes",
        "description": (
            "Assign start_s, end_s, duration_s to each scene based on its vo_text word count "
            "and the voiceover word timestamps. Call after generate_voiceover. "
            "Returns updated scenes JSON list."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "scenes_json": {
                    "type": "string",
                    "description": "JSON array of scene objects, each with vo_text field.",
                },
                "words_json": {
                    "type": "string",
                    "description": "JSON string of word timestamps from generate_voiceover (words array or path to vo_words.json).",
                },
            },
            "required": ["scenes_json", "words_json"],
        },
    },
    {
        "name": "assemble_clip_video",
        "description": (
            "Assemble a video from pre-existing numbered clips + aligned scene durations and voiceover. "
            "Use this instead of ken_burns_assemble when scan_project_folder returns mode='video_clips'. "
            "Each scene in scenes_json must have clip_paths (list of file paths) and duration_s. "
            "Clips are looped or trimmed to fill each scene's target duration. "
            "Returns the assembled video path."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "scenes_json": {
                    "type": "string",
                    "description": "JSON array of scene objects, each with clip_paths (list of paths) and duration_s.",
                },
                "audio_path": {"type": "string", "description": "Path to voiceover audio file."},
                "output_path": {"type": "string", "description": "Output video path. Default: output/clip_assembly.mp4."},
                "resolution": {"type": "string", "description": "WxH resolution. Default: auto-detect from clips."},
            },
            "required": ["scenes_json", "audio_path"],
        },
    },
    {
        "name": "ken_burns_assemble",
        "description": (
            "Assemble a Ken Burns draft video from stills + aligned scene durations and a voiceover. "
            "Each scene gets a smooth zoom/pan motion. Returns the output video path. "
            "Use resolution '1080x1920' for vertical (default) or '1920x1080' for landscape."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "scenes_json": {
                    "type": "string",
                    "description": "JSON array of scene objects with still_path and duration_s.",
                },
                "audio_path": {"type": "string", "description": "Path to voiceover audio file."},
                "output_path": {"type": "string", "description": "Output video path. Default: output/ken_burns_draft.mp4."},
                "resolution": {"type": "string", "description": "WxH resolution. Default: 1080x1920."},
            },
            "required": ["scenes_json", "audio_path"],
        },
    },
    {
        "name": "burn_captions",
        "description": (
            "Burn word-by-word captions onto a video using word timestamps. "
            "Default is one word at a time (words_per_chunk=1). "
            "caption_style controls the visual style — pick from: bangers (Kill Tony, default), "
            "impact (classic meme), bebas (yellow TikTok), anton (bold podcast), clean (dark pill box). "
            "Returns the captioned video path."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "video_path": {"type": "string", "description": "Input video path."},
                "words_json": {
                    "type": "string",
                    "description": "Path to vo_words.json, or JSON string of [{word, start, end}].",
                },
                "output_path": {"type": "string", "description": "Output path. Default: input_captioned.mp4."},
                "words_per_chunk": {"type": "integer", "description": "Words per caption chunk. Default: 1 (one word at a time)."},
                "fontsize": {"type": "integer", "description": "Caption font size. Default: 55."},
                "caption_style": {
                    "type": "string",
                    "enum": ["bangers", "impact", "bebas", "anton", "clean"],
                    "description": "Visual style preset. Default: bangers.",
                },
            },
            "required": ["video_path", "words_json"],
        },
    },
    {
        "name": "burn_headline",
        "description": (
            "Overlay a persistent headline title with a solid block background (Instagram/TikTok style). "
            "The text sits on a solid-color box — no stroke/outline. Always visible across the full video. "
            "bg_color/text_color accept ffmpeg color strings. y_position is an ffmpeg height expression."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "video_path": {"type": "string", "description": "Input video path."},
                "text": {"type": "string", "description": "Headline text to display."},
                "output_path": {"type": "string", "description": "Output path. Default: input_headline.mp4."},
                "fontsize": {"type": "integer", "description": "Font size. Default: 64."},
                "bg_color": {"type": "string", "description": "Background box color. Default: white."},
                "text_color": {"type": "string", "description": "Text color. Default: black."},
                "font_name": {
                    "type": "string",
                    "enum": ["bangers", "impact", "bebas", "anton", "clean"],
                    "description": "Font to use. Default: bangers.",
                },
                "y_position": {"type": "string", "description": "ffmpeg expression for top of text block. Default: h*12/100 (12% from top)."},
                "end_time_s": {"type": "number", "description": "If set, headline disappears after this timestamp. Pass the first scene's end_s to show headline only during the intro."},
            },
            "required": ["video_path", "text"],
        },
    },
    {
        "name": "write_manifest",
        "description": "Write a manifest dict (JSON string) to a YAML file. Returns the file path.",
        "input_schema": {
            "type": "object",
            "properties": {
                "manifest_json": {"type": "string", "description": "JSON string of the manifest dict."},
                "manifest_path": {"type": "string", "description": "Output path for the YAML file."},
            },
            "required": ["manifest_json", "manifest_path"],
        },
    },
    {
        "name": "read_manifest",
        "description": "Read a manifest YAML file and return its contents as a JSON string.",
        "input_schema": {
            "type": "object",
            "properties": {
                "manifest_path": {"type": "string", "description": "Path to manifest YAML file."},
            },
            "required": ["manifest_path"],
        },
    },
]


def dispatch_tool(name: str, args: dict[str, Any]) -> str:
    log.info("tool call: %s(%s)", name, _summarize_args(args))
    log.debug("tool call args full: %s", args)
    try:
        if name == "generate_image":
            result = generate_image(**args)
        elif name == "scan_project_folder":
            result = tools_video.scan_project_folder(**args)
        elif name == "assemble_clip_video":
            result = tools_video.assemble_clip_video(**args)
        elif name == "generate_voiceover":
            result = tools_video.generate_voiceover(**args)
        elif name == "align_scenes":
            result = tools_video.align_scenes(**args)
        elif name == "ken_burns_assemble":
            result = tools_video.ken_burns_assemble(**args)
        elif name == "burn_captions":
            result = tools_video.burn_captions(**args)
        elif name == "burn_headline":
            result = tools_video.burn_headline(**args)
        elif name == "write_manifest":
            result = tools_video.write_manifest(**args)
        elif name == "read_manifest":
            result = tools_video.read_manifest(**args)
        else:
            raise ValueError(f"Unknown tool: {name!r}")
    except Exception as e:
        log.info("tool result (error): %s: %s", type(e).__name__, e)
        raise
    log.info("tool result: %s", result)
    return result


def generate_image(
    prompt: str,
    model: str,
    reference_images: list[str] | None = None,
) -> str:
    spec = resolve(model)  # fails fast on unknown alias
    refs = _validate_refs(reference_images, spec)
    test_mode = is_test_mode()

    t0 = time.monotonic()
    if test_mode:
        output_path = str(render_mock_image(prompt=prompt, model=spec.alias))
    else:
        output_path = str(fal.generate(prompt=prompt, spec=spec, reference_images=refs))
    duration_ms = int((time.monotonic() - t0) * 1000)
    cost_usd = 0.0 if test_mode else spec.price_usd_per_image

    rec = usage.record(
        session_id=current_session_id.get(),
        backend=current_backend.get(),
        alias=spec.alias,
        fal_id=spec.fal_id,
        tier=spec.tier,
        prompt=prompt,
        output_path=output_path,
        duration_ms=duration_ms,
        cost_usd=cost_usd,
        test_mode=test_mode,
    )
    log.info(
        "usage: alias=%s duration=%dms cost=$%.4f%s",
        rec.alias,
        rec.duration_ms,
        rec.cost_usd,
        " [test]" if test_mode else "",
    )
    return output_path


def _validate_refs(reference_images: list[str] | None, spec: ModelSpec) -> list[Path]:
    """Coerce, validate, and resolve reference paths. Fails fast on the caller's behalf."""
    if not reference_images:
        return []
    if not spec.supports_reference:
        raise ValueError(
            f"Model {spec.alias!r} does not support reference_images. "
            f"Use one of: {', '.join(a for a, s in _ref_capable().items())}."
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


def _ref_capable() -> dict[str, str]:
    from .pricing import MODELS

    return {a: s.description for a, s in MODELS.items() if s.supports_reference}


def _summarize_args(args: dict[str, Any]) -> str:
    parts: list[str] = []
    for k, v in args.items():
        if isinstance(v, str) and len(v) > 60:
            parts.append(f"{k}={v[:57]!r}...")
        else:
            parts.append(f"{k}={v!r}")
    return ", ".join(parts)
