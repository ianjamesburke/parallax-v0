"""Manifest read/write helpers.

Trivial JSON ↔ YAML round-trip used by the produce pipeline to persist a
run's scene/audio/output map for later inspection or replay.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from .log import get_logger

log = get_logger(__name__)


def write_manifest_data(data: dict, manifest_path: str) -> str:
    """Write a manifest dict to a YAML file. Returns the path."""
    path = Path(manifest_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False))
    log.info("write_manifest: %s", path)
    return str(path)


def read_manifest_data(manifest_path: str) -> dict:
    """Read a manifest YAML file and return its contents as a dict."""
    return yaml.safe_load(Path(manifest_path).read_text())


def write_manifest(manifest_json: str, manifest_path: str) -> str:
    """JSON-string wrapper around write_manifest_data. Kept for CLI/external callers."""
    return write_manifest_data(json.loads(manifest_json), manifest_path)


def read_manifest(manifest_path: str) -> str:
    """JSON-string wrapper around read_manifest_data. Kept for CLI/external callers."""
    return json.dumps(read_manifest_data(manifest_path))
