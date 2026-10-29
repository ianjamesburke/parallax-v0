"""Editor handoff package export (Premiere Pro / DaVinci Resolve / Final Cut Pro).

Builds a self-contained, portable directory from a produce run's `manifest.yaml`
containing copied media assets, an FCP7-style xmeml v4 `timeline.xml`, an
optional FCPXML 1.10 `timeline.fcpxml`, `captions/captions.srt`, `README.txt`,
and `handoff.json`.
"""

from __future__ import annotations

import json
import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import yaml

from .assembly import _make_kb_clip, _resolve_auto_trim
from .captions.chunker import _fixed_chunk_words, _smart_chunk_words
from .ffmpeg_utils import (
    parse_resolution,
    pipe_rawvideo_frames,
    probe_audio_duration,
    probe_duration,
    probe_resolution,
    run_ffmpeg,
)
from .log import get_logger
from .shim import is_test_mode

log = get_logger(__name__)

_FORBIDDEN_TOP_DIRS = frozenset({
    "bin", "boot", "dev", "etc", "lib", "lib64", "proc", "root", "run", "sbin", "sys", "usr", "var",
})


def _validate_safe_out_dir(folder_path: Path, out_path: Path) -> None:
    """Refuse unsafe output directories that could clobber the project or system."""
    folder_resolved = folder_path.resolve()
    out_resolved = out_path.resolve()

    if out_resolved == folder_resolved:
        raise ValueError(
            f"Refusing out_dir equal to project folder itself: {out_resolved}"
        )
    if folder_resolved.is_relative_to(out_resolved):
        raise ValueError(
            f"Refusing out_dir outside safe bounds (ancestor of project folder): {out_resolved}"
        )
    if len(out_resolved.parts) <= 2 or out_resolved == Path.home().resolve():
        raise ValueError(
            f"Refusing out_dir outside safe bounds (shallow/home path): {out_resolved}"
        )
    if len(out_resolved.parts) > 1 and out_resolved.parts[1] in _FORBIDDEN_TOP_DIRS:
        raise ValueError(
            f"Refusing out_dir in system directory: {out_resolved}"
        )
    parallax_dir = folder_resolved / "parallax"
    if out_resolved == parallax_dir or out_resolved.is_relative_to(parallax_dir):
        raise ValueError(
            f"Refusing out_dir inside project parallax state directory: {out_resolved}"
        )
    if out_resolved.exists() and not out_resolved.is_dir():
        raise ValueError(
            f"Refusing out_dir that is an existing file: {out_resolved}"
        )
    if out_resolved.exists() and any(out_resolved.iterdir()):
        if not out_resolved.is_relative_to(folder_resolved):
            if not (
                (out_resolved / "handoff.json").is_file()
                or (out_resolved / "timeline.xml").is_file()
            ):
                raise ValueError(
                    f"Refusing to overwrite non-empty directory outside project folder "
                    f"(missing handoff.json / timeline.xml): {out_resolved}"
                )


def _clean_out_dir(out_resolved: Path) -> None:
    """Delete contents inside out_resolved without removing out_resolved itself."""
    out_resolved.mkdir(parents=True, exist_ok=True)
    for child in out_resolved.iterdir():
        if child.is_symlink() or child.is_file():
            child.unlink()
        elif child.is_dir():
            shutil.rmtree(child)


def _resolve_manifest_path(
    folder_path: Path,
    *,
    manifest_path: str | Path | None = None,
    version: int | None = None,
) -> Path:
    """Locate the target manifest.yaml from explicit path, version, or latest vN."""
    if manifest_path is not None:
        raw = Path(manifest_path).expanduser()
        candidates = [raw] if raw.is_absolute() else [Path.cwd() / raw, folder_path / raw]
        for cand in candidates:
            if cand.is_file():
                return cand.resolve()
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")

    output_base = folder_path / "parallax" / "output"
    if version is not None:
        cand = output_base / f"v{version}" / "manifest.yaml"
        if cand.is_file():
            return cand.resolve()
        raise FileNotFoundError(
            f"Manifest for version v{version} not found at {cand}"
        )

    if output_base.is_dir():
        version_dirs: list[tuple[int, Path]] = []
        for d in output_base.iterdir():
            if d.is_dir() and re.fullmatch(r"v\d+", d.name):
                m_file = d / "manifest.yaml"
                if m_file.is_file():
                    version_dirs.append((int(d.name[1:]), m_file))
        if version_dirs:
            version_dirs.sort(key=lambda item: item[0])
            return version_dirs[-1][1].resolve()

    root_manifest = folder_path / "manifest.yaml"
    if root_manifest.is_file():
        return root_manifest.resolve()

    raise FileNotFoundError(
        f"No manifest.yaml found under {output_base} or {root_manifest}"
    )


def _resolve_project_file(
    raw_path: str | Path | None,
    folder_path: Path,
    manifest_dir: Path,
) -> Path | None:
    """Resolve a path from manifest/plan relative to project folder or manifest dir."""
    if raw_path is None:
        return None
    raw_str = str(raw_path).strip()
    if not raw_str:
        return None
    p = Path(raw_str).expanduser()
    if p.is_absolute():
        if p.is_file():
            return p.resolve()
        # Handle relocated project folder with absolute path containing 'parallax/'
        if "parallax" in p.parts:
            idx = p.parts.index("parallax")
            relocated = folder_path / Path(*p.parts[idx:])
            if relocated.is_file():
                return relocated.resolve()
        return p

    for base in (folder_path, manifest_dir):
        cand = base / p
        if cand.is_file():
            return cand.resolve()
    return (folder_path / p).resolve()


def _probe_audio_stream_info(audio_path: Path) -> tuple[int, int]:
    """Return (channels, sample_rate) for an audio file via ffprobe, fallback (1, 44100)."""
    try:
        res = run_ffmpeg(
            [
                "ffprobe", "-v", "error", "-select_streams", "a:0",
                "-show_entries", "stream=channels,sample_rate",
                "-of", "json", str(audio_path),
            ],
            capture_output=True,
            text=True,
        )
        if res.returncode == 0 and res.stdout.strip():
            data = json.loads(res.stdout)
            streams = data.get("streams") or []
            if streams:
                ch = int(streams[0].get("channels") or 1)
                sr = int(streams[0].get("sample_rate") or 44100)
                return max(1, ch), sr if sr > 0 else 44100
    except Exception:
        pass
    return 1, 44100


def _probe_media_duration_s(media_path: Path) -> float:
    """Return media duration in seconds using ffprobe helpers."""
    dur = probe_audio_duration(media_path)
    if dur is None or dur <= 0:
        dur = probe_duration(media_path)
    return float(dur) if (dur is not None and dur > 0) else 0.0


