"""Voiceover verification sources: supplied words.json (no ASR) vs whisper with coverage classification."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from parallax.cut import (
    CutError,
    _speech_coverage,
    resolve_or_generate_voiceover,
    verify_audio_against_script,
    verify_words_against_script,
)
from parallax.voiceover import VoiceSynthesisResult

SCRIPT = (
    "Here is what a lip filler really does. Filler is a gel injected into delicate tissue. "
    "It draws in water and plumps the lip. Results fade over months as the gel breaks down."
)


def _tone(path: Path, seconds: float) -> Path:
    """Real audio of a known duration (ffprobe-able), so coverage checks have a duration to compare."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"sine=frequency=330:sample_rate=16000:duration={seconds}",
            str(path),
        ],
        check=True,
    )
    return path


def _timed(text: str, start: float, end: float) -> list[dict]:
    toks = text.split()
    step = (end - start) / len(toks)
    return [
        {"word": w, "start": round(start + i * step, 3), "end": round(start + (i + 0.8) * step, 3)}
        for i, w in enumerate(toks)
    ]


def _words_file(tmp_path: Path, words: list[dict], **extra) -> Path:
    p = tmp_path / "words.json"
    p.write_text(json.dumps({"words": words, **extra}), encoding="utf-8")
    return p


def _no_whisper(audio_path: str, label: str = "") -> list[dict]:
    raise AssertionError(f"whisper must not be called on the words.json path ({label})")


# --- words.json path ----------------------------------------------------------------------------


@pytest.mark.parametrize("process_audio", [False, True])
def test_matching_words_json_passes_without_whisper(tmp_path: Path, process_audio: bool) -> None:
    audio = _tone(tmp_path / "vo.wav", 20.0)
    words = _words_file(tmp_path, _timed(SCRIPT, 0.3, 19.6))
    res = resolve_or_generate_voiceover(
        clean_script=SCRIPT,
        project_dir=tmp_path / "proj",
        audio_path=audio,
        words_path=words,
        process_audio=process_audio,
        transcriber=_no_whisper,
        strict_vo=True,
        quiet=True,
    )
    vf = res["verification"]
    assert vf["passed"] is True
    assert vf["status"] == "passed"
    assert vf["source"] == "words_json"
    assert vf["final"]["coverage"]["complete"] is True
    on_disk = json.loads((tmp_path / "proj" / "voiceover.verify.json").read_text(encoding="utf-8"))
    assert on_disk["source"] == "words_json"


def test_words_json_missing_sentence_fails_strict_with_vo_verify_failed(tmp_path: Path) -> None:
    audio = _tone(tmp_path / "vo.wav", 20.0)
    dropped = SCRIPT.replace("It draws in water and plumps the lip. ", "")
    words = _words_file(tmp_path, _timed(dropped, 0.3, 19.6))
    with pytest.raises(CutError) as exc_info:
        resolve_or_generate_voiceover(
            clean_script=SCRIPT,
            project_dir=tmp_path / "proj",
            audio_path=audio,
            words_path=words,
            transcriber=_no_whisper,
            strict_vo=True,
            quiet=True,
        )
    rep = exc_info.value.report
    assert exc_info.value.exit_code == 1
    assert rep["status"] == "vo_verify_failed"
    assert rep["reason"] == "script_mismatch"
    assert rep["source"] == "words_json"
    msg = str(exc_info.value)
    assert "words_json" in msg and "it draws in water and plumps the lip" in msg

    # Non-strict renders on, but the report still records the mismatch.
    res = resolve_or_generate_voiceover(
        clean_script=SCRIPT,
        project_dir=tmp_path / "proj2",
        audio_path=audio,
        words_path=words,
        transcriber=_no_whisper,
        strict_vo=False,
        quiet=True,
    )
    assert res["verification"]["passed"] is False
    assert res["verification"]["reason"] == "script_mismatch"


