"""Best-effort update-check nag.

Called at CLI startup. Hits the GitHub releases API at most once per 24h,
caches the result, and prints a one-line nag to stderr when the installed
version is behind. All failures (network, parse, filesystem) are swallowed —
an update check must never break a run.

Disable with PARALLAX_NO_UPDATE_CHECK=1.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from pathlib import Path
from typing import Any, Callable

from .log import get_logger

log = get_logger("update_check")

RELEASES_URL = "https://api.github.com/repos/ianjamesburke/parallax-v0/releases/latest"
CACHE_PATH = Path.home() / ".parallax" / ".update_check"
CACHE_TTL_SECONDS = 24 * 60 * 60


def check_for_update(
    *,
    fetcher: Callable[[], str] | None = None,
    now: Callable[[], float] | None = None,
    cache_path: Path | None = None,
    installed_version: str | None = None,
) -> None:
    """Fire-and-forget update check. Never raises — swallow and log on error."""
    if os.environ.get("PARALLAX_NO_UPDATE_CHECK") == "1":
        return
    try:
        _check_for_update(
            fetcher=fetcher or _fetch_latest_from_github,
            now=now or time.time,
            cache_path=cache_path or CACHE_PATH,
            installed_version=installed_version or _installed_version(),
        )
    except Exception as exc:  # pragma: no cover — defense in depth
        log.debug("update check failed (silenced): %s", exc)


def _check_for_update(
    *,
    fetcher: Callable[[], str],
    now: Callable[[], float],
    cache_path: Path,
    installed_version: str,
) -> None:
    if not installed_version:
        return
    latest = _load_fresh_cache(cache_path, now)
    if latest is None:
        try:
            latest = fetcher()
        except Exception as exc:
            log.debug("fetch failed: %s", exc)
            return
        _write_cache(cache_path, latest, now)
    if _is_newer(latest, installed_version):
        print(
            f"[parallax] A new version is available: v{latest} "
            f"(you have v{installed_version}). Run: parallax update",
            file=sys.stderr,
        )


def _installed_version() -> str:
    try:
        return _pkg_version("parallax")
    except PackageNotFoundError:
        return ""


def _load_fresh_cache(path: Path, now: Callable[[], float]) -> str | None:
    try:
        data: Any = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    last_checked = data.get("last_checked")
    latest = data.get("latest_version")
    if not isinstance(last_checked, (int, float)) or not isinstance(latest, str):
        return None
    if now() - last_checked > CACHE_TTL_SECONDS:
        return None
    return _strip_v(latest)


def _write_cache(path: Path, latest: str, now: Callable[[], float]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"last_checked": now(), "latest_version": latest})
        )
    except OSError as exc:
        log.debug("cache write failed: %s", exc)


def _fetch_latest_from_github() -> str:
    req = urllib.request.Request(
        RELEASES_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "parallax-update-check",
        },
    )
    with urllib.request.urlopen(req, timeout=3) as resp:  # noqa: S310
        body = resp.read()
    payload = json.loads(body)
    tag = payload.get("tag_name") or ""
    return _strip_v(tag)


def _strip_v(tag: str) -> str:
    return tag[1:] if tag.startswith("v") else tag


def _is_newer(latest: str, installed: str) -> bool:
    """Tuple-compare dot-separated numeric versions. Non-numeric segments sort
    as older (so prerelease suffixes don't spuriously trigger the nag)."""
    try:
        lt = _parse(latest)
        it = _parse(installed)
    except ValueError:
        return False
    return lt > it


def _parse(v: str) -> tuple[int, ...]:
    parts: list[int] = []
    for seg in v.split("."):
        head = ""
        for c in seg:
            if c.isdigit():
                head += c
            else:
                break
        parts.append(int(head) if head else 0)
    return tuple(parts)