def _render_still_kb_clip(
    still_path: Path,
    target_frames: int,
    fps: int,
    resolution: str,
    output_path: Path,
    *,
    scene_index: int = 0,
    zoom_direction: str | None = None,
    zoom_amount: float | None = None,
) -> None:
    """Render a Ken Burns MP4 of exact target_frames at fps and resolution."""
    duration_s = target_frames / float(fps)
    if fps == 30 and max(1, round(duration_s * 30)) == target_frames:
        _make_kb_clip(
            str(still_path),
            duration_s,
            str(output_path),
            resolution=resolution,
            scene_index=scene_index,
            zoom_direction=zoom_direction,
            zoom_amount=zoom_amount,
        )
        return

    from PIL import Image  # type: ignore[import]

    out_w, out_h = parse_resolution(resolution)
    if is_test_mode():
        img = Image.open(still_path).convert("RGB")
        img = img.resize((out_w, out_h), Image.Resampling.LANCZOS)
        frame_bytes = img.tobytes()
        pipe_rawvideo_frames(
            str(output_path),
            width=out_w,
            height=out_h,
            fps=fps,
            total_frames=target_frames,
            frames=(frame_bytes for _ in range(target_frames)),
            source_label=str(still_path),
        )
        return

    motions = [
        (1.0, 1.15, 0.0, 0.0),
        (1.15, 1.0, 0.0, 0.0),
        (1.0, 1.12, 0.4, 0.0),
        (1.0, 1.12, -0.4, 0.0),
        (1.0, 1.12, 0.0, 0.4),
        (1.0, 1.12, 0.0, -0.4),
    ]
    if zoom_direction:
        end_z = zoom_amount if zoom_amount is not None else 1.25
        dir_map = {
            "up": (0.0, -1.0),
            "down": (0.0, 1.0),
            "left": (-1.0, 0.0),
            "right": (1.0, 0.0),
            "in": (0.0, 0.0),
        }
        pan_x, pan_y = dir_map.get(zoom_direction, (0.0, 0.0))
        start_zoom, end_zoom = 1.0, end_z
    else:
        start_zoom, end_zoom, pan_x, pan_y = motions[scene_index % len(motions)]

    src_w, src_h = round(out_w * 1.5), round(out_h * 1.5)
    img = Image.open(still_path).convert("RGB")
    scale = max(src_w / img.width, src_h / img.height)
    scaled = img.resize(
        (round(img.width * scale), round(img.height * scale)),
        Image.Resampling.LANCZOS,
    )
    x0 = (scaled.width - src_w) // 2
    y0 = (scaled.height - src_h) // 2
    img = scaled.crop((x0, y0, x0 + src_w, y0 + src_h))
    cx, cy = src_w / 2.0, src_h / 2.0

    def _frames():
        for n in range(target_frames):
            t = n / max(target_frames - 1, 1)
            zoom = start_zoom + (end_zoom - start_zoom) * t
            crop_w = src_w / zoom
            crop_h = src_h / zoom
            avail_x = (src_w - crop_w) / 2
            avail_y = (src_h - crop_h) / 2
            left = cx - crop_w / 2 + pan_x * avail_x * t
            top = cy - crop_h / 2 + pan_y * avail_y * t
            frame = img.transform(
                (out_w, out_h),
                Image.Transform.EXTENT,
                (left, top, left + crop_w, top + crop_h),
                Image.Resampling.BICUBIC,
            )
            yield frame.tobytes()

    pipe_rawvideo_frames(
        str(output_path),
        width=out_w,
        height=out_h,
        fps=fps,
        total_frames=target_frames,
        frames=_frames(),
        source_label=str(still_path),
    )


def _format_srt_timestamp(seconds: float) -> str:
    """Format seconds as HH:MM:SS,mmm for SubRip (.srt)."""
    total_ms = max(0, int(round(seconds * 1000)))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1_000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def _build_srt(words: list[dict[str, Any]], words_per_chunk: int | str = "smart") -> str:
    """Build SubRip (.srt) content from word timings using Parallax's chunker."""
    if isinstance(words_per_chunk, str) and words_per_chunk.strip().lower() == "smart":
        chunks = _smart_chunk_words(words, max_letters=4)
    else:
        try:
            max_w = int(words_per_chunk)
        except (TypeError, ValueError):
            max_w = 0
        if max_w >= 1:
            chunks = _fixed_chunk_words(words, max_words=max_w)
        else:
            chunks = _smart_chunk_words(words, max_letters=4)

    blocks: list[str] = []
    prev_end_ms = 0
    idx = 1
    for chunk in chunks:
        text = str(chunk.get("text") or "").strip()
        if not text:
            continue
        start_s = float(chunk.get("start", 0.0))
        end_s = float(chunk.get("end", start_s))
        start_ms = max(prev_end_ms, int(round(start_s * 1000)))
        end_ms = max(start_ms + 50, int(round(end_s * 1000)))
        prev_end_ms = end_ms
        start_tc = _format_srt_timestamp(start_ms / 1000.0)
        end_tc = _format_srt_timestamp(end_ms / 1000.0)
        blocks.append(f"{idx}\n{start_tc} --> {end_tc}\n{text}\n")
        idx += 1

    return "\n".join(blocks)


def _find_words_per_chunk(
    manifest: dict[str, Any],
    folder_path: Path,
    manifest_dir: Path,
) -> int | str:
    """Read words_per_chunk from manifest or plan.yaml if available, else 'smart'."""
    if manifest.get("words_per_chunk") is not None:
        return manifest["words_per_chunk"]
    for plan_cand in (
        manifest_dir / "plan.yaml",
        folder_path / "plan.yaml",
        folder_path / "parallax" / "scratch" / "plan.yaml",
    ):
        if plan_cand.is_file():
            try:
                plan_data = yaml.safe_load(plan_cand.read_text(encoding="utf-8")) or {}
                if isinstance(plan_data, dict) and plan_data.get("words_per_chunk") is not None:
                    return plan_data["words_per_chunk"]
            except Exception:
                pass
    return "smart"


