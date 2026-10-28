"""`parallax cut` voice defaults and fail-closed voiceover verification (PR #209 fresh-agent findings)."""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from parallax import cli
from parallax.cut import CutError, resolve_or_generate_voiceover, run_cut
from parallax.voiceover import VoiceSynthesisResult
from tests.test_cut_integration import _make_sine_vo, _make_test_clip

SCRIPT = "Roman harbour walls rose from the bay. Seawater locked limestone into stone."


def test_cut_defaults_to_tts_gemini_charon_and_strict_verify(monkeypatch, tmp_path: Path) -> None:
    captured: dict = {}

    def _fake_run_cut(**kwargs):
        captured.update(kwargs)
        return {"status": "ok", "out_path": str(tmp_path / "o.mp4")}

    monkeypatch.setattr("parallax.cut.run_cut", _fake_run_cut)
    script = tmp_path / "s.txt"
    script.write_text("Hello there.", encoding="utf-8")
    argv = ["cut", "--script", str(script), "--clips", str(tmp_path), "--out", str(tmp_path / "o.mp4")]

    cli.main(argv)
    assert captured["voice_model"] == "tts-gemini"
    assert captured["voice"] == "Charon"
    assert captured["strict_vo"] is True

    cli.main([*argv, "--voice-model", "tts-mini"])
    assert captured["voice_model"] == "tts-mini" and captured["voice"] == "nova"

    cli.main([*argv, "--voice", "Kore", "--no-strict-vo"])
    assert captured["voice"] == "Kore" and captured["strict_vo"] is False

    for fn in (run_cut, resolve_or_generate_voiceover):
        params = inspect.signature(fn).parameters
        assert params["voice_model"].default == "tts-gemini"
        assert params["voice"].default == "Charon"
        assert params["strict_vo"].default is True


class _SineProvider:
    def __init__(self, tmp: Path) -> None:
        self.calls = 0
        self.tmp = tmp

    def synthesize(self, text, voice="Charon", style=None, *, out_dir=None):
        self.calls += 1
        audio = _make_sine_vo(Path(out_dir or self.tmp) / f"take{self.calls}.wav", duration_s=4.0)
        toks = text.split()
        step = 3.5 / max(1, len(toks))
        words = [{"word": w, "start": round(i * step, 3), "end": round((i + 1) * step, 3)} for i, w in enumerate(toks)]
        return VoiceSynthesisResult(audio_path=str(audio), words=words, duration_s=4.0, cost_usd=0.001)


def _chatty_transcriber(audio_path, label=""):
    heard = "sure I can help but could you clarify what you want me to say".split()
    return [{"word": w, "start": i * 0.3, "end": i * 0.3 + 0.25} for i, w in enumerate(heard)]


def test_failed_voiceover_verification_exits_nonzero_names_chunks_and_never_renders(tmp_path: Path) -> None:
    clips = tmp_path / "clips"
    _make_test_clip(clips / "Scene_01.mp4", duration_s=3.0)
    _make_test_clip(clips / "Scene_02.mp4", duration_s=3.0)
    script = tmp_path / "script.txt"
    script.write_text(SCRIPT, encoding="utf-8")
    out = tmp_path / "out.mp4"
    provider = _SineProvider(tmp_path)

    with pytest.raises(CutError) as exc_info:
        run_cut(
            out_path=out,
            script_path=script,
            clips_dir=clips,
            provider=provider,
            transcriber=_chatty_transcriber,
            vo_retries=1,
            quiet=True,
        )
    err = exc_info.value
    assert err.exit_code == 1
    assert "chunk 0" in str(err) and "nothing was rendered" in str(err)
    assert err.report["status"] == "vo_verify_failed" and err.report["failed_chunks"] == [0]
    assert not out.exists()
    proj = tmp_path / "out.cut"
    assert not (proj / "manifest.yaml").exists()
    assert not (proj / "voiceover.mp3").exists()
    assert (proj / "voiceover.verify.json").is_file()
    assert json.loads((proj / "voiceover.verify.json").read_text())["passed"] is False
    calls_after_failure = provider.calls
    assert calls_after_failure == 2  # initial take + 1 retry

    # The failed take must not be served from cache: a second run regenerates (and fails again, still no render).
    with pytest.raises(CutError):
        run_cut(
            out_path=out,
            script_path=script,
            clips_dir=clips,
            provider=provider,
            transcriber=_chatty_transcriber,
            vo_retries=1,
            quiet=True,
        )
    assert provider.calls > calls_after_failure
    assert not out.exists()


def test_cli_exit_code_is_nonzero_when_voiceover_verification_fails(monkeypatch, tmp_path: Path) -> None:
    def _failing(**kwargs):
        raise CutError("Voiceover verification FAILED after 2 retries; nothing was rendered.\n  - chunk 0: similarity 0.03", exit_code=1,
                       report={"status": "vo_verify_failed", "failed_chunks": [0]})

    monkeypatch.setattr("parallax.cut.run_cut", _failing)
    script = tmp_path / "s.txt"
    script.write_text("Hello there.", encoding="utf-8")
    code = cli.main(["cut", "--script", str(script), "--clips", str(tmp_path), "--out", str(tmp_path / "o.mp4")])
    assert code == 1
