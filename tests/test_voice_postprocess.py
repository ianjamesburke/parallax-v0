"""Tests for voice_postprocess block — schema, cap_pauses adjusted_words, and preflight warnings."""

import pytest
import yaml


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------

def test_voice_postprocess_defaults():
    from parallax.plan import VoicePostprocess
    vp = VoicePostprocess()
    assert vp.cap_pauses is False
    assert vp.max_gap_s == 0.5
    assert vp.speed == 1.0


def test_voice_postprocess_extra_field_raises():
    from parallax.plan import VoicePostprocess
    with pytest.raises(Exception):
        VoicePostprocess.model_validate({"cap_pauses": True, "unknown_field": "x"})


def test_voice_postprocess_schema_full():
    from parallax.plan import Plan
    data = yaml.safe_load("""
aspect: "9:16"
voice_postprocess:
  cap_pauses: true
  max_gap_s: 0.4
  speed: 1.15
scenes:
  - index: 0
    vo_text: "hello"
    prompt: "test"
""")
    p = Plan.model_validate(data)
    assert p.voice_postprocess is not None
    assert p.voice_postprocess.cap_pauses is True
    assert p.voice_postprocess.max_gap_s == 0.4
    assert p.voice_postprocess.speed == 1.15


def test_voice_postprocess_none_by_default():
    from parallax.plan import Plan
    data = yaml.safe_load("""
aspect: "9:16"
scenes:
  - index: 0
    vo_text: "hello"
    prompt: "test"
""")
    p = Plan.model_validate(data)
    assert p.voice_postprocess is None


# ---------------------------------------------------------------------------
# cap_pauses returns adjusted_words
# ---------------------------------------------------------------------------

def test_cap_pauses_returns_adjusted_words_no_cuts(tmp_path):
    """When no gaps exceed max_gap_s, adjusted_words equals the input words."""
    import subprocess
    from parallax.audio import cap_pauses

    src = tmp_path / "src.wav"
    dst = tmp_path / "dst.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "anullsrc=cl=mono:r=44100",
         "-t", "1.0", "-c:a", "pcm_s16le", str(src)],
        check=True, capture_output=True,
    )
    # Tight words — no gap exceeds 0.5s
    words = [
        {"word": "hello", "start": 0.0, "end": 0.3},
        {"word": "world", "start": 0.4, "end": 0.7},
    ]
    result = cap_pauses(str(src), str(dst), max_gap_s=0.5, words=words)
    assert "adjusted_words" in result
    assert result["adjusted_words"] == words  # no adjustment needed


def test_cap_pauses_returns_adjusted_words_with_cuts(tmp_path):
    """Gaps > max_gap_s → adjusted_words have shifted timestamps."""
    import subprocess
    from parallax.audio import cap_pauses

    src = tmp_path / "src.wav"
    dst = tmp_path / "dst.wav"
    # 3s of silence
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "anullsrc=cl=mono:r=44100",
         "-t", "3.0", "-c:a", "pcm_s16le", str(src)],
        check=True, capture_output=True,
    )
    # Big gap between words (1.5s gap >> 0.5s max_gap_s)
    words = [
        {"word": "hello", "start": 0.0, "end": 0.3},
        {"word": "world", "start": 1.8, "end": 2.1},
    ]
    result = cap_pauses(str(src), str(dst), max_gap_s=0.5, words=words)
    assert "adjusted_words" in result
    adj = result["adjusted_words"]
    assert len(adj) == 2
    assert adj[0]["word"] == "hello"
    assert adj[1]["word"] == "world"
    # "world" was at 1.8s; gap was 1.5s, trimmed to 0.5s → world now at ~0.8s
    assert adj[1]["start"] < words[1]["start"]


# ---------------------------------------------------------------------------
# Preflight warnings
# ---------------------------------------------------------------------------

def test_preflight_warns_voice_postprocess_when_locked():
    from parallax.preflight import compute_preflight

    plan = {
        "image_model": "mid",
        "video_model": "mid",
        "voice_model": "tts-mini",
        "audio_path": "parallax/audio/vo.wav",
        "voice_postprocess": {"cap_pauses": True, "max_gap_s": 0.5, "speed": 1.0},
        "scenes": [{"index": 0, "still_path": "a.png"}],
    }
    result = compute_preflight(plan)
    assert any("voice_postprocess" in w and "locked" in w for w in result.warnings)


def test_preflight_warns_speed_conflict():
    from parallax.preflight import compute_preflight

    plan = {
        "image_model": "mid",
        "video_model": "mid",
        "voice_model": "tts-mini",
        "voice_speed": 1.2,
        "voice_postprocess": {"cap_pauses": False, "max_gap_s": 0.5, "speed": 1.5},
        "scenes": [{"index": 0}],
    }
    result = compute_preflight(plan)
    assert any("voice_postprocess.speed" in w and "voice_speed" in w for w in result.warnings)


def test_preflight_no_conflict_when_speeds_match():
    from parallax.preflight import compute_preflight

    plan = {
        "image_model": "mid",
        "video_model": "mid",
        "voice_model": "tts-mini",
        "voice_speed": 1.15,
        "voice_postprocess": {"cap_pauses": False, "max_gap_s": 0.5, "speed": 1.15},
        "scenes": [{"index": 0}],
    }
    result = compute_preflight(plan)
    # No conflict — same value
    assert not any("voice_postprocess.speed" in w and "voice_speed" in w for w in result.warnings)