def _resolve_logo_path(
    folder_path: Path,
    manifest_dir: Path,
    *,
    logo: str | Path | None = None,
    brand: str | Path | None = None,
    warnings: list[str],
) -> Path | None:
    """Resolve optional logo path from --logo, --brand, or project brand.yaml / plan.yaml."""
    if logo is not None:
        raw_logo = Path(logo).expanduser()
        candidates = [raw_logo] if raw_logo.is_absolute() else [Path.cwd() / raw_logo, folder_path / raw_logo]
        for cand in candidates:
            if cand.is_file():
                return cand.resolve()
        raise FileNotFoundError(f"Logo file not found: {logo}")

    brand_files: list[Path] = []
    if brand is not None:
        raw_brand = Path(brand).expanduser()
        b_cands = [raw_brand] if raw_brand.is_absolute() else [Path.cwd() / raw_brand, folder_path / raw_brand]
        found_brand: Path | None = None
        for cand in b_cands:
            if cand.is_file():
                found_brand = cand.resolve()
                break
        if found_brand is None:
            raise FileNotFoundError(f"Brand file not found: {brand}")
        brand_files.append(found_brand)
    else:
        for default_name in ("brand.yaml", "brand.yml", "brand.json"):
            cand = folder_path / default_name
            if cand.is_file():
                brand_files.append(cand.resolve())

    for bf in brand_files:
        try:
            data = yaml.safe_load(bf.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            warnings.append(f"Could not parse brand file {bf.name}: {exc}")
            continue
        if isinstance(data, dict) and isinstance(data.get("logo"), str):
            logo_val = data["logo"].strip()
            if logo_val and not logo_val.startswith("data:"):
                lp = Path(logo_val).expanduser()
                search = [lp] if lp.is_absolute() else [bf.parent / lp, folder_path / lp]
                for sc in search:
                    if sc.is_file():
                        return sc.resolve()
                warnings.append(f"Brand logo '{logo_val}' referenced in {bf.name} was not found on disk.")

    for plan_cand in (manifest_dir / "plan.yaml", folder_path / "plan.yaml"):
        if plan_cand.is_file():
            try:
                plan_data = yaml.safe_load(plan_cand.read_text(encoding="utf-8")) or {}
                brand_block = plan_data.get("brand") if isinstance(plan_data, dict) else None
                if isinstance(brand_block, dict) and isinstance(brand_block.get("logo"), str):
                    logo_val = brand_block["logo"].strip()
                    if logo_val and not logo_val.startswith("data:"):
                        lp = Path(logo_val).expanduser()
                        search = [lp] if lp.is_absolute() else [folder_path / lp, plan_cand.parent / lp]
                        for sc in search:
                            if sc.is_file():
                                return sc.resolve()
            except Exception:
                pass

    return None


def _find_reference_video(
    manifest: dict[str, Any],
    folder_path: Path,
    manifest_dir: Path,
) -> Path | None:
    """Locate the finished or draft MP4 from the output dir if one exists."""
    for key in ("final_video", "video_path", "current_video"):
        if manifest.get(key):
            resolved = _resolve_project_file(manifest[key], folder_path, manifest_dir)
            if resolved and resolved.is_file():
                return resolved

    if manifest_dir != folder_path and manifest_dir.is_dir():
        mp4s = sorted(p for p in manifest_dir.glob("*.mp4") if p.is_file())
        if mp4s:
            return mp4s[0].resolve()
        video_subdir = manifest_dir / "video"
        if video_subdir.is_dir():
            sub_mp4s = sorted(p for p in video_subdir.glob("*.mp4") if p.is_file())
            if sub_mp4s:
                return sub_mp4s[0].resolve()
    return None


def _scene_kind(scene: dict[str, Any], has_clip: bool) -> str:
    """Return deterministic scene asset kind ('motion', 'animated', 'clip', or 'still')."""
    if not has_clip:
        return "still"
    clip_str = str(scene.get("clip_path") or "").lower()
    stem = Path(clip_str).stem
    if (
        scene.get("scene_type") == "motion_graphic"
        or scene.get("motion_style")
        or scene.get("html_path")
        or stem.endswith("_motion")
        or "motion" in stem
    ):
        return "motion"
    if (
        scene.get("animate")
        or scene.get("scene_type") == "animation"
        or stem.endswith("_animated")
        or "animated" in stem
    ):
        return "animated"
    return "clip"


def _compute_scene_frame_grid(
    scenes: list[dict[str, Any]],
    vo_frames: int,
    fps: int,
) -> list[tuple[int, int]]:
    """Compute contiguous integer (start_frame, end_frame) slots covering 0..vo_frames."""
    n = len(scenes)
    if n == 0:
        raise ValueError("Manifest contains no scenes")
    if vo_frames < n:
        raise ValueError(
            f"Voiceover duration ({vo_frames} frames) is shorter than scene count ({n})"
        )

    raw_starts: list[int] = [0]
    has_start_end = all(
        s.get("start_s") is not None and s.get("end_s") is not None for s in scenes
    )
    if has_start_end:
        for i in range(1, n):
            s_val = float(scenes[i].get("start_s", scenes[i - 1].get("end_s", 0.0)))
            raw_starts.append(max(0, round(s_val * fps)))
    else:
        accum_s = 0.0
        for i in range(1, n):
            accum_s += float(scenes[i - 1].get("duration_s", 5.0))
            raw_starts.append(max(0, round(accum_s * fps)))

    edges = [0] * (n + 1)
    edges[0] = 0
    edges[n] = vo_frames
    for i in range(1, n):
        edges[i] = max(edges[i - 1] + 1, raw_starts[i])
    for i in range(n - 1, 0, -1):
        edges[i] = min(edges[i], edges[i + 1] - 1)

    return [(edges[i], edges[i + 1]) for i in range(n)]


def _add_rate_el(parent: ET.Element, fps: int) -> ET.Element:
    rate = ET.SubElement(parent, "rate")
    ET.SubElement(rate, "timebase").text = str(fps)
    ET.SubElement(rate, "ntsc").text = "FALSE"
    return rate


def _build_xmeml(
    *,
    project_name: str,
    fps: int,
    width: int,
    height: int,
    vo_frames: int,
    scene_records: list[dict[str, Any]],
    vo_rel_path: str,
    vo_channels: int,
    vo_samplerate: int,
    music_rel_path: str | None,
    music_file_frames: int,
    music_clip_frames: int,
    music_channels: int,
    music_samplerate: int,
    music_db: float,
) -> str:
    """Build FCP7-style xmeml version 4 XML string."""
    root = ET.Element("xmeml", {"version": "4"})
    seq = ET.SubElement(root, "sequence", {"id": "sequence-1"})
    ET.SubElement(seq, "name").text = project_name
    ET.SubElement(seq, "duration").text = str(vo_frames)
    _add_rate_el(seq, fps)

    tc = ET.SubElement(seq, "timecode")
    _add_rate_el(tc, fps)
    ET.SubElement(tc, "string").text = "00:00:00:00"
    ET.SubElement(tc, "frame").text = "0"
    ET.SubElement(tc, "displayformat").text = "NDF"

    for rec in scene_records:
        vo_text = str(rec.get("vo_text") or "").strip()
        if vo_text:
            marker = ET.SubElement(seq, "marker")
            ET.SubElement(marker, "name").text = f"Scene {rec['index']:02d}"
            ET.SubElement(marker, "comment").text = vo_text
            ET.SubElement(marker, "in").text = str(rec["start_frame"])
            ET.SubElement(marker, "out").text = "-1"

    media = ET.SubElement(seq, "media")

    # Video section
    video = ET.SubElement(media, "video")
    v_format = ET.SubElement(video, "format")
    v_sc = ET.SubElement(v_format, "samplecharacteristics")
    _add_rate_el(v_sc, fps)
    ET.SubElement(v_sc, "width").text = str(width)
    ET.SubElement(v_sc, "height").text = str(height)
    ET.SubElement(v_sc, "anamorphic").text = "FALSE"
    ET.SubElement(v_sc, "pixelaspectratio").text = "square"
    ET.SubElement(v_sc, "fielddominance").text = "none"

    v_track = ET.SubElement(video, "track")
    ET.SubElement(v_track, "enabled").text = "TRUE"
    ET.SubElement(v_track, "locked").text = "FALSE"

    defined_files: set[str] = set()
    for pos, rec in enumerate(scene_records, start=1):
        clipitem = ET.SubElement(v_track, "clipitem", {"id": f"clipitem-v1-{pos}"})
        asset_name = Path(rec["asset"]).name
        file_id = f"file-scene-{rec['index']:02d}"
        ET.SubElement(clipitem, "name").text = asset_name
        ET.SubElement(clipitem, "enabled").text = "TRUE"
        ET.SubElement(clipitem, "duration").text = str(rec["file_duration_frames"])
        _add_rate_el(clipitem, fps)
        ET.SubElement(clipitem, "start").text = str(rec["start_frame"])
        ET.SubElement(clipitem, "end").text = str(rec["end_frame"])
        ET.SubElement(clipitem, "in").text = str(rec["in_frame"])
        ET.SubElement(clipitem, "out").text = str(rec["out_frame"])

        if file_id not in defined_files:
            defined_files.add(file_id)
            f_el = ET.SubElement(clipitem, "file", {"id": file_id})
            ET.SubElement(f_el, "name").text = asset_name
            ET.SubElement(f_el, "pathurl").text = rec["asset"]
            _add_rate_el(f_el, fps)
            ET.SubElement(f_el, "duration").text = str(rec["file_duration_frames"])
            f_media = ET.SubElement(f_el, "media")
            f_video = ET.SubElement(f_media, "video")
            f_sc = ET.SubElement(f_video, "samplecharacteristics")
            _add_rate_el(f_sc, fps)
            ET.SubElement(f_sc, "width").text = str(rec["width"])
            ET.SubElement(f_sc, "height").text = str(rec["height"])
            ET.SubElement(f_sc, "anamorphic").text = "FALSE"
            ET.SubElement(f_sc, "pixelaspectratio").text = "square"
            ET.SubElement(f_sc, "fielddominance").text = "none"
        else:
            ET.SubElement(clipitem, "file", {"id": file_id})

        vo_text = str(rec.get("vo_text") or "").strip()
        if vo_text:
            comments = ET.SubElement(clipitem, "comments")
            ET.SubElement(comments, "mastercomment1").text = vo_text

    # Audio section
    audio = ET.SubElement(media, "audio")
    ET.SubElement(audio, "numOutputChannels").text = "2"
    a_format = ET.SubElement(audio, "format")
    a_sc = ET.SubElement(a_format, "samplecharacteristics")
    ET.SubElement(a_sc, "depth").text = "16"
    ET.SubElement(a_sc, "samplerate").text = str(vo_samplerate)

    # Track A1: Voiceover
    a1_track = ET.SubElement(audio, "track")
    ET.SubElement(a1_track, "enabled").text = "TRUE"
    ET.SubElement(a1_track, "locked").text = "FALSE"
    a1_clip = ET.SubElement(a1_track, "clipitem", {"id": "clipitem-a1-1"})
    vo_name = Path(vo_rel_path).name
    ET.SubElement(a1_clip, "name").text = vo_name
    ET.SubElement(a1_clip, "enabled").text = "TRUE"
    ET.SubElement(a1_clip, "duration").text = str(vo_frames)
    _add_rate_el(a1_clip, fps)
    ET.SubElement(a1_clip, "start").text = "0"
    ET.SubElement(a1_clip, "end").text = str(vo_frames)
    ET.SubElement(a1_clip, "in").text = "0"
    ET.SubElement(a1_clip, "out").text = str(vo_frames)

    vo_file = ET.SubElement(a1_clip, "file", {"id": "file-voiceover"})
    ET.SubElement(vo_file, "name").text = vo_name
    ET.SubElement(vo_file, "pathurl").text = vo_rel_path
    _add_rate_el(vo_file, fps)
    ET.SubElement(vo_file, "duration").text = str(vo_frames)
    vo_media = ET.SubElement(vo_file, "media")
    vo_audio = ET.SubElement(vo_media, "audio")
    vo_asc = ET.SubElement(vo_audio, "samplecharacteristics")
    ET.SubElement(vo_asc, "depth").text = "16"
    ET.SubElement(vo_asc, "samplerate").text = str(vo_samplerate)
    ET.SubElement(vo_audio, "channelcount").text = str(vo_channels)

    vo_st = ET.SubElement(a1_clip, "sourcetrack")
    ET.SubElement(vo_st, "mediatype").text = "audio"
    ET.SubElement(vo_st, "trackindex").text = "1"

    # Track A2: Optional Music
    if music_rel_path is not None:
        a2_track = ET.SubElement(audio, "track")
        ET.SubElement(a2_track, "enabled").text = "TRUE"
        ET.SubElement(a2_track, "locked").text = "FALSE"
        a2_clip = ET.SubElement(a2_track, "clipitem", {"id": "clipitem-a2-1"})
        music_name = Path(music_rel_path).name
        ET.SubElement(a2_clip, "name").text = music_name
        ET.SubElement(a2_clip, "enabled").text = "TRUE"
        ET.SubElement(a2_clip, "duration").text = str(music_file_frames)
        _add_rate_el(a2_clip, fps)
        ET.SubElement(a2_clip, "start").text = "0"
        ET.SubElement(a2_clip, "end").text = str(music_clip_frames)
        ET.SubElement(a2_clip, "in").text = "0"
        ET.SubElement(a2_clip, "out").text = str(music_clip_frames)

        m_file = ET.SubElement(a2_clip, "file", {"id": "file-music"})
        ET.SubElement(m_file, "name").text = music_name
        ET.SubElement(m_file, "pathurl").text = music_rel_path
        _add_rate_el(m_file, fps)
        ET.SubElement(m_file, "duration").text = str(music_file_frames)
        m_media = ET.SubElement(m_file, "media")
        m_audio = ET.SubElement(m_media, "audio")
        m_asc = ET.SubElement(m_audio, "samplecharacteristics")
        ET.SubElement(m_asc, "depth").text = "16"
        ET.SubElement(m_asc, "samplerate").text = str(music_samplerate)
        ET.SubElement(m_audio, "channelcount").text = str(music_channels)

        m_st = ET.SubElement(a2_clip, "sourcetrack")
        ET.SubElement(m_st, "mediatype").text = "audio"
        ET.SubElement(m_st, "trackindex").text = "1"

        linear_gain = 10.0 ** (float(music_db) / 20.0)
        filt = ET.SubElement(a2_clip, "filter")
        eff = ET.SubElement(filt, "effect")
        ET.SubElement(eff, "name").text = "Audio Levels"
        ET.SubElement(eff, "effectid").text = "audiolevels"
        ET.SubElement(eff, "effectcategory").text = "audiolevels"
        ET.SubElement(eff, "effecttype").text = "audiolevels"
        ET.SubElement(eff, "mediatype").text = "audio"
        param = ET.SubElement(eff, "parameter", {"authoringApp": "PremierePro"})
        ET.SubElement(param, "parameterid").text = "level"
        ET.SubElement(param, "name").text = "Level"
        ET.SubElement(param, "valuemin").text = "0"
        ET.SubElement(param, "valuemax").text = "3.98109"
        ET.SubElement(param, "value").text = f"{linear_gain:.6f}"

    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n' + body + "\n"


def _build_fcpxml(
    *,
    project_name: str,
    fps: int,
    width: int,
    height: int,
    vo_frames: int,
    scene_records: list[dict[str, Any]],
    vo_rel_path: str,
    vo_channels: int,
    vo_samplerate: int,
    music_rel_path: str | None,
    music_file_frames: int,
    music_clip_frames: int,
    music_channels: int,
    music_samplerate: int,
    music_db: float,
) -> str:
    """Build FCPXML 1.10 document string."""
    root = ET.Element("fcpxml", {"version": "1.10"})
    resources = ET.SubElement(root, "resources")
    ET.SubElement(
        resources,
        "format",
        {
            "id": "r1",
            "name": f"FFVideoFormat{height}p{fps}",
            "frameDuration": f"1/{fps}s",
            "width": str(width),
            "height": str(height),
            "colorSpace": "1-1-1 (Rec. 709)",
        },
    )

    for rec in scene_records:
        asset_id = f"r_scene_{rec['index']:02d}"
        asset_name = Path(rec["asset"]).name
        ET.SubElement(
            resources,
            "asset",
            {
                "id": asset_id,
                "name": asset_name,
                "src": rec["asset"],
                "start": "0s",
                "duration": f"{rec['file_duration_frames']}/{fps}s",
                "hasVideo": "1",
                "hasAudio": "0",
                "format": "r1",
            },
        )

    vo_name = Path(vo_rel_path).name
    ET.SubElement(
        resources,
        "asset",
        {
            "id": "r_voiceover",
            "name": vo_name,
            "src": vo_rel_path,
            "start": "0s",
            "duration": f"{vo_frames}/{fps}s",
            "hasVideo": "0",
            "hasAudio": "1",
            "audioSources": "1",
            "audioChannels": str(vo_channels),
            "audioRate": str(vo_samplerate),
        },
    )

    if music_rel_path is not None:
        music_name = Path(music_rel_path).name
        ET.SubElement(
            resources,
            "asset",
            {
                "id": "r_music",
                "name": music_name,
                "src": music_rel_path,
                "start": "0s",
                "duration": f"{music_file_frames}/{fps}s",
                "hasVideo": "0",
                "hasAudio": "1",
                "audioSources": "1",
                "audioChannels": str(music_channels),
                "audioRate": str(music_samplerate),
            },
        )

    library = ET.SubElement(root, "library")
    event = ET.SubElement(library, "event", {"name": project_name})
    project = ET.SubElement(event, "project", {"name": project_name})
    sequence = ET.SubElement(
        project,
        "sequence",
        {
            "format": "r1",
            "duration": f"{vo_frames}/{fps}s",
            "tcStart": "0s",
            "tcFormat": "NDF",
            "audioLayout": "stereo",
            "audioRate": "48k",
        },
    )
    spine = ET.SubElement(sequence, "spine")

    for pos, rec in enumerate(scene_records):
        asset_id = f"r_scene_{rec['index']:02d}"
        asset_name = Path(rec["asset"]).name
        clip_el = ET.SubElement(
            spine,
            "asset-clip",
            {
                "name": asset_name,
                "ref": asset_id,
                "offset": f"{rec['start_frame']}/{fps}s",
                "start": f"{rec['in_frame']}/{fps}s",
                "duration": f"{rec['duration_frames']}/{fps}s",
                "format": "r1",
            },
        )
        vo_text = str(rec.get("vo_text") or "").strip()
        if vo_text:
            ET.SubElement(clip_el, "note").text = vo_text

        if pos == 0:
            ET.SubElement(
                clip_el,
                "asset-clip",
                {
                    "name": vo_name,
                    "ref": "r_voiceover",
                    "lane": "-1",
                    "offset": f"0/{fps}s",
                    "start": "0s",
                    "duration": f"{vo_frames}/{fps}s",
                    "audioRole": "dialogue",
                },
            )
            if music_rel_path is not None:
                music_name = Path(music_rel_path).name
                m_clip = ET.SubElement(
                    clip_el,
                    "asset-clip",
                    {
                        "name": music_name,
                        "ref": "r_music",
                        "lane": "-2",
                        "offset": f"0/{fps}s",
                        "start": "0s",
                        "duration": f"{music_clip_frames}/{fps}s",
                        "audioRole": "music",
                    },
                )
                ET.SubElement(m_clip, "adjust-volume", {"amount": f"{music_db:g}dB"})

    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n' + body + "\n"


def _build_readme(
    *,
    project_name: str,
    fps: int,
    resolution: str,
    vo_frames: int,
    vo_duration_s: float,
    has_fcpxml: bool,
    has_srt: bool,
    has_music: bool,
    music_db: float,
    has_logo: bool,
    reference_rel: str | None,
) -> str:
    """Build human-readable README.txt with editor import instructions."""
    linear_gain = 10.0 ** (float(music_db) / 20.0)
    lines = [
        f"PARALLAX EDITOR HANDOFF PACKAGE — {project_name}",
        "=" * (34 + len(project_name)),
        "",
        f"Resolution: {resolution} @ {fps} fps",
        f"Duration:   {vo_frames} frames ({vo_duration_s:.2f}s)",
        "",
        "WHAT IS IN THIS FOLDER",
        "----------------------",
        "  timeline.xml          FCP7 XML (xmeml v4) for Premiere Pro, DaVinci Resolve, and Final Cut Pro 7",
    ]
    if has_fcpxml:
        lines.append(
            "  timeline.fcpxml       FCPXML 1.10 for Final Cut Pro X and DaVinci Resolve"
        )
    lines.extend([
        "  assets/               Portable copies of voiceover, scene clips/stills"
        + (", background music" if has_music else "")
        + (", and brand logo" if has_logo else ""),
    ])
    if has_srt:
        lines.append("  captions/captions.srt Word-aligned SubRip subtitle track")
    if reference_rel:
        lines.append(f"  {reference_rel:<21} Reference render from Parallax")
    lines.extend([
        "  handoff.json          Machine-readable timing manifest (frames + seconds)",
        "  README.txt            This guide",
        "",
        "IMPORTANT: Keep this folder together! All XML asset paths are relative",
        "(assets/...) so the folder can be moved, zipped, or shared without breaking links.",
        "",
        "HOW TO IMPORT",
        "-------------",
        "1. Adobe Premiere Pro:",
        "   - Choose File > Import... and select timeline.xml.",
        "   - Premiere creates a sequence and media bin automatically.",
        "   - If any media shows offline, right-click the clips > Link Media and point",
        "     at the assets/ folder.",
        "",
        "2. DaVinci Resolve:",
        "   - Choose File > Import > Timeline... (or File > Import > Timeline > Import AAF, EDL, XML...)",
        "     and pick timeline.xml (or timeline.fcpxml).",
        "   - Leave 'Automatically import source clips into media pool' checked; uncheck it",
        "     only if the clips are already imported into your Media Pool.",
        "",
        "3. Final Cut Pro X / Final Cut Pro:",
        "   - For Final Cut Pro X (and Resolve): File > Import > XML... and select timeline.fcpxml.",
        "   - For legacy Final Cut Pro 7: File > Import > XML... and select timeline.xml.",
        "",
        "4. Captions (captions/captions.srt):",
        "   - Premiere Pro: File > Import captions/captions.srt, then drag it onto the",
        "     timeline to create a Captions track.",
        "   - DaVinci Resolve: Right-click in Media Pool > Import Subtitle... and select",
        "     captions/captions.srt.",
        "",
        "5. CapCut:",
        "   - CapCut does NOT import XML or FCPXML timelines.",
        "   - Instead, drag the loose scene files from assets/ in order (scene_NN_* names",
        "     are sorted in timeline order), drop assets/voiceover.* on the audio track,",
        "     import captions/captions.srt for captions, and see handoff.json for exact",
        "     scene start/end times.",
        "",
        "AUDIO & MUSIC NOTES",
        "-------------------",
        "  - Track A1: Voiceover (0 dB unity gain).",
    ])
    if has_music:
        lines.append(
            f"  - Track A2: Music level is set to {music_db:g} dB (linear gain {linear_gain:.4f}). "
            "The music track is trimmed/clamped to the sequence length and is NOT looped."
        )
    else:
        lines.append(
            f"  - No background music track was included (default music level when supplied: {music_db:g} dB, trimmed/not looped)."
        )
    lines.append("")
    return "\n".join(lines)


def validate_editor_package(out_dir: str | Path) -> list[str]:
    """Validate an exported editor handoff package. Returns a list of problems (empty = valid)."""
    problems: list[str] = []
    out_path = Path(out_dir).expanduser().resolve()
    if not out_path.is_dir():
        return [f"Output directory does not exist: {out_path}"]

    xml_path = out_path / "timeline.xml"
    if not xml_path.is_file():
        return [f"Missing timeline.xml in {out_path}"]

    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
    except ET.ParseError as exc:
        return [f"timeline.xml failed to parse: {exc}"]

    if root.tag != "xmeml":
        problems.append(f"timeline.xml root tag must be 'xmeml', got {root.tag!r}")

    seq = root.find("sequence")
    if seq is None:
        problems.append("timeline.xml is missing <sequence>")
        return problems

    try:
        seq_dur = int(seq.findtext("duration") or "0")
    except ValueError:
        seq_dur = 0
    if seq_dur <= 0:
        problems.append(f"Invalid sequence <duration>: {seq.findtext('duration')!r}")

    # Check every <pathurl> in timeline.xml is relative and exists under out_dir
    pathurls = [el.text or "" for el in root.iter("pathurl")]
    if not pathurls:
        problems.append("timeline.xml contains no <pathurl> elements")
    for raw_url in pathurls:
        val = raw_url.strip()
        if not val:
            problems.append("Empty <pathurl> element in timeline.xml")
            continue
        if (
            val.startswith("/")
            or val.startswith("file://")
            or Path(val).is_absolute()
            or ".." in Path(val).parts
        ):
            problems.append(f"<pathurl> must be a relative path inside package, got: {val!r}")
            continue
        target = out_path / val
        if not target.is_file():
            problems.append(f"Asset referenced by <pathurl> does not exist: {val}")

    # Check V1 track clipitems
    v_track = seq.find("media/video/track")
    if v_track is None:
        problems.append("timeline.xml is missing media/video/track (V1)")
    else:
        v_clips = v_track.findall("clipitem")
        if not v_clips:
            problems.append("V1 track has no <clipitem> elements")
        else:
            total_v1_frames = 0
            expected_start = 0
            for idx, clip in enumerate(v_clips):
                try:
                    start_f = int(clip.findtext("start") or "-1")
                    end_f = int(clip.findtext("end") or "-1")
                except ValueError:
                    problems.append(f"V1 clipitem {idx} has non-integer start/end")
                    continue
                if idx == 0 and start_f != 0:
                    problems.append(f"First V1 clipitem must start at 0, got {start_f}")
                if start_f != expected_start:
                    problems.append(
                        f"V1 clipitem {idx} is not contiguous: expected start {expected_start}, got {start_f}"
                    )
                if end_f <= start_f:
                    problems.append(
                        f"V1 clipitem {idx} has non-positive duration: start={start_f}, end={end_f}"
                    )
                total_v1_frames += max(0, end_f - start_f)
                expected_start = end_f

            if total_v1_frames != seq_dur:
                problems.append(
                    f"V1 clip durations sum to {total_v1_frames} frames, expected sequence duration {seq_dur}"
                )
            if expected_start != seq_dur:
                problems.append(
                    f"Last V1 clipitem ends at {expected_start}, expected sequence duration {seq_dur}"
                )

    # Check audio tracks present
    a_tracks = seq.findall("media/audio/track")
    if not a_tracks:
        problems.append("timeline.xml has no audio tracks")
    else:
        a1_clips = a_tracks[0].findall("clipitem")
        if not a1_clips:
            problems.append("Audio track A1 (voiceover) has no <clipitem>")

    # Validate timeline.fcpxml if present
    fcpxml_path = out_path / "timeline.fcpxml"
    if fcpxml_path.is_file():
        try:
            f_tree = ET.parse(fcpxml_path)
            f_root = f_tree.getroot()
            if f_root.tag != "fcpxml":
                problems.append(f"timeline.fcpxml root tag must be 'fcpxml', got {f_root.tag!r}")
            src_els = [el.attrib["src"] for el in f_root.iter() if "src" in el.attrib]
            if not src_els:
                problems.append("timeline.fcpxml has no asset src attributes")
            for src_val in src_els:
                if (
                    src_val.startswith("/")
                    or src_val.startswith("file://")
                    or Path(src_val).is_absolute()
                    or ".." in Path(src_val).parts
                ):
                    problems.append(f"FCPXML src must be relative, got: {src_val!r}")
                    continue
                if not (out_path / src_val).is_file():
                    problems.append(f"Asset referenced by FCPXML src does not exist: {src_val}")
        except ET.ParseError as exc:
            problems.append(f"timeline.fcpxml failed to parse: {exc}")

    return problems


def export_editor_package(
    folder: str | Path,
    out_dir: str | Path | None = None,
    *,
    version: int | None = None,
    manifest_path: str | Path | None = None,
    music: str | Path | None = None,
    music_db: float = -18.0,
    logo: str | Path | None = None,
    brand: str | Path | None = None,
    fps: int = 30,
    fcpxml: bool = True,
    render_stills: bool = True,
    include_reference: bool = True,
) -> dict[str, Any]:
    """Export a portable editor handoff package (Premiere Pro / DaVinci Resolve / FCP)."""
    if fps <= 0:
        raise ValueError(f"fps must be a positive integer, got {fps}")

    folder_path = Path(folder).expanduser().resolve()
    if not folder_path.is_dir():
        raise FileNotFoundError(f"Project folder not found: {folder_path}")

    out_path = (
        Path(out_dir).expanduser().resolve()
        if out_dir is not None
        else (folder_path / "editor-handoff").resolve()
    )
    _validate_safe_out_dir(folder_path, out_path)

    resolved_manifest = _resolve_manifest_path(
        folder_path, manifest_path=manifest_path, version=version
    )
    manifest_dir = resolved_manifest.parent
    manifest: dict[str, Any] = yaml.safe_load(resolved_manifest.read_text(encoding="utf-8")) or {}
    if not isinstance(manifest, dict):
        raise ValueError(f"Invalid manifest YAML in {resolved_manifest}")

    scenes_raw = manifest.get("scenes") or []
    if not isinstance(scenes_raw, list) or not scenes_raw:
        raise ValueError(f"Manifest {resolved_manifest} has no scenes")
    scenes = _resolve_auto_trim([dict(s) for s in scenes_raw])

    resolution = str(manifest.get("resolution") or "720x1280")
    width, height = parse_resolution(resolution)
    project_name = folder_path.name

    warnings: list[str] = []

    # Resolve voiceover audio
    audio_src = _resolve_project_file(manifest.get("audio_path"), folder_path, manifest_dir)
    if audio_src is None or not audio_src.is_file():
        raise FileNotFoundError(
            f"Voiceover audio_path {manifest.get('audio_path')!r} not found for manifest {resolved_manifest}"
        )

    vo_duration_s = _probe_media_duration_s(audio_src)
    if vo_duration_s <= 0:
        vo_duration_s = float(scenes[-1].get("end_s") or sum(float(s.get("duration_s", 5.0)) for s in scenes))
    has_explicit_end = all(
        s.get("start_s") is not None and s.get("end_s") is not None for s in scenes
    )
    if has_explicit_end and float(scenes[-1].get("end_s") or 0.0) > 0:
        vo_frames = max(len(scenes), round(float(scenes[-1]["end_s"]) * fps))
    else:
        vo_frames = max(len(scenes), round(vo_duration_s * fps))
    vo_channels, vo_samplerate = _probe_audio_stream_info(audio_src)

    # Resolve optional music file before cleaning out_dir
    music_src: Path | None = None
    if music is not None:
        raw_music = Path(music).expanduser()
        m_cands = [raw_music] if raw_music.is_absolute() else [Path.cwd() / raw_music, folder_path / raw_music]
        for mc in m_cands:
            if mc.is_file():
                music_src = mc.resolve()
                break
        if music_src is None:
            raise FileNotFoundError(f"Music file not found: {music}")

    # Resolve optional logo file before cleaning out_dir
    logo_src = _resolve_logo_path(
        folder_path, manifest_dir, logo=logo, brand=brand, warnings=warnings
    )

    # Clean and prepare output directory structure
    _clean_out_dir(out_path)
    assets_out = out_path / "assets"
    assets_out.mkdir(parents=True, exist_ok=True)

    written_files: list[str] = []

    # Copy voiceover
    vo_ext = audio_src.suffix.lower() or ".mp3"
    vo_rel = f"assets/voiceover{vo_ext}"
    shutil.copy2(audio_src, out_path / vo_rel)
    written_files.append(vo_rel)

    # Copy optional music
    music_rel: str | None = None
    music_file_frames = 0
    music_clip_frames = 0
    music_channels, music_samplerate = 2, 44100
    if music_src is not None:
        m_ext = music_src.suffix.lower() or ".mp3"
        music_rel = f"assets/music{m_ext}"
        shutil.copy2(music_src, out_path / music_rel)
        written_files.append(music_rel)
        m_dur_s = _probe_media_duration_s(music_src)
        music_file_frames = max(1, round(m_dur_s * fps)) if m_dur_s > 0 else vo_frames
        music_channels, music_samplerate = _probe_audio_stream_info(music_src)
        if music_file_frames < vo_frames:
            music_clip_frames = music_file_frames
            warnings.append(
                f"Music track ({m_dur_s:.2f}s, {music_file_frames} frames) is shorter than "
                f"sequence ({vo_duration_s:.2f}s, {vo_frames} frames); clamped without looping."
            )
        else:
            music_clip_frames = vo_frames

    # Compute contiguous frame slots across scenes
    frame_slots = _compute_scene_frame_grid(scenes, vo_frames, fps)
    scene_records: list[dict[str, Any]] = []

    for pos, (scene, (start_frame, end_frame)) in enumerate(zip(scenes, frame_slots)):
        idx = int(scene.get("index", pos))
        slot_frames = end_frame - start_frame
        slot_dur_s = slot_frames / float(fps)

        clip_src = _resolve_project_file(scene.get("clip_path"), folder_path, manifest_dir)
        still_src = _resolve_project_file(
            scene.get("still_path") or scene.get("image_path"), folder_path, manifest_dir
        )

        has_clip = clip_src is not None and clip_src.is_file()
        has_still = still_src is not None and still_src.is_file()

        if has_clip:
            assert clip_src is not None
            kind = _scene_kind(scene, has_clip=True)
            ext = clip_src.suffix.lower() or ".mp4"
            rel_asset = f"assets/scene_{idx:02d}_{kind}{ext}"
            dst_asset = out_path / rel_asset
            shutil.copy2(clip_src, dst_asset)
            written_files.append(rel_asset)

            clip_dur_s = _probe_media_duration_s(dst_asset)
            source_frames = max(1, round(clip_dur_s * fps)) if clip_dur_s > 0 else slot_frames
            probed_res = probe_resolution(dst_asset)
            clip_w, clip_h = probed_res if probed_res else (width, height)

            trim_start_s = float(scene.get("clip_trim_start_s") or 0.0)
            in_frame = max(0, round(trim_start_s * fps))
            if scene.get("clip_trim_end_s") is not None:
                trim_end_frames = max(in_frame + 1, round(float(scene["clip_trim_end_s"]) * fps))
                avail_end = min(source_frames, trim_end_frames)
            else:
                avail_end = source_frames

            desired_out = in_frame + slot_frames
            if avail_end < desired_out:
                out_frame = max(in_frame + 1, avail_end) if avail_end > in_frame else source_frames
                warnings.append(
                    f"Scene {idx:02d}: clip ({ max(0, avail_end - in_frame) } frames) is shorter "
                    f"than scene slot ({slot_frames} frames); clamped out to {out_frame}."
                )
            else:
                out_frame = desired_out
            file_duration_frames = max(source_frames, out_frame)

        elif has_still:
            assert still_src is not None
            if render_stills:
                rel_asset = f"assets/scene_{idx:02d}_still.mp4"
                dst_asset = out_path / rel_asset
                zoom_dir = scene.get("zoom_direction")
                zoom_amt = float(scene["zoom_amount"]) if scene.get("zoom_amount") is not None else None
                _render_still_kb_clip(
                    still_src,
                    slot_frames,
                    fps,
                    resolution,
                    dst_asset,
                    scene_index=pos,
                    zoom_direction=zoom_dir,
                    zoom_amount=zoom_amt,
                )
                written_files.append(rel_asset)
                in_frame = 0
                out_frame = slot_frames
                file_duration_frames = slot_frames
                clip_w, clip_h = width, height
                kind = "still"
            else:
                ext = still_src.suffix.lower() or ".png"
                rel_asset = f"assets/scene_{idx:02d}_still{ext}"
                dst_asset = out_path / rel_asset
                shutil.copy2(still_src, dst_asset)
                written_files.append(rel_asset)
                in_frame = 0
                out_frame = slot_frames
                file_duration_frames = slot_frames
                probed_res = probe_resolution(dst_asset)
                clip_w, clip_h = probed_res if probed_res else (width, height)
                kind = "still"
        else:
            raise FileNotFoundError(
                f"Scene {idx:02d} has no existing clip_path or still_path on disk"
            )

        scene_records.append({
            "index": idx,
            "kind": kind,
            "asset": rel_asset,
            "start_s": round(start_frame / float(fps), 4),
            "end_s": round(end_frame / float(fps), 4),
            "duration_s": round(slot_dur_s, 4),
            "manifest_start_s": float(scene.get("start_s", start_frame / float(fps))),
            "manifest_end_s": float(scene.get("end_s", end_frame / float(fps))),
            "start_frame": start_frame,
            "end_frame": end_frame,
            "duration_frames": slot_frames,
            "in_frame": in_frame,
            "out_frame": out_frame,
            "file_duration_frames": file_duration_frames,
            "width": clip_w,
            "height": clip_h,
            "vo_text": str(scene.get("vo_text") or ""),
        })

    # Copy optional logo
    has_logo = False
    if logo_src is not None and logo_src.is_file():
        l_ext = logo_src.suffix.lower() or ".png"
        logo_rel = f"assets/logo{l_ext}"
        shutil.copy2(logo_src, out_path / logo_rel)
        written_files.append(logo_rel)
        has_logo = True

    # Build captions/captions.srt if words_path exists
    has_srt = False
    words_src = _resolve_project_file(manifest.get("words_path"), folder_path, manifest_dir)
    if words_src is not None and words_src.is_file():
        try:
            words_payload = json.loads(words_src.read_text(encoding="utf-8"))
            words_list = (
                words_payload
                if isinstance(words_payload, list)
                else (words_payload.get("words") or [])
            )
            if words_list:
                wpc = _find_words_per_chunk(manifest, folder_path, manifest_dir)
                srt_text = _build_srt(words_list, words_per_chunk=wpc)
                if srt_text.strip():
                    captions_dir = out_path / "captions"
                    captions_dir.mkdir(parents=True, exist_ok=True)
                    srt_rel = "captions/captions.srt"
                    (out_path / srt_rel).write_text(srt_text, encoding="utf-8")
                    written_files.append(srt_rel)
                    has_srt = True
                else:
                    warnings.append("Words file produced no caption chunks; skipped captions/captions.srt.")
            else:
                warnings.append("Words file is empty; skipped captions/captions.srt.")
        except Exception as exc:
            warnings.append(f"Failed to build captions.srt from {words_src.name}: {exc}")
    else:
        warnings.append("No words_path file found; skipped captions/captions.srt.")

    # Optional reference MP4 copy
    reference_rel: str | None = None
    if include_reference:
        ref_mp4 = _find_reference_video(manifest, folder_path, manifest_dir)
        if ref_mp4 is not None and ref_mp4.is_file():
            ref_dir = out_path / "reference"
            ref_dir.mkdir(parents=True, exist_ok=True)
            reference_rel = f"reference/{ref_mp4.name}"
            shutil.copy2(ref_mp4, out_path / reference_rel)
            written_files.append(reference_rel)

    # Write timeline.xml (xmeml v4)
    xmeml_str = _build_xmeml(
        project_name=project_name,
        fps=fps,
        width=width,
        height=height,
        vo_frames=vo_frames,
        scene_records=scene_records,
        vo_rel_path=vo_rel,
        vo_channels=vo_channels,
        vo_samplerate=vo_samplerate,
        music_rel_path=music_rel,
        music_file_frames=music_file_frames,
        music_clip_frames=music_clip_frames,
        music_channels=music_channels,
        music_samplerate=music_samplerate,
        music_db=music_db,
    )
    (out_path / "timeline.xml").write_text(xmeml_str, encoding="utf-8")
    written_files.append("timeline.xml")

    # Write timeline.fcpxml (FCPXML 1.10) if requested
    if fcpxml:
        fcpxml_str = _build_fcpxml(
            project_name=project_name,
            fps=fps,
            width=width,
            height=height,
            vo_frames=vo_frames,
            scene_records=scene_records,
            vo_rel_path=vo_rel,
            vo_channels=vo_channels,
            vo_samplerate=vo_samplerate,
            music_rel_path=music_rel,
            music_file_frames=music_file_frames,
            music_clip_frames=music_clip_frames,
            music_channels=music_channels,
            music_samplerate=music_samplerate,
            music_db=music_db,
        )
        (out_path / "timeline.fcpxml").write_text(fcpxml_str, encoding="utf-8")
        written_files.append("timeline.fcpxml")

    # Write README.txt
    readme_text = _build_readme(
        project_name=project_name,
        fps=fps,
        resolution=resolution,
        vo_frames=vo_frames,
        vo_duration_s=vo_frames / float(fps),
        has_fcpxml=fcpxml,
        has_srt=has_srt,
        has_music=music_rel is not None,
        music_db=music_db,
        has_logo=has_logo,
        reference_rel=reference_rel,
    )
    (out_path / "README.txt").write_text(readme_text, encoding="utf-8")
    written_files.append("README.txt")

    written_files.append("handoff.json")
    handoff_payload: dict[str, Any] = {
        "project": project_name,
        "version": manifest.get("version", version or 1),
        "resolution": resolution,
        "width": width,
        "height": height,
        "fps": fps,
        "sequence_duration_frames": vo_frames,
        "sequence_duration_s": round(vo_frames / float(fps), 4),
        "music_db": float(music_db) if music_rel is not None else None,
        "files": list(written_files),
        "scenes": scene_records,
        "warnings": list(warnings),
    }
    (out_path / "handoff.json").write_text(
        json.dumps(handoff_payload, indent=2) + "\n", encoding="utf-8"
    )

    problems = validate_editor_package(out_path)
    if problems:
        raise RuntimeError(
            f"Editor handoff package validation failed: {'; '.join(problems)}"
        )

    return {
        "out_dir": str(out_path),
        **handoff_payload,
        "problems": problems,
    }
