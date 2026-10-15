"""Opening silence edits must hit a word deadline without touching its onset."""

import json
import math
import wave
from array import array

import pytest

from parallax import cli
from parallax.audio import trim_leading_quiet


def _fixture(path, first_sound=0.28):
    rate = 44100
    samples = array("h")
    for i in range(2 * rate):
        t = i / rate
        voiced = first_sound <= t < 0.55 or 1.15 <= t < 1.45
        samples.append(round(32767 * 0.2 * math.sin(2 * math.pi * 220 * t)) if voiced else 0)
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(samples.tobytes())
    return samples


def _read(path):
    with wave.open(str(path), "rb") as stream:
        samples = array("h")
        samples.frombytes(stream.readframes(stream.getnframes()))
        return samples


def _words():
    return [
        {"word": "Hello.", "start": 0.35, "end": 0.50},
        {"word": "World", "start": 1.20, "end": 1.40},
    ]


def test_trims_only_verified_quiet_and_shifts_words(tmp_path):
    src, dst = tmp_path / "source.wav", tmp_path / "edited.wav"
    before = _fixture(src)
    result = trim_leading_quiet(str(src), str(dst), _words())
    after = _read(dst)
    assert result["seconds_removed"] == pytest.approx(0.25, abs=0.0001)
    assert result["first_word_after_s"] == pytest.approx(0.100, abs=0.0001)
    assert result["leading_quiet_before_s"] == pytest.approx(0.28, abs=0.001)
    assert result["leading_quiet_after_s"] == pytest.approx(0.03, abs=0.001)
    assert [w["word"] for w in result["adjusted_words"]] == ["Hello.", "World"]
    # A sample-aligned trim leaves voiced samples byte-identical. Fade occurs
    # in the first 5 ms of retained quiet, before any speech.
    assert after[round(0.03 * 44100):round(0.25 * 44100)] == before[
        round(0.28 * 44100):round(0.50 * 44100)
    ]
    assert _read(src) == before


def test_refuses_deadline_when_acoustic_quiet_is_insufficient(tmp_path):
    src, dst = tmp_path / "source.wav", tmp_path / "edited.wav"
    _fixture(src, first_sound=0.15)
    with pytest.raises(ValueError, match="verified leading quiet"):
        trim_leading_quiet(str(src), str(dst), _words())
    assert not dst.exists()


def test_already_early_word_is_noop_even_without_leading_quiet(tmp_path):
    src, dst = tmp_path / "source.wav", tmp_path / "edited.wav"
    before = _fixture(src, first_sound=0)
    words = [{"word": "Hello.", "start": 0.04, "end": 0.20}]
    result = trim_leading_quiet(str(src), str(dst), words)
    assert result["seconds_removed"] == 0
    assert result["first_word_after_s"] == 0.04
    assert result["adjusted_words"] == words
    assert _read(dst) == before


def test_cli_writes_words_report_and_combines_sentence_cap(tmp_path):
    src = tmp_path / "source.wav"
    _fixture(src)
    words = tmp_path / "words.json"
    words.write_text(json.dumps({"words": _words()}))
    dst, adjusted, report = (tmp_path / "combined.wav", tmp_path / "combined.json", tmp_path / "report.json")
    assert cli.main([
        "audio", "cap-sentence-silence", "--input", str(src), "--output", str(dst),
        "--words", str(words), "--words-out", str(adjusted), "--report", str(report),
        "--first-word-by", "0.100",
    ]) == 0
    data = json.loads(report.read_text())
    assert data["quiet_segments_capped"] == 1
    assert data["opening_trim"]["first_word_after_s"] == pytest.approx(0.1, abs=0.001)
    assert data["opening_trim"]["leading_quiet_after_s"] >= 0.02
    assert data["verified_quiet_coordinate_system"] == "final output audio"
    assert data["edits"][0]["verified_quiet_start_s"] == pytest.approx(
        data["edits"][0]["sentence_stage_verified_quiet_start_s"]
        - data["opening_trim"]["seconds_removed"], abs=0.0001,
    )
    assert json.loads(adjusted.read_text())["words"][0]["start"] == pytest.approx(0.1, abs=0.001)
    assert dst.is_file()


def test_standalone_cli_rejects_source_output_collision(tmp_path):
    src = tmp_path / "source.wav"
    _fixture(src)
    words = tmp_path / "words.json"
    words.write_text(json.dumps({"words": _words()}))
    assert cli.main([
        "audio", "trim-leading-quiet", "--input", str(src), "--output", str(src),
        "--words", str(words), "--words-out", str(tmp_path / "output.json"),
    ]) != 0
    assert src.is_file()
