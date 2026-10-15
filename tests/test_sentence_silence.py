"""Acoustic sentence-gap editing must retain audible material and word identity."""

import json
import math
import wave
from array import array

import pytest

from parallax import cli
from parallax.audio import cap_sentence_silence, detect_silences


def _source(path):
    rate = 44100
    samples = array("h")
    for i in range(round(2.5 * rate)):
        t = i / rate
        # Distinct voiced islands: first word, an audible breath between two
        # quiet plateaus, then two words separated by a non-sentence gap.
        loud = 0.24 if (0.08 <= t < 0.35 or 1.05 <= t < 1.35 or 2.0 <= t < 2.3) else 0
        breath = 0.035 if 0.67 <= t < 0.74 else 0
        samples.append(round(32767 * (loud + breath) * math.sin(2 * math.pi * 220 * t)))
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(samples.tobytes())
    return samples


def _samples(path):
    with wave.open(str(path), "rb") as stream:
        data = array("h")
        data.frombytes(stream.readframes(stream.getnframes()))
        return data


def test_caps_only_acoustic_quiet_at_sentence_boundary(tmp_path):
    src, dst = tmp_path / "source.wav", tmp_path / "edited.wav"
    before = _source(src)
    words = [
        {"word": "First.", "start": 0.08, "end": 0.35},
        {"word": "Next", "start": 1.05, "end": 1.35},
        {"word": "Third.", "start": 2.0, "end": 2.3},
    ]
    result = cap_sentence_silence(str(src), str(dst), words)
    after = _samples(dst)

    assert result["sentence_boundaries"] == 1
    assert result["quiet_segments_capped"] == 2  # active breath splits silence
    assert result["seconds_removed"] > 0.40
    assert [w["word"] for w in result["adjusted_words"]] == [w["word"] for w in words]
    assert result["adjusted_words"][1]["start"] == pytest.approx(
        words[1]["start"] - result["seconds_removed"], abs=0.002,
    )
    # No cut reaches the first phoneme or the intervening breath.
    assert after[:round(0.35 * 44100)] == before[:round(0.35 * 44100)]
    breath_start = 0.67 - (result["edits"][0]["quiet_before_s"] - 0.075)
    breath_samples = after[round(breath_start * 44100):round((breath_start + 0.07) * 44100)]
    assert max(map(abs, breath_samples)) > 900
    # The untouched intra-sentence gap remains long; each edited sentence
    # quiet plateau is at most about 75 ms (detector quantization tolerance).
    quiet_after = detect_silences(str(dst), noise_db=-40, min_silence_s=0.04)
    sentence_region_end = result["adjusted_words"][1]["start"]
    assert all(q["duration"] <= 0.09 for q in quiet_after if q["start"] < sentence_region_end)
    assert any(q["duration"] > 0.4 for q in quiet_after if q["start"] > sentence_region_end)


def test_sentence_silence_cli_writes_timing_and_report(tmp_path):
    src, dst = tmp_path / "source.wav", tmp_path / "edited.wav"
    _source(src)
    words = tmp_path / "canonical.json"
    words.write_text(json.dumps({"words": [
        {"word": "First.", "start": 0.08, "end": 0.35},
        {"word": "Next", "start": 1.05, "end": 1.35},
    ]}))
    words_out, report = tmp_path / "adjusted.json", tmp_path / "report.json"
    assert cli.main(["audio", "cap-sentence-silence", "--input", str(src),
                     "--output", str(dst), "--words", str(words),
                     "--words-out", str(words_out), "--report", str(report)]) == 0
    result = json.loads(report.read_text())
    adjusted = json.loads(words_out.read_text())
    assert result["quiet_segments_capped"] == 2
    assert len(result["edits"]) == 2
    assert adjusted["words"][0]["word"] == "First."
    assert adjusted["total_duration_s"] == result["new_duration_s"]
    assert dst.is_file()


def test_sentence_silence_refuses_output_collision_before_edit(tmp_path):
    src = tmp_path / "source.wav"
    _source(src)
    words = tmp_path / "words.json"
    words.write_text(json.dumps({"words": [{"word": "Hello.", "start": 0.08, "end": 0.35}]}))
    assert cli.main(["audio", "cap-sentence-silence", "--input", str(src),
                     "--output", str(src), "--words", str(words),
                     "--words-out", str(tmp_path / "out.json")]) != 0
    assert src.stat().st_size > 0


def test_caps_eighty_ms_quiet_and_ignores_abbreviations(tmp_path):
    src, dst = tmp_path / "short.wav", tmp_path / "short-edited.wav"
    rate = 44100
    samples = array("h", (
        round(32767 * 0.2 * math.sin(2 * math.pi * 220 * i / rate))
        if not (0.4 <= i / rate < 0.48) else 0
        for i in range(rate)
    ))
    with wave.open(str(src), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(samples.tobytes())
    words = [
        {"word": "Dr.", "start": 0.1, "end": 0.35},
        {"word": "Smith", "start": 0.55, "end": 0.8},
    ]
    unchanged = cap_sentence_silence(str(src), str(dst), words)
    assert unchanged["quiet_segments_capped"] == 0
    words[0]["word"] = "Stop."
    capped = cap_sentence_silence(str(src), str(dst), words)
    assert capped["quiet_segments_capped"] == 1
    assert capped["edits"][0]["verified_quiet_after_s"] <= 0.077
    assert capped["edits"][0]["crossfade_s"] < 0.010


def test_rejects_word_timing_inside_long_quiet_before_writing(tmp_path):
    src, dst = tmp_path / "source.wav", tmp_path / "output.wav"
    _source(src)
    words = [
        {"word": "First.", "start": 0.08, "end": 0.55},
        {"word": "Next", "start": 1.05, "end": 1.35},
    ]
    with pytest.raises(ValueError, match="overlaps word timing"):
        cap_sentence_silence(str(src), str(dst), words)
    assert not dst.exists()


@pytest.mark.parametrize("bad", [float("nan"), -0.1, 1.0])
def test_rejects_invalid_word_timing(tmp_path, bad):
    src, dst = tmp_path / "source.wav", tmp_path / "output.wav"
    _source(src)
    with pytest.raises(ValueError, match="finite nonnegative"):
        cap_sentence_silence(str(src), str(dst), [{"word": "Hello.", "start": bad, "end": 0.35}])


def test_rejects_nonfinite_config_before_edit(tmp_path):
    src, dst = tmp_path / "source.wav", tmp_path / "output.wav"
    _source(src)
    with pytest.raises(ValueError, match="numeric settings must be finite"):
        cap_sentence_silence(
            str(src), str(dst), [{"word": "Hello.", "start": 0.08, "end": 0.35}],
            noise_db=float("nan"),
        )
    assert not dst.exists()
