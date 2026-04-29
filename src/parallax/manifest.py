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


def write_manifest(manifest_json: str, manifest_path: str) -> str:
    """Write a manifest dict (JSON string) to a YAML file. Returns the path."""
    data = json.loads(manifest_json)
    path = Path(manifest_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False))
    log.info("write_manifest: %s", path)
    return str(path)


def read_manifest(manifest_path: str) -> str:
    """Read a manifest YAML file and return its contents as JSON string."""
    data = yaml.safe_load(Path(manifest_path).read_text())
    return json.dumps(data)