@pytest.mark.parametrize(
    "mutate, expect",
    [
        (lambda w: w.__setitem__(5, {**w[5], "start": w[2]["start"]}), "before the previous word"),
        (lambda w: w.__setitem__(5, {**w[5], "end": w[5]["start"] - 0.1}), "ends before it starts"),
        (lambda w: w.__setitem__(-1, {**w[-1], "end": 25.0}), "beyond the 20.000s audio"),
    ],
    ids=["non_monotonic", "end_before_start", "beyond_duration"],
)
def test_words_json_timing_problems_fail_clearly(tmp_path: Path, mutate, expect: str) -> None:
    audio = _tone(tmp_path / "vo.wav", 20.0)
    timed = _timed(SCRIPT, 0.3, 19.6)
    mutate(timed)
    words = _words_file(tmp_path, timed)
    with pytest.raises(CutError) as exc_info:
        resolve_or_generate_voiceover(
            clean_script=SCRIPT,
            project_dir=tmp_path / "proj",
            audio_path=audio,
            words_path=words,
            transcriber=_no_whisper,
            strict_vo=True,
            quiet=True,
        )
    rep = exc_info.value.report
    assert rep["status"] == "vo_verify_failed"
    assert rep["reason"] == "words_timing_invalid"
    assert rep["source"] == "words_json"
    assert expect in str(exc_info.value)


def test_words_json_coverage_ending_early_fails(tmp_path: Path) -> None:
    audio = _tone(tmp_path / "vo.wav", 20.0)
    words = _words_file(tmp_path, _timed(SCRIPT, 0.3, 12.0))  # 8 s of audio after the last word
    with pytest.raises(CutError) as exc_info:
        resolve_or_generate_voiceover(
            clean_script=SCRIPT,
            project_dir=tmp_path / "proj",
            audio_path=audio,
            words_path=words,
            transcriber=_no_whisper,
            strict_vo=True,
            quiet=True,
        )
    assert exc_info.value.report["reason"] == "words_timing_invalid"
    assert "uncovered" in str(exc_info.value)


def test_estimated_words_json_is_not_trusted(tmp_path: Path) -> None:
    """A words.json that marks its own timings as estimates falls back to whisper."""
    audio = _tone(tmp_path / "vo.wav", 20.0)
    words = _words_file(tmp_path, _timed(SCRIPT, 0.3, 19.6), estimated=True)
    calls: list[str] = []

    def _whisper(audio_path: str, label: str = "") -> list[dict]:
        calls.append(label)
        return _timed(SCRIPT, 0.3, 19.6)

    res = resolve_or_generate_voiceover(
        clean_script=SCRIPT,
        project_dir=tmp_path / "proj",
        audio_path=audio,
        words_path=words,
        transcriber=_whisper,
        quiet=True,
    )
    assert calls == ["supplied audio"]
    assert res["verification"]["source"] == "whisper"
    assert res["verification"]["passed"] is True


def test_coverage_constants() -> None:
    # 20 s: edge tolerance 1.5 s (floor), gap tolerance 5 s (floor); 100 s: 5 s and 10 s.
    assert _speech_coverage([{"start": 1.4, "end": 18.6}], 20.0)["complete"] is True
    assert _speech_coverage([{"start": 0.2, "end": 18.4}], 20.0)["complete"] is False
    assert _speech_coverage([{"start": 0.2, "end": 95.5}], 100.0)["complete"] is True
    gap = [{"start": 0.2, "end": 40.0}, {"start": 51.0, "end": 99.0}]
    assert _speech_coverage(gap, 100.0)["complete"] is False
    assert verify_words_against_script(_timed(SCRIPT, 0.3, 19.6), SCRIPT, audio_duration_s=20.0)["passed"]


# --- whisper path -------------------------------------------------------------------------------


def _partial_then(window_words) -> tuple[list[str], object]:
    """Whole-file transcription only hears the first quarter of the audio (the PA9 4-segment flake)."""
    labels: list[str] = []
    head = " ".join(SCRIPT.split()[:9])

    def _fn(audio_path: str, label: str = "") -> list[dict]:
        labels.append(label)
        if "window" in label:
            return window_words(audio_path, label)
        return _timed(head, 0.3, 5.0)

    return labels, _fn


def test_partial_whisper_transcript_is_retried_and_classified_incomplete(tmp_path: Path) -> None:
    audio = _tone(tmp_path / "vo.wav", 60.0)
    labels, fn = _partial_then(lambda path, label: [])
    rep = verify_audio_against_script(audio, SCRIPT, transcriber=fn, label="supplied audio")
    assert rep["passed"] is False
    assert rep["status"] == "transcription_incomplete"
    assert rep["reason"] == "transcription_incomplete"
    assert rep["source"] == "whisper"
    assert [a["mode"] for a in rep["asr_attempts"]] == ["full", "windows"]
    assert labels[:2] == ["supplied audio", "supplied audio"]
    assert sum("window" in lab for lab in labels) == 3  # 60 s / 25 s windows
    assert "of 60.00s audio" in rep["note"]


