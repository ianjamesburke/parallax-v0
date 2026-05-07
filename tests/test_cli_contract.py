"""CLI contract tests — validate flag parsing, routing, and error surfaces for all 10 CLI modules.

Layer: CONTRACT
Mechanism: cli.main([...]) + monkeypatch.setattr on pipeline entry points.
No API calls. No PARALLAX_TEST_MODE. No I/O except tmp_path fixtures.
This is the regression harness for issue #100 (argparse → Typer rewrite).
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from parallax import cli


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _help_exits_zero(*args: str) -> None:
    rc = cli.main(list(args) + ["--help"])
    assert rc == 0, f"Expected 0 for {args!r} --help, got {rc}"


def _rejects_args(*args: str) -> None:
    rc = cli.main(list(args))
    assert rc == 2, f"Expected 2 for {args!r}, got {rc}"


# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------

class TestModels:
    def test_help(self):
        _help_exits_zero("models", "list")

    def test_list_exits_zero(self, capsys):
        rc = cli.main(["models", "list"])
        assert rc == 0
        assert "image" in capsys.readouterr().out.lower()

    def test_list_kind_image(self):
        assert cli.main(["models", "list", "--kind", "image"]) == 0

    def test_list_kind_video(self):
        assert cli.main(["models", "list", "--kind", "video"]) == 0

    def test_list_kind_tts(self):
        assert cli.main(["models", "list", "--kind", "tts"]) == 0

    def test_list_json(self, capsys):
        rc = cli.main(["models", "list", "--json"])
        assert rc == 0
        parsed = json.loads(capsys.readouterr().out)
        assert "image" in parsed and "video" in parsed

    def test_list_invalid_kind_rejected(self):
        _rejects_args("models", "list", "--kind", "bogus")

    def test_show_known_alias(self, capsys):
        rc = cli.main(["models", "show", "mid"])
        assert rc == 0
        assert "mid" in capsys.readouterr().out

    def test_show_unknown_alias_exits_1(self):
        rc = cli.main(["models", "show", "totally-fake-alias-xyz"])
        assert rc != 0

    def test_bare_models_shows_help(self):
        rc = cli.main(["models"])
        assert rc == 0


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------

class TestSchema:
    def test_brief_exits_zero(self, capsys):
        rc = cli.main(["schema", "brief"])
        assert rc == 0
        assert "properties" in json.loads(capsys.readouterr().out)

    def test_plan_exits_zero(self, capsys):
        rc = cli.main(["schema", "plan"])
        assert rc == 0
        assert "properties" in json.loads(capsys.readouterr().out)

    def test_bare_overview_exits_zero(self, capsys):
        rc = cli.main(["schema"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "brief" in out and "plan" in out

    def test_output_writes_file(self, tmp_path, capsys):
        out_file = tmp_path / "schema.json"
        rc = cli.main(["schema", "brief", "--output", str(out_file)])
        assert rc == 0
        assert out_file.exists()
        assert json.loads(out_file.read_text())
        assert capsys.readouterr().out == ""

    def test_output_without_target_exits_1(self, tmp_path, capsys):
        rc = cli.main(["schema", "--output", str(tmp_path / "x.json")])
        assert rc != 0
        assert capsys.readouterr().err.strip()

    def test_invalid_target_rejected(self):
        _rejects_args("schema", "bogus")


# ---------------------------------------------------------------------------
# log
# ---------------------------------------------------------------------------

class TestLog:
    def test_help(self):
        _help_exits_zero("log")

    def test_list_exits_zero_empty(self, monkeypatch, capsys):
        import parallax.runlog as runlog_mod
        monkeypatch.setattr(runlog_mod, "load_run_index", lambda: [])
        rc = cli.main(["log", "list"])
        assert rc == 0

    def test_latest_exits_1_when_no_runs(self, monkeypatch, capsys):
        import parallax.runlog as runlog_mod
        monkeypatch.setattr(runlog_mod, "find_run", lambda spec: None)
        rc = cli.main(["log", "latest"])
        assert rc != 0
        assert "no run found" in capsys.readouterr().err

    def test_invalid_level_rejected(self):
        _rejects_args("log", "--level", "trace")

    def test_list_since_filter(self, monkeypatch, capsys):
        import parallax.runlog as runlog_mod
        monkeypatch.setattr(runlog_mod, "load_run_index", lambda: [])
        rc = cli.main(["log", "list", "--since", "1d"])
        assert rc == 0


# ---------------------------------------------------------------------------
# usage
# ---------------------------------------------------------------------------

class TestUsage:
    def _fake_summary(self, include_test: bool) -> dict:
        return {
            "include_test_mode": include_test,
            "log_path": "/fake",
            "total_calls": 0,
            "total_cost_usd": 0.0,
            "total_duration_ms": 0,
            "session_count": 0,
            "by_alias": {},
        }

    def test_exits_zero(self, monkeypatch, capsys):
        import parallax.usage as usage_mod
        monkeypatch.setattr(usage_mod, "summarize", self._fake_summary)
        assert cli.main(["usage"]) == 0

    def test_include_test_flag_passed(self, monkeypatch):
        import parallax.usage as usage_mod
        captured: dict = {}

        def fake(include_test):
            captured["include_test"] = include_test
            return self._fake_summary(include_test)

        monkeypatch.setattr(usage_mod, "summarize", fake)
        cli.main(["usage", "--include-test"])
        assert captured["include_test"] is True


# ---------------------------------------------------------------------------
# credits
# ---------------------------------------------------------------------------

class TestCredits:
    def test_sufficient_balance_exits_0(self, monkeypatch, capsys):
        import parallax.openrouter as or_mod
        monkeypatch.setattr(or_mod, "check_credits",
                            lambda min_balance_usd: SimpleNamespace(total=10.0, used=2.0, remaining=8.0))
        rc = cli.main(["credits"])
        assert rc == 0
        assert "8.00" in capsys.readouterr().out

    def test_low_balance_exits_1(self, monkeypatch, capsys):
        import parallax.openrouter as or_mod
        monkeypatch.setattr(or_mod, "check_credits",
                            lambda min_balance_usd: SimpleNamespace(total=1.0, used=0.9, remaining=0.1))
        rc = cli.main(["credits"])
        assert rc != 0


# ---------------------------------------------------------------------------
# completions
# ---------------------------------------------------------------------------

class TestCompletions:
    def test_print_zsh(self, capsys):
        rc = cli.main(["completions", "print", "zsh"])
        assert rc == 0

    def test_print_bash(self, capsys):
        rc = cli.main(["completions", "print", "bash"])
        assert rc == 0

    def test_install_writes_file(self, tmp_path):
        out = tmp_path / "completion.zsh"
        rc = cli.main(["completions", "install", "--shell", "zsh", "--path", str(out)])
        assert rc == 0
        assert out.exists()

    def test_invalid_shell_rejected(self):
        _rejects_args("completions", "print", "fish")

    def test_bare_completions_shows_help(self):
        rc = cli.main(["completions"])
        assert rc == 0


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------

class TestValidate:
    def test_help(self):
        _help_exits_zero("validate")

    def test_valid_brief_exits_0(self, monkeypatch, tmp_path, capsys):
        import parallax.validate as validate_mod
        monkeypatch.setattr(validate_mod, "validate_brief",
                            lambda brief_path, folder: {"valid": True, "errors": [], "warnings": []})
        brief = tmp_path / "brief.yaml"
        brief.write_text("goal: x\n")
        rc = cli.main(["validate", "--folder", str(tmp_path), "--brief", str(brief)])
        assert rc == 0

    def test_invalid_brief_exits_1(self, monkeypatch, tmp_path):
        import parallax.validate as validate_mod
        monkeypatch.setattr(validate_mod, "validate_brief",
                            lambda brief_path, folder: {"valid": False, "errors": ["missing asset"], "warnings": []})
        brief = tmp_path / "brief.yaml"
        brief.write_text("goal: x\n")
        rc = cli.main(["validate", "--folder", str(tmp_path), "--brief", str(brief)])
        assert rc == 1

    def test_brief_and_plan_mutually_exclusive(self, tmp_path):
        _rejects_args("validate", "--folder", str(tmp_path),
                      "--brief", "x.yaml", "--plan", "y.yaml")

    def test_folder_required(self):
        _rejects_args("validate", "--brief", "x.yaml")

    def test_brief_or_plan_required(self, tmp_path):
        _rejects_args("validate", "--folder", str(tmp_path))


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------

class TestVerify:
    def test_suite_help(self):
        _help_exits_zero("verify", "suite")

    def test_init_help(self):
        _help_exits_zero("verify", "init")

    def test_suite_runs(self, monkeypatch, tmp_path):
        import parallax.verify_suite as vs_mod
        monkeypatch.setattr(vs_mod, "cli_run", lambda suite_dir, paid, case: 0)
        assert cli.main(["verify", "suite", str(tmp_path)]) == 0

    def test_init_runs(self, monkeypatch, tmp_path):
        import parallax.verify_suite as vs_mod
        monkeypatch.setattr(vs_mod, "cli_init",
                            lambda target, from_dir, resolution, force: 0)
        assert cli.main(["verify", "init", str(tmp_path / "new_case")]) == 0

    def test_bare_verify_shows_help(self):
        rc = cli.main(["verify"])
        assert rc == 0


# ---------------------------------------------------------------------------
# audio
# ---------------------------------------------------------------------------

class TestAudio:
    def test_transcribe_help(self):
        _help_exits_zero("audio", "transcribe")

    def test_transcribe_out_required(self, tmp_path):
        dummy = tmp_path / "audio.wav"
        dummy.write_bytes(b"")
        _rejects_args("audio", "transcribe", str(dummy))

    def test_transcribe_happy_path(self, monkeypatch, tmp_path, capsys):
        import parallax.audio as audio_mod
        dummy = tmp_path / "audio.wav"
        dummy.write_bytes(b"")
        out = tmp_path / "words.json"
        monkeypatch.setattr(audio_mod, "transcribe_words",
                            lambda *a, **kw: [{"word": "hi", "start": 0.0, "end": 0.4}])
        rc = cli.main(["audio", "transcribe", str(dummy), "--out", str(out)])
        assert rc == 0
        assert "1 words" in capsys.readouterr().out

    def test_cap_pauses_help(self):
        _help_exits_zero("audio", "cap-pauses")

    def test_cap_pauses_input_required(self, tmp_path):
        _rejects_args("audio", "cap-pauses", "--output", str(tmp_path / "out.wav"))

    def test_cap_pauses_output_required(self, tmp_path):
        dummy = tmp_path / "a.wav"
        dummy.write_bytes(b"")
        _rejects_args("audio", "cap-pauses", "--input", str(dummy))

    def test_cap_pauses_happy_path(self, monkeypatch, tmp_path, capsys):
        import parallax.audio as audio_mod
        dummy = tmp_path / "audio.wav"
        dummy.write_bytes(b"")
        out = tmp_path / "out.wav"
        monkeypatch.setattr(audio_mod, "cap_pauses", lambda **kw: {
            "gaps_trimmed": 2, "max_gap_s": 0.75,
            "original_duration_s": 10.0, "new_duration_s": 9.5,
            "seconds_removed": 0.5, "output": str(out),
        })
        rc = cli.main(["audio", "cap-pauses", "--input", str(dummy), "--output", str(out)])
        assert rc == 0

    def test_pad_onsets_help(self):
        _help_exits_zero("audio", "pad-onsets")

    def test_pad_onsets_words_required(self, tmp_path):
        dummy = tmp_path / "a.wav"
        dummy.write_bytes(b"")
        _rejects_args("audio", "pad-onsets", "--input", str(dummy), "--output", str(tmp_path / "out.wav"))

    def test_pad_onsets_happy_path(self, monkeypatch, tmp_path, capsys):
        import parallax.audio as audio_mod
        dummy = tmp_path / "audio.wav"
        dummy.write_bytes(b"")
        words = tmp_path / "words.json"
        words.write_text('[{"word": "hi", "start": 0.0, "end": 0.4}]')
        out = tmp_path / "out.wav"
        monkeypatch.setattr(audio_mod, "pad_onsets", lambda **kw: {
            "onsets_padded": 1, "seconds_added": 0.05,
            "original_duration_s": 1.0, "new_duration_s": 1.05, "output": str(out),
        })
        rc = cli.main([
            "audio", "pad-onsets",
            "--input", str(dummy), "--output", str(out), "--words", str(words),
        ])
        assert rc == 0

    def test_voiceover_help(self):
        _help_exits_zero("audio", "voiceover")

    def test_voiceover_out_required(self):
        _rejects_args("audio", "voiceover", "--text", "hello")

    def test_voiceover_happy_path(self, monkeypatch, tmp_path, capsys):
        import parallax.voiceover as vo_mod
        src = tmp_path / "src.mp3"
        src.write_bytes(b"")
        out = tmp_path / "out.mp3"
        monkeypatch.setattr(vo_mod, "generate_voiceover_dict", lambda **kw: {
            "audio_path": str(src), "total_duration_s": 2.0,
        })
        rc = cli.main(["audio", "voiceover", "--text", "hello world", "--out", str(out)])
        assert rc == 0

    def test_speed_help(self):
        _help_exits_zero("audio", "speed")

    def test_speed_rate_required(self, tmp_path):
        _rejects_args("audio", "speed",
                      "--in", str(tmp_path / "a.wav"), "--out", str(tmp_path / "b.wav"))

    def test_speed_rate_and_by_mutually_exclusive(self, tmp_path):
        _rejects_args("audio", "speed",
                      "--in", str(tmp_path / "a.wav"), "--out", str(tmp_path / "b.wav"),
                      "--rate", "1.2", "--by", "20%")

    def test_speed_happy_path(self, monkeypatch, tmp_path, capsys):
        import parallax.audio as audio_mod
        in_f = tmp_path / "a.wav"
        out_f = tmp_path / "b.wav"
        in_f.write_bytes(b"")
        monkeypatch.setattr(audio_mod, "speedup", lambda *a, **kw: out_f)
        rc = cli.main(["audio", "speed",
                       "--in", str(in_f), "--out", str(out_f), "--rate", "1.2"])
        assert rc == 0

    def test_detect_silences_help(self):
        _help_exits_zero("audio", "detect-silences")

    def test_detect_silences_happy_path(self, monkeypatch, tmp_path, capsys):
        import parallax.audio as audio_mod
        dummy = tmp_path / "audio.wav"
        dummy.write_bytes(b"")
        monkeypatch.setattr(audio_mod, "detect_silences", lambda *a, **kw: [])
        rc = cli.main(["audio", "detect-silences", str(dummy)])
        assert rc == 0
        assert "No silences" in capsys.readouterr().out

    def test_trim_help(self):
        _help_exits_zero("audio", "trim")

    def test_trim_folder_required(self, tmp_path):
        plan = tmp_path / "plan.yaml"
        plan.write_text("")
        _rejects_args("audio", "trim",
                      "--plan", str(plan), "--start", "1.0", "--end", "2.0")

    def test_trim_happy_path(self, monkeypatch, tmp_path, capsys):
        import parallax.audio as audio_mod
        plan = tmp_path / "plan.yaml"
        plan.write_text("")
        monkeypatch.setattr(audio_mod, "trim_silence", lambda **kw: {
            "seconds_removed": 1.0, "new_audio": "a.wav",
            "new_words": "w.json", "new_avatar": None,
        })
        rc = cli.main([
            "audio", "trim",
            "--plan", str(plan), "--folder", str(tmp_path),
            "--start", "1.0", "--end", "2.0",
        ])
        assert rc == 0

    def test_bare_audio_shows_help(self):
        rc = cli.main(["audio"])
        assert rc == 0


# ---------------------------------------------------------------------------
# video
# ---------------------------------------------------------------------------

class TestVideo:
    def test_frame_help(self):
        _help_exits_zero("video", "frame")

    def test_frame_time_required(self, tmp_path):
        _rejects_args("video", "frame", str(tmp_path / "clip.mp4"))

    def test_frame_happy_path(self, monkeypatch, tmp_path, capsys):
        import parallax.video as video_mod
        dummy = tmp_path / "clip.mp4"
        dummy.write_bytes(b"")
        out = tmp_path / "frame.png"
        monkeypatch.setattr(video_mod, "extract_frame", lambda *a, **kw: str(out))
        rc = cli.main(["video", "frame", str(dummy), "2.5"])
        assert rc == 0
        assert str(out) in capsys.readouterr().out

    def test_color_help(self):
        _help_exits_zero("video", "color")

    def test_color_happy_path(self, monkeypatch, tmp_path, capsys):
        import parallax.video as video_mod
        dummy = tmp_path / "clip.mp4"
        dummy.write_bytes(b"")
        monkeypatch.setattr(video_mod, "sample_color", lambda *a, **kw: "0xFF0000")
        rc = cli.main(["video", "color", str(dummy)])
        assert rc == 0
        assert "0xFF0000" in capsys.readouterr().out

    def test_animate_help(self):
        _help_exits_zero("video", "animate")

    def test_animate_prompt_required(self):
        _rejects_args("video", "animate")

    def test_animate_start_and_ref_mutually_exclusive(self, tmp_path):
        _rejects_args("video", "animate", "--prompt", "x",
                      "--start", str(tmp_path / "s.png"),
                      "--ref", str(tmp_path / "r.png"))

    def test_animate_end_without_start_exits_2(self, monkeypatch, tmp_path, capsys):
        import parallax.openrouter as or_mod
        monkeypatch.setattr(or_mod, "generate_video", lambda *a, **kw: tmp_path / "out.mp4")
        rc = cli.main(["video", "animate", "--prompt", "x",
                       "--end", str(tmp_path / "end.png")])
        assert rc == 2

    def test_animate_happy_path(self, monkeypatch, tmp_path, capsys):
        import parallax.openrouter as or_mod
        out = tmp_path / "out.mp4"
        monkeypatch.setattr(or_mod, "generate_video", lambda *a, **kw: out)
        rc = cli.main(["video", "animate", "--prompt", "fly through space",
                       "--out", str(tmp_path)])
        assert rc == 0
        assert str(out) in capsys.readouterr().out

    def test_bare_video_shows_help(self):
        rc = cli.main(["video"])
        assert rc == 0


# ---------------------------------------------------------------------------
# image
# ---------------------------------------------------------------------------

class TestImage:
    def test_generate_help(self):
        _help_exits_zero("image", "generate")

    def test_generate_prompt_required(self):
        _rejects_args("image", "generate")

    def test_generate_happy_path(self, monkeypatch, tmp_path, capsys):
        import parallax.openrouter as or_mod
        out = tmp_path / "img.png"
        monkeypatch.setattr(or_mod, "generate_image", lambda *a, **kw: out)
        rc = cli.main(["image", "generate", "a lion at sunset", "--out", str(tmp_path)])
        assert rc == 0
        assert str(out) in capsys.readouterr().out

    def test_analyze_help(self):
        _help_exits_zero("image", "analyze")

    def test_analyze_path_required(self):
        _rejects_args("image", "analyze")

    def test_analyze_happy_path(self, monkeypatch, tmp_path, capsys):
        import parallax.openrouter as or_mod
        img = tmp_path / "img.png"
        img.write_bytes(b"\x89PNG")
        monkeypatch.setattr(or_mod, "analyze_image", lambda *a, **kw: "A lion is shown.")
        rc = cli.main(["image", "analyze", str(img)])
        assert rc == 0
        assert "lion" in capsys.readouterr().out

    def test_bare_image_shows_help(self):
        rc = cli.main(["image"])
        assert rc == 0


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------

class TestPlan:
    def test_help(self):
        _help_exits_zero("plan")

    def test_folder_required(self):
        _rejects_args("plan")

    def test_missing_brief_exits_1(self, tmp_path, capsys):
        rc = cli.main(["plan", "--folder", str(tmp_path)])
        assert rc == 1
        assert "brief not found" in capsys.readouterr().err

    def test_happy_path(self, monkeypatch, tmp_path, capsys):
        import parallax.planner as planner_mod
        brief = tmp_path / "brief.yaml"
        brief.write_text("goal: x\n")
        plan_path = tmp_path / "parallax" / "scratch" / "plan.yaml"
        plan_path.parent.mkdir(parents=True)
        plan_path.write_text("")
        monkeypatch.setattr(planner_mod, "plan_from_brief",
                            lambda *a, **kw: SimpleNamespace(
                                ok=True, plan_path=plan_path, scene_count=2,
                                missing_assets=[], questions_path=None,
                            ))
        rc = cli.main(["plan", "--folder", str(tmp_path), "--brief", str(brief)])
        assert rc == 0
        assert "plan.yaml" in capsys.readouterr().out

    def test_missing_assets_exits_1(self, monkeypatch, tmp_path, capsys):
        import parallax.planner as planner_mod
        brief = tmp_path / "brief.yaml"
        brief.write_text("goal: x\n")
        questions = tmp_path / "questions.yaml"
        monkeypatch.setattr(planner_mod, "plan_from_brief",
                            lambda *a, **kw: SimpleNamespace(
                                ok=False, plan_path=None, scene_count=0,
                                missing_assets=["brand/logo.png"], questions_path=questions,
                            ))
        rc = cli.main(["plan", "--folder", str(tmp_path), "--brief", str(brief)])
        assert rc == 1
        assert "missing" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# ingest
# ---------------------------------------------------------------------------

class TestIngest:
    def test_help(self):
        _help_exits_zero("ingest")

    def test_path_required(self):
        _rejects_args("ingest")

    def test_empty_dir_exits_1(self, tmp_path, capsys):
        empty = tmp_path / "empty"
        empty.mkdir()
        rc = cli.main(["ingest", str(empty)])
        assert rc == 1
        assert "no recognized clip extensions" in capsys.readouterr().err

    def test_estimate_flag(self, monkeypatch, tmp_path, capsys):
        import parallax.ingest as ingest_mod
        index_path = tmp_path / "index.json"
        monkeypatch.setattr(ingest_mod, "ingest",
                            lambda *a, **kw: SimpleNamespace(
                                clips=[{"path": "a.wav"}], total_duration_s=1.0,
                                estimated_cost_usd=0.01, index_path=index_path,
                            ))
        rc = cli.main(["ingest", str(tmp_path), "--estimate"])
        assert rc == 0
        assert "est cost" in capsys.readouterr().out

    def test_visual_exits_1(self, monkeypatch, tmp_path, capsys):
        import parallax.ingest as ingest_mod
        monkeypatch.setattr(ingest_mod, "ingest",
                            lambda *a, **kw: (_ for _ in ()).throw(NotImplementedError))
        rc = cli.main(["ingest", str(tmp_path), "--visual"])
        assert rc == 1
        assert "--visual is not implemented" in capsys.readouterr().err

    def test_happy_path(self, monkeypatch, tmp_path, capsys):
        import parallax.ingest as ingest_mod
        index_path = tmp_path / "index.json"
        monkeypatch.setattr(ingest_mod, "ingest",
                            lambda *a, **kw: SimpleNamespace(
                                clips=[{"path": "a.wav"}], total_duration_s=1.5,
                                estimated_cost_usd=0.01, index_path=index_path,
                            ))
        rc = cli.main(["ingest", str(tmp_path)])
        assert rc == 0
        assert "Indexed 1 clips" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# produce
# ---------------------------------------------------------------------------

class TestProduce:
    def _fake_result(self, tmp_path) -> SimpleNamespace:
        return SimpleNamespace(
            status="ok", run_id="test", output_dir=tmp_path,
            final_video=tmp_path / "out.mp4", stills_dir=None,
            cost_usd=0.0, error=None,
        )

    def test_help(self):
        _help_exits_zero("produce")

    def test_folder_required(self):
        _rejects_args("produce", "--plan", "plan.yaml")

    def test_plan_or_brief_required(self, tmp_path):
        _rejects_args("produce", "--folder", str(tmp_path))

    def test_brief_and_plan_mutually_exclusive(self, tmp_path):
        _rejects_args("produce", "--folder", str(tmp_path),
                      "--brief", "b.yaml", "--plan", "p.yaml")

    def test_invalid_debug_level_rejected(self, tmp_path):
        _rejects_args("produce", "--folder", str(tmp_path),
                      "--plan", "p.yaml", "--debug", "5")

    def test_invalid_aspect_rejected(self, tmp_path):
        _rejects_args("produce", "--folder", str(tmp_path),
                      "--plan", "p.yaml", "--aspect", "2:3")

    def test_happy_path_with_plan(self, monkeypatch, tmp_path, capsys):
        import parallax.produce as produce_mod
        plan = tmp_path / "plan.yaml"
        plan.write_text("scenes: []\n")
        monkeypatch.setattr(produce_mod, "run_plan", lambda **kw: self._fake_result(tmp_path))
        rc = cli.main(["produce", "--folder", str(tmp_path), "--plan", str(plan), "--yes"])
        assert rc == 0

    def test_hq_flag_accepted(self, monkeypatch, tmp_path, capsys):
        import parallax.produce as produce_mod
        captured: dict = {}
        plan = tmp_path / "plan.yaml"
        plan.write_text("scenes: []\n")

        def fake_run(**kw):
            captured.update(kw)
            return self._fake_result(tmp_path)

        monkeypatch.setattr(produce_mod, "run_plan", fake_run)
        cli.main(["produce", "--folder", str(tmp_path), "--plan", str(plan), "--yes", "--hq"])
        assert captured.get("hq") is True

    def test_debug_level_passed(self, monkeypatch, tmp_path):
        import parallax.produce as produce_mod
        captured: dict = {}
        plan = tmp_path / "plan.yaml"
        plan.write_text("scenes: []\n")

        def fake_run(**kw):
            captured.update(kw)
            return self._fake_result(tmp_path)

        monkeypatch.setattr(produce_mod, "run_plan", fake_run)
        cli.main(["produce", "--folder", str(tmp_path), "--plan", str(plan), "--yes", "--debug", "2"])
        assert captured.get("debug_level") == 2
