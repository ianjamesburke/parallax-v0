"""Pure unit tests for `parallax.cut` (no network, no paid calls)."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest
import yaml

from parallax.cut import (
    CutError,
    align_script_words,
    build_manifest_dict,
    check_manifest_xml_consistency,
    locate_anchors,
    plan_cut,
    resolve_mapping,
    resolve_or_generate_voiceover,
    tokenize_script,
    write_cut_manifest,
)


# ---------------------------------------------------------------------------
# 1. Tokenisation, em-dashes, numbers ("45"), fuzzy ASR alignment & anchors
# ---------------------------------------------------------------------------


def test_tokenize_script_splits_em_dash_joined_words() -> None:
    text = "In 45 AD, limestone—the mineral inside salt—rebuilt the surface."
    tokens = tokenize_script(text)
    assert "limestone—" in tokens
    assert "the" in tokens
    assert "salt—" in tokens
    assert "rebuilt" in tokens
    assert "45" in tokens


def test_align_script_words_handles_em_dash_numbers_and_fuzzy_asr() -> None:
    script = "45 AD. Nano-limestone—the mineral—works fast."
    # ASR transcribed "45" as "forty", "five", and "limestone" as "limestoan"
    asr_words = [
        {"word": "forty", "start": 0.10, "end": 0.30},
        {"word": "five", "start": 0.30, "end": 0.55},
        {"word": "AD", "start": 0.60, "end": 0.90},
        {"word": "nanolimestoan", "start": 1.30, "end": 1.85},
        {"word": "the", "start": 1.90, "end": 2.05},
        {"word": "mineral", "start": 2.10, "end": 2.45},
        {"word": "works", "start": 2.50, "end": 2.80},
    ]
    res = align_script_words(script, asr_words, audio_duration=3.20)
    words = res["words"]

    # Caption words must come from the SCRIPT text (preserving punctuation & em-dashes), not ASR text
    assert [w["word"] for w in words] == [
        "45",
        "AD.",
        "Nano-limestone—",
        "the",
        "mineral—",
        "works",
        "fast.",
    ]
    # "45" coalesced from "forty" (0.10) .. "five" (0.55)
    assert words[0]["start"] == pytest.approx(0.10, abs=1e-3)
    assert words[0]["end"] == pytest.approx(0.55, abs=1e-3)
    # Silence gap between "AD." (0.90) and "Nano-limestone—" (1.30) is preserved!
    assert words[1]["end"] == pytest.approx(0.90, abs=1e-3)
    assert words[2]["start"] == pytest.approx(1.30, abs=1e-3)
    # Unmatched trailing token "fast." is interpolated between 2.80 and 3.20
    assert words[-1]["word"] == "fast."
    assert words[-1]["start"] >= 2.80
    assert words[-1]["end"] == pytest.approx(3.20, abs=1e-2)
    assert any(u["token"] == "fast." for u in res["unmatched_tokens"])


def test_locate_anchors_resolves_repeated_phrases_monotonically() -> None:
    script = "Wait for it. First verse starts now. Wait for it. Second verse begins here. Wait for it."
    tokens = tokenize_script(script)
    aligned = [
        {"word": tok, "start": i * 0.4, "end": i * 0.4 + 0.3, "matched": True}
        for i, tok in enumerate(tokens)
    ]
    segments = [
        {"anchor": "Wait for it.", "clip": "Scene_01.mp4"},
        {"anchor": "Wait for it.", "clip": "Scene_02.mp4"},
        {"anchor": "Wait for it.", "clip": "Scene_03.mp4"},
    ]
    loc = locate_anchors(script, aligned, segments)
    assert loc["errors"] == []
    indices = [item["token_index"] for item in loc["located"]]
    assert indices == [0, 7, 14]


def test_locate_anchors_extends_first_segment_to_zero_when_first_anchor_is_later() -> None:
    script = "Intro line before anchor. Main point starts here. Final thought."
    tokens = tokenize_script(script)
    aligned = [
        {"word": tok, "start": i * 0.5, "end": i * 0.5 + 0.4, "matched": True}
        for i, tok in enumerate(tokens)
    ]
    segments = [
        {"anchor": "Main point", "clip": "Scene_01.mp4"},
        {"anchor": "Final thought.", "clip": "Scene_02.mp4"},
    ]
    loc = locate_anchors(script, aligned, segments)
    assert loc["errors"] == []
    assert any("first segment extended to 0.0s automatically" in w for w in loc["warnings"])

    plan = plan_cut(
        script_text=script,
        words=aligned,
        segments=segments,
        clip_durations={"Scene_01.mp4": 5.0, "Scene_02.mp4": 5.0},
        fps=24,
        audio_duration=5.0,
    )
    assert plan["scenes"][0]["start_s"] == 0.0


def test_locate_anchors_supports_fuzzy_anchor_and_reports_it() -> None:
    script = "In 45 AD, Emperor Claudius built the harbour. Desperate merchants cheered."
    tokens = tokenize_script(script)
    aligned = [
        {"word": tok, "start": i * 0.4, "end": i * 0.4 + 0.35, "matched": True}
        for i, tok in enumerate(tokens)
    ]
    segments = [
        {"anchor": "In 45 AD,", "clip": "Scene_01.mp4"},
        {"anchor": "Desparate merchant", "clip": "Scene_02.mp4"},  # slight typo vs "Desperate merchants"
    ]
    loc = locate_anchors(script, aligned, segments)
    assert loc["errors"] == []
    assert len(loc["fuzzy_anchors"]) == 1
    assert loc["fuzzy_anchors"][0]["segment"] == 1


# ---------------------------------------------------------------------------
# 2. Pure-function planning: midpoint boundaries, redistribution, reuse-trim, overrun
# ---------------------------------------------------------------------------


def test_plan_cut_midpoint_of_gap_redistribution_and_frame_snapping() -> None:
    script = "First beat here. Middle stretch has many words to speak slowly. Ending beat."
    words = [
        {"word": "First", "start": 0.10, "end": 0.40},
        {"word": "beat", "start": 0.40, "end": 0.70},
        {"word": "here.", "start": 0.70, "end": 1.00},
        # 0.40s gap between 1.00 and 1.40 -> midpoint is 1.20s!
        {"word": "Middle", "start": 1.40, "end": 2.00},
        {"word": "stretch", "start": 2.00, "end": 2.60},
        {"word": "has", "start": 2.60, "end": 3.20},
        {"word": "many", "start": 3.20, "end": 3.80},
        {"word": "words", "start": 3.80, "end": 4.40},
        {"word": "to", "start": 4.40, "end": 4.90},
        {"word": "speak", "start": 4.90, "end": 5.40},
        {"word": "slowly.", "start": 5.40, "end": 5.80},
        # 0.20s gap between 5.80 and 6.00 -> midpoint is 5.90s!
        {"word": "Ending", "start": 6.00, "end": 6.40},
        {"word": "beat.", "start": 6.40, "end": 6.95},
    ]
    segments = [
        {"anchor": "First beat", "clip": "c1.mp4"},
        {"anchor": "Middle stretch", "clip": "c2.mp4"},
        {"anchor": "Ending beat.", "clip": "c3.mp4"},
    ]
    # Raw slots are: [0..1.20]=1.20s, [1.20..5.90]=4.70s, [5.90..7.00]=1.10s.
    # Cap c2 at 3.50s while c1 (3.0s) and c3 (3.0s) have plenty of room -> excess 1.20s redistributes to c1 and c3!
    plan = plan_cut(
        script_text=script,
        words=words,
        segments=segments,
        clip_durations={"c1.mp4": 3.0, "c2.mp4": 3.5, "c3.mp4": 3.0},
        fps=24,
        audio_duration=7.0,
    )
    assert plan["raw_boundaries_s"] == [0.0, 1.2, 5.9, 7.0]
    assert plan["overruns"] == []
    assert plan["scenes"][1]["duration_s"] <= 3.5 + (1.0 / 24)
    assert plan["edges_s"][-1] >= 7.0
    assert abs(plan["video_duration_s"] - 7.0) < (1.0 / 24) + 1e-4


def test_plan_cut_reuse_trim_continues_and_restarts_with_warning_when_needed() -> None:
    script = "Beat one. Beat two. Beat three."
    words = [
        {"word": "Beat", "start": 0.0, "end": 0.5},
        {"word": "one.", "start": 0.5, "end": 1.8},
        {"word": "Beat", "start": 2.2, "end": 2.6},
        {"word": "two.", "start": 2.6, "end": 3.8},
        {"word": "Beat", "start": 4.2, "end": 4.6},
        {"word": "three.", "start": 4.6, "end": 5.8},
    ]
    # All 3 beats (~2s each) use the same 4.5s clip:
    # - Beat 0: trim 0.0 .. 2.0s
    # - Beat 1: continues at trim 2.0 .. 4.0s
    # - Beat 2: remainder (0.5s) is too short for 2.0s slot -> restarts at 0.0s with "repeated footage" warning!
    segments = [
        {"anchor": "Beat one.", "clip": "shared.mp4"},
        {"anchor": "Beat two.", "clip": "shared.mp4"},
        {"anchor": "Beat three.", "clip": "shared.mp4"},
    ]
    plan = plan_cut(
        script_text=script,
        words=words,
        segments=segments,
        clip_durations={"shared.mp4": 4.5, "unused.mp4": 3.0},
        fps=24,
        audio_duration=6.0,
    )
    assert plan["scenes"][0]["clip_trim_start_s"] == pytest.approx(0.0, abs=1e-3)
    assert plan["scenes"][1]["clip_trim_start_s"] == pytest.approx(2.0, abs=1e-2)
    assert plan["scenes"][2]["clip_trim_start_s"] == pytest.approx(0.0, abs=1e-3)
    assert plan["reuse_count"] == 2
    assert plan["unused_clips"] == ["unused.mp4"]
    assert any("repeated footage" in w for w in plan["warnings"])


def test_plan_cut_detects_overrun_when_neighbours_cannot_absorb() -> None:
    script = "First part. Second part."
    words = [
        {"word": "First", "start": 0.0, "end": 0.5},
        {"word": "part.", "start": 0.5, "end": 3.0},
        {"word": "Second", "start": 3.2, "end": 4.0},
        {"word": "part.", "start": 4.0, "end": 6.0},
    ]
    segments = [
        {"anchor": "First part.", "clip": "short1.mp4"},
        {"anchor": "Second part.", "clip": "short2.mp4"},
    ]
    plan = plan_cut(
        script_text=script,
        words=words,
        segments=segments,
        clip_durations={"short1.mp4": 2.0, "short2.mp4": 2.0},
        fps=24,
        audio_duration=6.0,
        allow_hold=False,
    )
    assert len(plan["overruns"]) == 2
    assert all(ov["excess_s"] > 0.5 for ov in plan["overruns"])


# ---------------------------------------------------------------------------
# 3. Name inference & MAPPING NEEDED template generation + validation
# ---------------------------------------------------------------------------


def _fake_clips(names: list[str]) -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "stem": Path(name).stem,
            "path": f"/tmp/{name}",
            "duration_s": 4.0,
            "resolution": "720x1280",
            "width": 720,
            "height": 1280,
        }
        for name in names
    ]


def test_infer_mapping_from_inline_markers_strips_marker_text(tmp_path: Path) -> None:
    clips = _fake_clips(["Scene_01.mp4", "Scene_02.mp4", "Scene_03.mp4"])
    script = (
        "[Scene_01] Ancient builders mixed volcanic ash.\n"
        "[scene 2] Seawater crystallized the mortar.\n"
        "Scene 3: Modern piers still crumble in decades."
    )
    res = resolve_mapping(script_text=script, clips=clips, project_dir=tmp_path)
    assert res["source"] == "inferred_inline_markers"
    assert "[Scene_01]" not in res["clean_script"]
    assert "[scene 2]" not in res["clean_script"]
    assert "Scene 3:" not in res["clean_script"]
    assert [s["clip"] for s in res["segments"]] == ["Scene_01.mp4", "Scene_02.mp4", "Scene_03.mp4"]


def test_infer_mapping_from_paragraph_count(tmp_path: Path) -> None:
    clips = _fake_clips(["Scene_01.mp4", "Scene_02.mp4"])
    script = "First paragraph has two sentences. Here is the second.\n\nSecond paragraph closes the piece."
    res = resolve_mapping(script_text=script, clips=clips, project_dir=tmp_path)
    assert res["source"] == "inferred_paragraphs"
    assert res["inference_log"] == [
        "inferred: paragraph 1 -> Scene_01.mp4",
        "inferred: paragraph 2 -> Scene_02.mp4",
    ]


def test_infer_mapping_from_sentence_count(tmp_path: Path) -> None:
    clips = _fake_clips(["clip1.mp4", "clip2.mp4", "clip3.mp4"])
    script = "First sentence here. Second sentence follows. Third sentence finishes."
    res = resolve_mapping(script_text=script, clips=clips, project_dir=tmp_path)
    assert res["source"] == "inferred_sentences"
    assert res["inference_log"] == [
        "inferred: sentence 1 -> clip1.mp4",
        "inferred: sentence 2 -> clip2.mp4",
        "inferred: sentence 3 -> clip3.mp4",
    ]


def test_ambiguous_mapping_raises_mapping_needed_and_writes_template(tmp_path: Path) -> None:
    clips = _fake_clips(["Scene_01.mp4", "Scene_02.mp4", "Scene_03.mp4", "Scene_04.mp4"])
    script = "Only two sentences here. So four clips cannot be matched automatically."
    with pytest.raises(CutError) as exc_info:
        resolve_mapping(
            script_text=script,
            clips=clips,
            project_dir=tmp_path,
            script_path=tmp_path / "script.txt",
            clips_dir=tmp_path / "clips",
            out_path=tmp_path / "out.mp4",
        )
    err = exc_info.value
    assert err.exit_code == 2
    assert "MAPPING NEEDED" in str(err)
    report = err.report
    assert report["status"] == "mapping_needed"
    assert len(report["rules_tried"]) == 3

    template_file = tmp_path / "cut-map.template.yaml"
    assert template_file.is_file()
    tpl = yaml.safe_load(template_file.read_text(encoding="utf-8"))
    assert tpl["version"] == 1
    assert len(tpl["segments"]) == 2
    assert tpl["segments"][0]["clip"] == ""
    assert tpl["segments"][0]["anchor"].startswith("Only two")


def test_map_validation_reports_all_errors_at_once(tmp_path: Path) -> None:
    clips = _fake_clips(["Scene_01.mp4", "Scene_02.mp4"])
    script = "Alpha sentence starts here. Beta sentence is second. Gamma sentence is third."
    bad_map = tmp_path / "bad-map.yaml"
    bad_map.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "segments": [
                    {"anchor": "Beta sentence", "clip": "Scene_01.mp4"},
                    # 1) Non-monotonic anchor (Alpha is before Beta) + 2) Unknown clip
                    {"anchor": "Alpha sentence", "clip": "Missing_99.mp4"},
                    # 3) Anchor not found at all (should suggest closest candidate sentences)
                    {"anchor": "Completely nonexistent phrase", "clip": "Scene_02.mp4"},
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(CutError) as exc_info:
        resolve_mapping(
            script_text=script,
            clips=clips,
            project_dir=tmp_path,
            map_path=bad_map,
        )
    err = exc_info.value
    assert err.exit_code == 2
    text = str(err)
    # All 3 failures must be reported together in a single run
    assert "unknown clip 'Missing_99.mp4'" in text
    assert "non-monotonic anchor 'Alpha sentence'" in text
    assert "anchor 'Completely nonexistent phrase' not found" in text
    assert "Closest candidate sentences:" in text


# ---------------------------------------------------------------------------
# 4. Two-tier idempotent voiceover cache (mocked TTS call count: 1 -> 0 -> 0)
# ---------------------------------------------------------------------------


def test_voiceover_cache_reuses_processed_and_raw_across_speed_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import parallax.voiceover as vo_mod

    tts_call_count = 0

    def _mock_generate_voiceover_dict(**kwargs: Any) -> dict[str, Any]:
        nonlocal tts_call_count
        tts_call_count += 1
        out_dir = Path(kwargs["out_dir"])
        raw_mp3 = out_dir / "raw_synth.mp3"
        raw_mp3.write_bytes(b"FAKE_MP3_BYTES")
        return {
            "audio_path": str(raw_mp3),
            "raw_audio_path": str(raw_mp3),
            "words": [
                {"word": "Ancient", "start": 0.10, "end": 0.50},
                {"word": "concrete", "start": 0.95, "end": 1.45},  # 0.45s gap (>0.30s) -> trimmed to 0.25s
                {"word": "endures.", "start": 1.50, "end": 2.00},
            ],
            "total_duration_s": 2.10,
        }

    monkeypatch.setattr(vo_mod, "generate_voiceover_dict", _mock_generate_voiceover_dict)
    script = "Ancient concrete endures."
    proj = tmp_path / "vo_proj"

    # 1st run: cache miss -> calls TTS once
    r1 = resolve_or_generate_voiceover(
        clean_script=script,
        project_dir=proj,
        speed=1.15,
        max_sentence_silence=0.25,
        quiet=True,
    )
    assert tts_call_count == 1
    assert r1["source"] == "tts"
    assert (proj / "voiceover.provider-raw.mp3").is_file()

    # 2nd run (identical settings): processed cache hit -> 0 new TTS calls
    r2 = resolve_or_generate_voiceover(
        clean_script=script,
        project_dir=proj,
        speed=1.15,
        max_sentence_silence=0.25,
        quiet=True,
    )
    assert tts_call_count == 1
    assert r2["source"] == "cached"
    assert r2["cache_tier"] == "processed"
    assert r2["cost_usd"] == 0.0

    # 3rd run (speed changed 1.15 -> 1.30): raw cache hit -> re-processes locally with 0 new TTS calls!
    r3 = resolve_or_generate_voiceover(
        clean_script=script,
        project_dir=proj,
        speed=1.30,
        max_sentence_silence=0.25,
        quiet=True,
    )
    assert tts_call_count == 1
    assert r3["source"] == "cached"
    assert r3["cache_tier"] == "raw"
    assert r3["cost_usd"] == 0.0
    assert r3["words"][-1]["end"] < r2["words"][-1]["end"]


# ---------------------------------------------------------------------------
# 5. Manifest round-trip and manifest==xml consistency check
# ---------------------------------------------------------------------------


def test_manifest_round_trip_and_xml_consistency_check(tmp_path: Path) -> None:
    clips = _fake_clips(["Scene_01.mp4", "Scene_02.mp4"])
    plan = {
        "scenes": [
            {
                "index": 0,
                "clip": "Scene_01.mp4",
                "start_s": 0.0,
                "end_s": 1.5,
                "duration_s": 1.5,
                "clip_trim_start_s": 0.0,
                "vo_text": "First scene.",
            },
            {
                "index": 1,
                "clip": "Scene_02.mp4",
                "start_s": 1.5,
                "end_s": 3.0,
                "duration_s": 1.5,
                "clip_trim_start_s": 0.25,
                "vo_text": "Second scene.",
            },
        ]
    }
    manifest_dict = build_manifest_dict(
        plan=plan,
        clips=clips,
        project_dir=tmp_path,
        audio_path=tmp_path / "voiceover.mp3",
        words_path=tmp_path / "words.json",
        resolution="720x1280",
        fps=24,
        words_per_chunk=3,
        cut_meta={"speed": 1.15},
    )
    mpath = write_cut_manifest(manifest_dict, tmp_path / "manifest.yaml")
    loaded = yaml.safe_load(mpath.read_text(encoding="utf-8"))
    assert loaded["version"] == 1
    assert loaded["fps"] == 24
    assert len(loaded["scenes"]) == 2

    # Build matching xmeml timeline.xml (36 frames + 36 frames @ 24fps)
    xml_path = tmp_path / "timeline.xml"
    xml_path.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE xmeml>
<xmeml version="4">
  <sequence>
    <rate><timebase>24</timebase><ntsc>FALSE</ntsc></rate>
    <media>
      <video>
        <track>
          <clipitem id="clipitem-1"><start>0</start><end>36</end><in>0</in><out>36</out></clipitem>
          <clipitem id="clipitem-2"><start>36</start><end>72</end><in>6</in><out>42</out></clipitem>
        </track>
      </video>
    </media>
  </sequence>
</xmeml>""",
        encoding="utf-8",
    )
    ok, probs = check_manifest_xml_consistency(mpath, xml_path)
    assert ok is True
    assert probs == []

    # Perturb XML duration and verify mismatch is caught
    bad_xml = tmp_path / "bad_timeline.xml"
    bad_xml.write_text(xml_path.read_text(encoding="utf-8").replace("<end>72</end>", "<end>60</end>"), encoding="utf-8")
    ok_bad, probs_bad = check_manifest_xml_consistency(mpath, bad_xml)
    assert ok_bad is False
    assert any("Scene 1" in p for p in probs_bad)
