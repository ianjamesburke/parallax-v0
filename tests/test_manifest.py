"""Manifest write/read round-trip tests.

Locks in:
  - write_manifest emits YAML at the requested path and creates parent dirs.
  - read_manifest parses YAML and returns the original shape as JSON.
  - Round-trip preserves nested structures and unicode.
"""

from __future__ import annotations

import json
from pathlib import Path

from parallax.manifest import read_manifest, write_manifest


def test_write_manifest_creates_file_and_parent_dirs(tmp_path):
    path = tmp_path / "nested" / "dir" / "manifest.yaml"
    payload = json.dumps({"version": 1, "scenes": []})
    out = write_manifest(payload, str(path))
    assert Path(out) == path
    assert path.exists()
    assert path.read_text().strip()  # non-empty yaml


def test_round_trip_preserves_shape(tmp_path):
    path = tmp_path / "manifest.yaml"
    data = {
        "version": 2,
        "scenes": [
            {"index": 0, "vo_text": "hi", "duration_s": 1.5},
            {"index": 1, "vo_text": "world", "duration_s": 2.0},
        ],
        "config": {"resolution": "1080x1920", "fps": 30},
    }
    write_manifest(json.dumps(data), str(path))
    out_json = read_manifest(str(path))
    assert json.loads(out_json) == data


def test_round_trip_preserves_unicode(tmp_path):
    path = tmp_path / "manifest.yaml"
    data = {"title": "Café — résumé", "emoji": "🎬", "quote": "\"hello\""}
    write_manifest(json.dumps(data), str(path))
    out = json.loads(read_manifest(str(path)))
    assert out == data
