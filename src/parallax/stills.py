"""Still post-processing: validate generated stills match the project aspect ratio.

Image models occasionally ignore the `aspect_ratio` request and return whatever
they feel like (Gemini Flash Image has been observed returning landscape for
prompts that don't textually mention vertical framing). Sending a wrong-aspect
image into an image-to-video model produces stretched faces or off-center
crops that ruin the final video.

The strategy here is fail-loud, not silent-fallback:

  - `validate_aspect` raises `AspectMismatchError` when the still's aspect
    differs from the project resolution by more than 2%. Caller is expected to
    catch the error, regenerate with a sterner prompt, and validate again.
  - `normalize_aspect` micro-trims a still that's already very close to the
    target (<= 2% off — Gemini's natural 768x1376 → true 9:16 falls here).
    For larger mismatches it raises rather than crop, because center-cropping
    a landscape image into a portrait frame discards the subject in 9 of 10
    cases.

The 2% tolerance covers natural model precision (768x1376 vs 768x1365 = 0.8%)
without permitting "I asked for portrait and got landscape" silent recoveries.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Largest source-vs-target aspect ratio mismatch we'll silently micro-trim.
# Anything larger raises and forces a regenerate.
MAX_TRIM_TOLERANCE = 0.02  # 2%


class AspectMismatchError(RuntimeError):
    """Raised when a generated still's aspect ratio is too far off-target to silently fix."""


@dataclass(frozen=True)
class AspectCheck:
    src_w: int
    src_h: int
    src_ratio: float
    target_ratio: float
    mismatch_pct: float
    within_tolerance: bool


def _probe(still_path: Path) -> tuple[int, int]:
    from PIL import Image  # type: ignore[import]

    img = Image.open(still_path)
    return img.size  # (w, h)


def check_aspect(still_path: str | Path, target_resolution: str) -> AspectCheck:
    """Compare a still's aspect to a target resolution. Pure inspection — no I/O writes."""
    from .ffmpeg_utils import parse_resolution

    src = Path(still_path)
    if not src.is_file():
        raise FileNotFoundError(f"check_aspect: still not found: {src}")

    target_w, target_h = parse_resolution(target_resolution)
    target_ratio = target_w / target_h

    src_w, src_h = _probe(src)
    src_ratio = src_w / src_h

    mismatch_pct = abs(src_ratio - target_ratio) / target_ratio
    return AspectCheck(
        src_w=src_w, src_h=src_h, src_ratio=src_ratio,
        target_ratio=target_ratio, mismatch_pct=mismatch_pct,
        within_tolerance=mismatch_pct <= MAX_TRIM_TOLERANCE,
    )


def validate_aspect(still_path: str | Path, target_resolution: str) -> AspectCheck:
    """Raise `AspectMismatchError` if the still is too far off-aspect to micro-trim.

    Returns the `AspectCheck` on success so callers can log/log-event the
    mismatch_pct (will be ≤ MAX_TRIM_TOLERANCE).
    """
    check = check_aspect(still_path, target_resolution)
    if not check.within_tolerance:
        raise AspectMismatchError(
            f"still {Path(still_path).name} aspect ratio {check.src_ratio:.4f} "
            f"({check.src_w}x{check.src_h}) is {check.mismatch_pct*100:.1f}% off "
            f"from target {check.target_ratio:.4f} ({target_resolution}). "
            f"Tolerance is {MAX_TRIM_TOLERANCE*100:.0f}%. The model returned a "
            f"wrong-aspect image — regenerate with a sterner prompt rather than "
            f"silently crop (which would discard subject content)."
        )
    return check


