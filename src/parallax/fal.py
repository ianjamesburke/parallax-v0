"""Real FAL integration for `generate_image`.

`generate(prompt, spec, *, runner=None, downloader=None)` is the only public
entry point. Both dependencies are injectable so tests can stay hermetic
without monkeypatching the fal_client module.

The runner returns whatever shape FAL gave us; we extract the first image
URL, download it to `output_dir()/<hash>.png`, and return the path. FAL-side
errors (auth, quota, model outage, safety blocks) propagate as RuntimeError
with the FAL exception preserved as __cause__ so the agent loop surfaces
them as tool_result errors rather than crashing.
"""

from __future__ import annotations

import hashlib
import os
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .log import get_logger
from .pricing import ModelSpec
from .shim import output_dir

log = get_logger("fal")

Runner = Callable[[str, dict[str, Any]], dict[str, Any]]
Downloader = Callable[[str, Path], None]
Uploader = Callable[[Path], str]


def check_available() -> None:
    if not os.environ.get("FAL_KEY"):
        raise RuntimeError(
            "FAL integration requires FAL_KEY to be set. "
            "Export it, or set PARALLAX_TEST_MODE=1 to use the shim."
        )


def _default_runner(fal_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
    import fal_client  # lazy so tests don't import it

    return fal_client.subscribe(fal_id, arguments=arguments, with_logs=False)


def _default_downloader(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url) as resp, dest.open("wb") as f:
        f.write(resp.read())


def _default_uploader(path: Path) -> str:
    import fal_client

    return fal_client.upload_file(str(path))


def _url_ext(url: str) -> str:
    path = urllib.parse.urlparse(url).path
    ext = Path(path).suffix.lower()
    if ext in {".png", ".jpg", ".jpeg", ".webp"}:
        return ext
    return ".png"


def _first_image_url(result: dict[str, Any]) -> str:
    images = result.get("images")
    if not isinstance(images, list) or not images:
        raise RuntimeError(f"FAL result missing 'images' list: keys={list(result)}")
    first = images[0]
    if isinstance(first, dict) and isinstance(first.get("url"), str):
        return first["url"]
    if isinstance(first, str):
        return first
    raise RuntimeError(f"FAL result image has no URL: {first!r}")


def generate(
    prompt: str,
    spec: ModelSpec,
    *,
    reference_images: list[Path] | None = None,
    runner: Runner | None = None,
    downloader: Downloader | None = None,
    uploader: Uploader | None = None,
) -> Path:
    check_available()
    run = runner or _default_runner
    dl = downloader or _default_downloader
    up = uploader or _default_uploader

    refs = reference_images or []
    if refs and not spec.supports_reference:
        raise RuntimeError(
            f"Model {spec.alias!r} does not support reference_images "
            f"(no edit endpoint). Caller should have validated this."
        )

    if refs:
        if len(refs) > spec.max_refs:
            raise RuntimeError(
                f"Model {spec.alias!r} accepts at most {spec.max_refs} reference image(s); "
                f"got {len(refs)}."
            )
        ref_urls: list[str] = []
        for p in refs:
            try:
                url = up(p)
            except Exception as e:
                raise RuntimeError(f"FAL upload failed for {p}: {type(e).__name__}: {e}") from e
            ref_urls.append(url)
        fal_id = spec.edit_fal_id
        param = spec.ref_param_name
        assert fal_id is not None and param is not None  # guarded by supports_reference
        args: dict[str, Any] = {"prompt": prompt}
        args[param] = ref_urls if spec.refs_are_list else ref_urls[0]
        log.info("fal call: %s prompt=%r refs=%d", fal_id, prompt[:60], len(ref_urls))
    else:
        fal_id = spec.fal_id
        args = {"prompt": prompt}
        log.info("fal call: %s prompt=%r", fal_id, prompt[:60])

    try:
        result = run(fal_id, args)
    except Exception as e:
        raise RuntimeError(f"FAL call failed for {fal_id}: {type(e).__name__}: {e}") from e

    url = _first_image_url(result)
    ext = _url_ext(url)
    key = hashlib.sha1(f"{spec.alias}|{prompt}|{url}".encode()).hexdigest()[:10]
    dest = output_dir() / f"{spec.alias}_{key}{ext}"
    try:
        dl(url, dest)
    except Exception as e:
        raise RuntimeError(f"FAL download failed from {url}: {type(e).__name__}: {e}") from e
    log.info("fal download: %s", dest)
    return dest
