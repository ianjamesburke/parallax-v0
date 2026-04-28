"""Project folder scanning and per-scene image-to-video animation.

`scan_project_folder` is the produce-pipeline entry point: it inspects a
project folder for a script + character image (Ken Burns mode) or a set
of numbered clips (video_clips mode) and returns a JSON descriptor plus a
freshly-versioned `parallax/output/vN/` directory.

`animate_scenes` runs image-to-video generation for any scene flagged
`animate: true`, uploading the locked still and Grok motion prompt and
writing the resulting clip into the run's output directory.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from . import usage as _usage
from .context import current_session_id
from .ffmpeg_utils import _get_ffmpeg
from .log import get_logger
from .shim import is_test_mode

log = get_logger("tools_video")

# Per-second rates from fal.ai/models/xai/grok-imagine-video/image-to-video (verified 2026-04-23).
# Cost = duration_s * rate + $0.002 image input fee. Update when FAL pricing changes.
_GROK_I2V_RATE_480P = 0.05   # $/second @ 480p
_GROK_I2V_RATE_720P = 0.07   # $/second @ 720p
_GROK_I2V_INPUT_FEE = 0.002  # flat image-upload fee per clip

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".m4v"}

_CLIP_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".png", ".jpg", ".jpeg", ".webp"}


def scan_project_folder(folder_path: str) -> str:
    """Scan a project folder for a script and either numbered clips or a character image.

    Returns JSON with:
      - mode: "video_clips" (numbered clip files found) or "ken_burns" (still images / no clips)
      - script_path, script_text: the script file
      - clips: {str(number): path} — only present in video_clips mode
      - character_image_path: only relevant in ken_burns mode
      - folder: resolved folder path
    """
    folder = Path(folder_path).expanduser().resolve()
    if not folder.is_dir():
        raise ValueError(f"Not a directory: {folder}")

    # Find script: prefer script.txt / script.md; fall back to any lone .txt
    script_path: Path | None = None
    for name in ("script.txt", "script.md", "brief.txt", "brief.md"):
        candidate = folder / name
        if candidate.exists():
            script_path = candidate
            break
    if script_path is None:
        txts = [f for f in folder.iterdir() if f.suffix in (".txt", ".md") and f.is_file()]
        if len(txts) == 1:
            script_path = txts[0]
        elif len(txts) > 1:
            raise ValueError(
                f"Multiple text files found in {folder}; name one 'script.txt' to disambiguate: "
                + ", ".join(f.name for f in txts)
            )

    # Detect numbered clips (e.g. 001.mp4, 002.mov, 011.png)
    numbered_clips: dict[int, str] = {}
    for f in sorted(folder.iterdir()):
        if re.match(r"^\d+$", f.stem) and f.suffix.lower() in _CLIP_EXTS and f.is_file():
            numbered_clips[int(f.stem)] = str(f)

    mode = "video_clips" if len(numbered_clips) >= 3 else "ken_burns"

    # Find character image (only meaningful in ken_burns mode; exclude numbered clips)
    numbered_paths = set(numbered_clips.values())
    char_path: Path | None = None
    for name in ("character.jpg", "character.jpeg", "character.png", "character.webp"):
        candidate = folder / name
        if candidate.exists():
            char_path = candidate
            break
    if char_path is None:
        imgs = [
            f for f in folder.iterdir()
            if f.suffix.lower() in IMAGE_EXTS and f.is_file() and str(f) not in numbered_paths
        ]
        if len(imgs) == 1:
            char_path = imgs[0]
        elif len(imgs) > 1:
            char_path = sorted(imgs)[0]
            log.info("Multiple images found; using %s as character reference", char_path.name)

    # Create versioned output directory: {folder}/parallax/output/v1/, v2/, ...
    parallax_dir = folder / "parallax"
    output_base = parallax_dir / "output"
    output_base.mkdir(parents=True, exist_ok=True)
    existing_versions = []
    for d in output_base.iterdir():
        if d.is_dir() and d.name.startswith("v"):
            try:
                existing_versions.append(int(d.name[1:]))
            except ValueError:
                pass
    version = max(existing_versions, default=0) + 1
    versioned_output = output_base / f"v{version}"
    versioned_output.mkdir(parents=True, exist_ok=True)

    result: dict[str, Any] = {
        "folder": str(folder),
        "mode": mode,
        "version": version,
        "output_dir": str(versioned_output),
        "script_path": str(script_path) if script_path else None,
        "script_text": script_path.read_text().strip() if script_path else None,
        "character_image_path": str(char_path) if char_path else None,
        "clips": {str(num): path for num, path in sorted(numbered_clips.items())} if mode == "video_clips" else {},
        "test_mode": is_test_mode(),
    }
    log.info("scan_project_folder: mode=%s script=%s clips=%d version=v%d", mode, script_path, len(numbered_clips), version)
    return json.dumps(result)


def animate_scenes(
    scenes_json: str,
    out_dir: str,
    video_model: str = "xai/grok-imagine-video/image-to-video",
    resolution: str = "480p",
) -> str:
    """Generate video clips for scenes marked with animate=true using an image-to-video model.

    Uploads each scene's still to FAL, calls the model with the scene's motion_prompt
    (or a generic cinematic motion prompt), downloads the result, and returns updated
    scenes_json with clip_path set on each animated scene.

    Scenes without animate=true are returned unchanged (clip_path stays unset).
    """
    scenes: list[dict] = json.loads(scenes_json)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    if is_test_mode():
        from .shim import render_mock_video
        for scene in scenes:
            if not scene.get("animate"):
                continue
            existing = scene.get("clip_path")
            if existing and Path(existing).exists():
                continue
            idx = scene["index"]
            motion_prompt = scene.get("motion_prompt") or scene.get("prompt") or "stub motion"
            duration = float(scene.get("duration_s") or 5.0)
            stub = render_mock_video(
                prompt=motion_prompt,
                model=video_model.split("/")[-1] or "video",
                duration_s=duration,
                out_dir=out,
            )
            scene["clip_path"] = str(stub)
            log.info("animate_scenes [test]: scene %d → %s", idx, stub)
            _usage.record(
                session_id=current_session_id.get(),
                backend="shim",
                alias="stub-i2v",
                fal_id=video_model,
                tier="latest",
                prompt=motion_prompt,
                output_path=str(stub),
                duration_ms=0,
                cost_usd=0.0,
                test_mode=True,
            )
        return json.dumps(scenes)

    import urllib.request
    import fal_client

    for scene in scenes:
        if not scene.get("animate"):
            continue
        # Skip scenes whose clip is already locked
        existing_clip = scene.get("clip_path")
        if existing_clip and Path(existing_clip).exists():
            log.info("animate_scenes: scene %d clip already locked, skipping", scene.get("index"))
            continue
        idx = scene["index"]
        still = scene.get("still_path")
        if not still or not Path(still).exists():
            log.warning("animate_scenes: scene %d has no valid still, skipping", idx)
            continue

        motion_prompt = scene.get("motion_prompt") or (
            "Subtle cinematic motion, gentle camera drift, Pixar 3D animation style. "
            "Keep the scene stable and beautiful."
        )

        log.info("animate_scenes: scene %d — uploading still", idx)
        image_url = fal_client.upload_file(Path(still))

        scene_resolution = scene.get("animate_resolution", resolution)
        log.info("animate_scenes: scene %d — calling %s @ %s", idx, video_model, scene_resolution)
        try:
            result = fal_client.subscribe(video_model, arguments={
                "image_url": image_url,
                "prompt": motion_prompt,
                "aspect_ratio": "9:16",
                "resolution": scene_resolution,
            })
        except Exception as e:
            raise RuntimeError(f"animate_scenes: scene {idx} failed: {e}") from e

        video_url = (result.get("video") or {}).get("url") or result.get("url")
        if not video_url:
            raise RuntimeError(f"animate_scenes: no video URL for scene {idx}: {result}")

        raw_path = str(out / f"scene_{idx:02d}_animated_raw.mp4")
        log.info("animate_scenes: scene %d — downloading clip", idx)
        urllib.request.urlretrieve(video_url, raw_path)

        # Strip generated audio — voiceover is mixed in at assembly
        clip_path = str(out / f"scene_{idx:02d}_animated.mp4")
        subprocess.run(
            [_get_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
             "-i", raw_path, "-an", "-c:v", "copy", clip_path],
            check=True,
        )
        # Probe duration before deleting raw clip — needed for per-second cost
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", raw_path],
            capture_output=True, text=True,
        )
        clip_duration_s = float(probe.stdout.strip()) if probe.stdout.strip() else 6.0

        Path(raw_path).unlink(missing_ok=True)
        scene["clip_path"] = clip_path
        log.info("animate_scenes: scene %d → %s (%.1fs)", idx, clip_path, clip_duration_s)

        rate = _GROK_I2V_RATE_720P if scene_resolution == "720p" else _GROK_I2V_RATE_480P
        cost = round(clip_duration_s * rate + _GROK_I2V_INPUT_FEE, 4)
        _usage.record(
            session_id=current_session_id.get(),
            backend="fal",
            alias="grok-i2v",
            fal_id=video_model,
            tier="latest",
            prompt=motion_prompt,
            output_path=clip_path,
            duration_ms=0,
            cost_usd=cost,
            test_mode=is_test_mode(),
        )

    return json.dumps(scenes)