def crop_to_aspect(image_path: str | Path, target_resolution: str) -> Path:
    """Center-crop AND resize an image to exactly `target_resolution`.

    Sibling of `normalize_aspect` for reference-image preprocessing — where
    we WANT to crop aggressively (Gemini matches its output aspect to the
    reference image, so feeding it a non-portrait reference biases the
    output away from 9:16 regardless of any body/prompt directive). This
    helper never raises on large mismatches; cropping a square reference
    to 9:16 is exactly the desired behavior, not an error.

    Output is written as a sibling file `<stem>_a<W>x<H>.png` at exactly
    the target resolution (e.g. 720x1280) so downstream consumers always
    get a uniform size. Idempotent — cached on subsequent calls.
    """
    from PIL import Image  # type: ignore[import]
    from .ffmpeg_utils import parse_resolution

    src = Path(image_path)
    target_w, target_h = parse_resolution(target_resolution)
    if not src.is_file():
        # Source may have been deleted by a prior crop_to_aspect run that
        # wrote the variant and removed the original. Recover gracefully.
        candidate = src.with_name(f"{src.stem}_a{target_w}x{target_h}.png")
        if candidate.is_file():
            return candidate
        raise FileNotFoundError(f"crop_to_aspect: ref not found: {src}")
    target_ratio = target_w / target_h

    # Idempotency guard. The output filename suffix `_a<W>x<H>` made it
    # impossible to recognize an already-cropped file: a second pass
    # over `image1_a720x1280.png` produced `image1_a720x1280_a720x1280.png`,
    # the suffix accumulating on every run. If the source already matches
    # the target resolution exactly, return it untouched.
    img = Image.open(src)
    src_w, src_h = img.size
    if (src_w, src_h) == (target_w, target_h):
        return src
    src_ratio = src_w / src_h

    if src_ratio > target_ratio:
        new_w = round(src_h * target_ratio)
        new_h = src_h
        x0 = (src_w - new_w) // 2
        y0 = 0
    else:
        new_w = src_w
        new_h = round(src_w / target_ratio)
        x0 = 0
        y0 = (src_h - new_h) // 2

    out = src.with_name(f"{src.stem}_a{target_w}x{target_h}.png")
    if out.exists():
        # Cached variant exists from a prior run — drop the now-redundant
        # source so the media bin doesn't carry both copies forward.
        if src != out:
            src.unlink(missing_ok=True)
        return out
    cropped = img.crop((x0, y0, x0 + new_w, y0 + new_h))
    if (cropped.width, cropped.height) != (target_w, target_h):
        cropped = cropped.resize((target_w, target_h), Image.LANCZOS)
    if cropped.mode != "RGB":
        cropped = cropped.convert("RGB")
    cropped.save(out, format="PNG")
    # Source is superseded by the cropped variant; remove it so downstream
    # readers of the media dir see exactly one canonical reference per
    # original. Mirrors `normalize_aspect`'s post-write delete.
    if src != out:
        src.unlink(missing_ok=True)
    return out


def normalize_aspect(still_path: str | Path, target_resolution: str) -> Path:
    """Micro-trim a near-correct still and resize to exactly `target_resolution`.

    Only operates when the source is within `MAX_TRIM_TOLERANCE` of the target;
    raises `AspectMismatchError` otherwise. This makes silent center-crop
    impossible for large mismatches — those must be regenerated.

    Output is written alongside the source as `<stem>_n<W>x<H>.png` at
    exactly `target_resolution` (e.g. 720x1280). After a successful
    normalize, the source is deleted so the folder doesn't accumulate
    near-duplicate copies of every still. Idempotent — if the normalized
    output already exists, the source is removed (if still present) and
    the cached output is returned.
    """
    from PIL import Image  # type: ignore[import]
    from .ffmpeg_utils import parse_resolution

    check = validate_aspect(still_path, target_resolution)  # raises if too far off
    src = Path(still_path)
    target_w, target_h = parse_resolution(target_resolution)

    out = src.with_name(f"{src.stem}_n{target_w}x{target_h}.png")
    if out.exists():
        # Cached normalize — clean up the source if it lingered (e.g. from
        # an interrupted earlier pass).
        if src.exists() and src != out:
            try:
                src.unlink()
            except OSError:
                pass
        return out

    target_ratio = check.target_ratio
    src_w, src_h = check.src_w, check.src_h

    if check.src_ratio > target_ratio:
        new_w = round(src_h * target_ratio)
        new_h = src_h
        x0 = (src_w - new_w) // 2
        y0 = 0
    else:
        new_w = src_w
        new_h = round(src_w / target_ratio)
        x0 = 0
        y0 = (src_h - new_h) // 2

    img = Image.open(src)
    cropped = img.crop((x0, y0, x0 + new_w, y0 + new_h))
    if (cropped.width, cropped.height) != (target_w, target_h):
        cropped = cropped.resize((target_w, target_h), Image.LANCZOS)
    if cropped.mode != "RGB":
        cropped = cropped.convert("RGB")
    cropped.save(out, format="PNG")

    # Delete the un-normalized source — the normalized file is the
    # canonical output and the original is just disk noise.
    if src != out:
        try:
            src.unlink()
        except OSError:
            pass
    return out
