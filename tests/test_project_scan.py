"""scan_project_folder behaviour snapshot.

Locks in:
  - mode='ken_burns' when fewer than 3 numbered clips, 'video_clips' otherwise.
  - script.txt is preferred over a lone .txt file.
  - character.<ext> is preferred over arbitrary single image.
  - Versioned output dir parallax/output/v<N>/ is auto-created and increments.
  - Numbered clips are excluded from character_image candidates.
"""

from __future__ import annotations

import json

from parallax.tools_video import scan_project_folder


def _w(p, content="x"):
    p.write_text(content) if p.suffix in (".txt", ".md") else p.write_bytes(b"\x00")


def test_scan_ken_burns_mode_with_script_and_character(tmp_path):
    (tmp_path / "script.txt").write_text("Hello world")
    (tmp_path / "character.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    out = json.loads(scan_project_folder(str(tmp_path)))
    assert out["mode"] == "ken_burns"
    assert out["script_text"] == "Hello world"
    assert out["script_path"].endswith("script.txt")
    assert out["character_image_path"].endswith("character.png")
    assert out["clips"] == {}


def test_scan_video_clips_mode_with_three_numbered(tmp_path):
    (tmp_path / "script.txt").write_text("Hi")
    for i in (1, 2, 3):
        (tmp_path / f"00{i}.mp4").write_bytes(b"\x00")

    out = json.loads(scan_project_folder(str(tmp_path)))
    assert out["mode"] == "video_clips"
    assert set(out["clips"].keys()) == {"1", "2", "3"}


def test_scan_two_numbered_still_ken_burns(tmp_path):
    """<3 numbered clips falls back to ken_burns mode."""
    (tmp_path / "script.txt").write_text("Hi")
    (tmp_path / "001.mp4").write_bytes(b"\x00")
    (tmp_path / "002.mp4").write_bytes(b"\x00")

    out = json.loads(scan_project_folder(str(tmp_path)))
    assert out["mode"] == "ken_burns"


def test_scan_lone_txt_treated_as_script(tmp_path):
    (tmp_path / "brief_for_agent.txt").write_text("only one")
    out = json.loads(scan_project_folder(str(tmp_path)))
    assert out["script_text"] == "only one"


def test_scan_multiple_txt_without_script_raises(tmp_path):
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.txt").write_text("b")
    import pytest
    with pytest.raises(ValueError, match="Multiple text files"):
        scan_project_folder(str(tmp_path))


def test_scan_versioned_output_dir_increments(tmp_path):
    (tmp_path / "script.txt").write_text("hi")
    out1 = json.loads(scan_project_folder(str(tmp_path)))
    out2 = json.loads(scan_project_folder(str(tmp_path)))
    assert out1["version"] == 1
    assert out2["version"] == 2
    assert out2["output_dir"].endswith("/v2")


def test_scan_excludes_numbered_clips_from_character_pool(tmp_path):
    """A numbered .png clip should not be picked as the character image."""
    (tmp_path / "script.txt").write_text("hi")
    for i in (1, 2, 3):
        (tmp_path / f"00{i}.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    # No character.* file present — char should resolve to None or to a non-numbered image
    out = json.loads(scan_project_folder(str(tmp_path)))
    assert out["mode"] == "video_clips"
    # character_image_path should not be one of the numbered clips
    if out["character_image_path"]:
        stem = out["character_image_path"].split("/")[-1].split(".")[0]
        assert not stem.isdigit()


def test_scan_not_a_directory_raises(tmp_path):
    import pytest
    with pytest.raises(ValueError, match="Not a directory"):
        scan_project_folder(str(tmp_path / "does-not-exist"))
