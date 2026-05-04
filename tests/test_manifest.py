"""Manifest write/read round-trip tests.

Locks in:
  - write_manifest emits YAML at the requested path and creates parent dirs.
  - read_manifest parses YAML and returns the original shape as JSON.
  - Round-trip preserves nested structures and unicode.
"""

from __future__ import annotations

import json
from pathlib import Path

from parallax.manifest import read_manifest, read_manifest_data, write_manifest, write_manifest_data


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


# ─── write_manifest_data / read_manifest_data: object-level API ──────────


def test_write_manifest_data_accepts_dict_directly(tmp_path):
    """write_manifest_data takes a dict, not a JSON string."""
    path = tmp_path / "manifest.yaml"
    data = {"version": 3, "scenes": [{"index": 0}]}
    out = write_manifest_data(data, str(path))
    assert Path(out) == path
    assert path.exists()


def test_read_manifest_data_returns_dict(tmp_path):
    """read_manifest_data returns a dict, not a JSON string."""
    path = tmp_path / "manifest.yaml"
    data = {"version": 4, "resolution": "1080x1920"}
    write_manifest_data(data, str(path))
    result = read_manifest_data(str(path))
    assert isinstance(result, dict)
    assert result == data


def test_object_api_same_result_as_json_wrappers(tmp_path):
    """Object-level and JSON-string APIs produce identical on-disk YAML."""
    path_obj = tmp_path / "obj.yaml"
    path_json = tmp_path / "json.yaml"
    data = {"version": 5, "scenes": [{"index": 0, "duration_s": 2.5}]}
    write_manifest_data(data, str(path_obj))
    write_manifest(json.dumps(data), str(path_json))
    assert path_obj.read_text() == path_json.read_text()