def test_partial_whisper_transcript_recovers_via_windows(tmp_path: Path) -> None:
    audio = _tone(tmp_path / "vo.wav", 60.0)
    full = _timed(SCRIPT, 0.5, 59.0)

    def _window(path: str, label: str) -> list[dict]:
        k = int(label.split("window ")[1].split("/")[0]) - 1
        clip_start = max(0.0, k * 25.0 - 1.0)
        return [
            {**w, "start": w["start"] - clip_start, "end": w["end"] - clip_start}
            for w in full
            if clip_start <= w["start"] < clip_start + 27.0
        ]

    _, fn = _partial_then(_window)
    rep = verify_audio_against_script(audio, SCRIPT, transcriber=fn, label="supplied audio")
    assert rep["passed"] is True, rep.get("missing_runs")
    assert rep["asr_mode"] == "windows"
    assert rep["coverage"]["complete"] is True


def test_supplied_audio_without_words_reports_vo_transcription_incomplete(tmp_path: Path, monkeypatch) -> None:
    import parallax.forced_align as forced_align

    audio = _tone(tmp_path / "vo.wav", 60.0)
    monkeypatch.setattr(forced_align, "align_words", lambda a, s: _timed(SCRIPT, 0.5, 59.0))
    _, fn = _partial_then(lambda path, label: [])
    with pytest.raises(CutError) as exc_info:
        resolve_or_generate_voiceover(
            clean_script=SCRIPT,
            project_dir=tmp_path / "proj",
            audio_path=audio,
            transcriber=fn,
            strict_vo=True,
            quiet=True,
        )
    rep = exc_info.value.report
    assert rep["status"] == "vo_transcription_incomplete"
    assert rep["source"] == "whisper"
    msg = str(exc_info.value)
    assert "INCOMPLETE" in msg and "not a script mismatch" in msg and "60.00s audio" in msg


def test_genuine_whisper_mismatch_with_full_coverage_is_still_vo_verify_failed(tmp_path: Path, monkeypatch) -> None:
    import parallax.forced_align as forced_align

    audio = _tone(tmp_path / "vo.wav", 20.0)
    monkeypatch.setattr(forced_align, "align_words", lambda a, s: _timed(SCRIPT, 0.3, 19.6))
    dropped = SCRIPT.replace("It draws in water and plumps the lip. ", "")
    calls: list[str] = []

    def _fn(audio_path: str, label: str = "") -> list[dict]:
        calls.append(label)
        return _timed(dropped, 0.3, 19.6)

    with pytest.raises(CutError) as exc_info:
        resolve_or_generate_voiceover(
            clean_script=SCRIPT,
            project_dir=tmp_path / "proj",
            audio_path=audio,
            transcriber=_fn,
            strict_vo=True,
            quiet=True,
        )
    assert exc_info.value.report["status"] == "vo_verify_failed"
    assert exc_info.value.report["reason"] == "script_mismatch"
    assert calls == ["supplied audio"]  # full coverage: no ASR retries


def test_tts_chunk_transcription_gap_does_not_regenerate_tts(tmp_path: Path) -> None:
    """A partial transcript of a synthesized take blocks strict mode but never pays for a regeneration."""
    synth_calls: list[str] = []

    class _Provider:
        def synthesize(self, text, voice="nova", style=None, *, out_dir=None) -> VoiceSynthesisResult:
            synth_calls.append(text)
            mp3 = _tone(Path(out_dir or tmp_path) / f"take{len(synth_calls)}.wav", 60.0)
            return VoiceSynthesisResult(
                audio_path=str(mp3), words=_timed(text, 0.5, 59.0), duration_s=60.0, cost_usd=0.01
            )

    _, fn = _partial_then(lambda path, label: [])
    with pytest.raises(CutError) as exc_info:
        resolve_or_generate_voiceover(
            voice="nova",
            voice_model="tts-mini",
            clean_script=SCRIPT,
            project_dir=tmp_path / "proj",
            vo_chunk_seconds=0,
            vo_verify=True,
            vo_retries=2,
            vo_normalize=False,
            max_sentence_silence=10.0,
            speed=1.0,
            strict_vo=True,
            provider=_Provider(),
            transcriber=fn,
            quiet=True,
        )
    assert len(synth_calls) == 1
    rep = exc_info.value.report
    assert rep["status"] == "vo_transcription_incomplete"
    assert rep["tts_calls"] == 1
    assert "no TTS was regenerated" in str(exc_info.value)
    # The paid raw take survives for a free re-verify on the next run.
    assert list((tmp_path / "proj" / "cache" / "vo" / "chunks").glob("*.raw.mp3"))
