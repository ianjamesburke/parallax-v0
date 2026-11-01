"""Single-line caption rule: one line, 1-2 words, 3 max (owner rule 2026-11-01)."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from parallax.captions import CAPTION_STYLES, resolve_caption_style
from parallax.captions.burn import burn_captions
from parallax.captions.chunker import chunk_words, split_chunks_to_fit
from parallax.captions.layout import fits_one_line, layout_caption
from parallax.cli._cut import _parse_caption_style
from parallax.editor_export import _build_srt
from parallax.ffmpeg_utils import _ffmpeg_has_drawtext

_requires_drawtext = pytest.mark.skipif(not _ffmpeg_has_drawtext(), reason="ffmpeg lacks drawtext")


def _words(text: str, step: float = 0.4) -> list[dict]:
    return [{"word": w, "start": round(i * step, 3), "end": round(i * step + step * 0.75, 3)}
            for i, w in enumerate(text.split())]


def _capture_burn(monkeypatch, words, style, fontsize, **kw):
    """Run burn_captions with real chunking/layout/drawtext building; ffmpeg mocked."""
    import parallax.ffmpeg_utils as ffmpeg
    monkeypatch.setattr(ffmpeg, "_ffmpeg_has_drawtext", lambda: True)
    monkeypatch.setattr(ffmpeg, "probe_resolution", lambda _p: (720, 1280))
    monkeypatch.setattr(ffmpeg, "_probe_fps", lambda _p: 24.0)
    captured: list[dict] = []
    with patch("parallax.captions.drawtext.run_ffmpeg") as run, \
         patch("parallax.captions.drawtext._probe_duration", return_value=5.0):
        run.return_value = type("R", (), {"returncode": 0, "stderr": ""})()
        import parallax.captions.burn as burn_module
        real = burn_module._burn_captions_drawtext

        def spy(video, chunks, *args):
            captured.extend(dict(c) for c in chunks)
            return real(video, chunks, *args)

        monkeypatch.setattr(burn_module, "_burn_captions_drawtext", spy)
        payload = json.dumps(words)
        if len(payload) > 200:  # burn_captions probes the arg as a path first; long inline JSON overflows NAME_MAX
            import tempfile
            fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
            fh.write(payload)
            fh.close()
            payload = fh.name
        burn_captions("in.mp4", payload, "/tmp/never-written.mp4",
                      caption_style=style, fontsize=fontsize, **kw)
    argv = run.call_args.args[0]
    flag = "-filter_complex" if "-filter_complex" in argv else "-vf"
    return captured, argv[argv.index(flag) + 1]


# ─── defaults ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", list(CAPTION_STYLES))
def test_every_preset_is_single_line_three_words_max(name):
    style = resolve_caption_style(name)
    assert style["max_lines"] == 1
    assert style["max_words"] == 3
    assert style["three_word_max_letters"] == 14
    assert style["three_word_rule"] == "function_words"


def test_all_seven_presets_covered():
    assert set(CAPTION_STYLES) == {"bangers", "impact", "bebas", "anton", "clean", "karaoke", "soft"}


# ─── chunking policy ─────────────────────────────────────────────────────

def test_integer_words_per_chunk_clamped_to_max_words():
    words = _words("a b c d e f g h")
    with patch("parallax.captions.chunker.log") as log:
        chunks = chunk_words(words, 5, {**resolve_caption_style("karaoke"), "three_word_rule": "any"})
    assert [len(c["words"]) for c in chunks] == [3, 3, 2]
    assert log.info.call_args.args[1:] == (5, 3, 3)


def test_three_words_only_when_function_words():
    style = resolve_caption_style("bangers")
    assert [c["text"] for c in chunk_words(_words("is a gel"), 3, style)] == ["is a gel"]
    long = chunk_words(_words("absolutely beautiful results"), 3, style)
    assert [c["text"] for c in long] == ["absolutely beautiful", "results"]
    assert all(len(c["words"]) <= 2 for c in long)


def test_three_word_letter_guard_ignores_punctuation():
    # "letters" rule: 12 letters exactly (16 characters); quotes, apostrophe and period do not count.
    style = {**resolve_caption_style("bangers"), "three_word_rule": "letters", "three_word_max_letters": 12}
    assert len(chunk_words(_words("“Here’s what lip.”"), 3, style)) == 1
    assert len(chunk_words(_words("“Here’s what lips.”"), 3, style)) == 2


def test_connector_carry_still_applies_under_letter_budget():
    chunks = chunk_words(_words("cats and elephants"), 3, resolve_caption_style("bangers"))
    assert [c["text"] for c in chunks] == ["cats", "and elephants"]
    letters = {**resolve_caption_style("bangers"), "three_word_rule": "letters"}
    assert [c["text"] for c in chunk_words(_words("cats and elephants"), 3, letters)] == ["cats", "and elephants"]


def test_smart_mode_obeys_max_words():
    chunks = chunk_words(_words("a I a I a"), "smart", resolve_caption_style("bangers"))
    assert max(len(c["words"]) for c in chunks) <= 3
    assert " ".join(c["text"] for c in chunks) == "a I a I a"


def test_punctuation_still_ends_chunks():
    chunks = chunk_words(_words("Go. I can start."), 3, resolve_caption_style("bangers"))
    assert [c["text"] for c in chunks] == ["Go.", "I can start."]


def test_inline_override_of_max_words_and_max_lines():
    style = _parse_caption_style('{"base":"karaoke","three_word_rule":"any","max_words":4,"max_lines":2}')
    assert style["max_words"] == 4 and style["max_lines"] == 2
    resolved = resolve_caption_style(style)
    assert [len(c["words"]) for c in chunk_words(_words("a b c d e f"), 5, resolved)] == [4, 2]
    # Multi-line layout is still reachable through the override.
    laid = layout_caption("absolutely exhausted all", 1080, 1920, 78, {**resolve_caption_style("clean"), "max_lines": 2}, .78)
    assert "\n" in laid["text"]


def test_srt_export_obeys_three_word_cap():
    srt = _build_srt(_words("a b c d e f g"), words_per_chunk=5)
    texts = [block.split("\n")[2] for block in srt.strip().split("\n\n")]
    assert texts == ["a b", "c d", "e f", "g"]
    any_srt = _build_srt(_words("a b c d e f g"), words_per_chunk=3)
    assert [len(b.split("\n")[2].split()) for b in any_srt.strip().split("\n\n")] == [2, 2, 2, 1]


# ─── width split ─────────────────────────────────────────────────────────

def test_split_chunks_to_fit_uses_each_pieces_own_timings():
    chunk = chunk_words(_words("one two three", step=1.0), 3, {"three_word_rule": "any"})[0]
    pieces = split_chunks_to_fit([chunk], lambda text: len(text.split()) == 1)
    assert [p["text"] for p in pieces] == ["one", "two", "three"]
    for piece, word in zip(pieces, chunk["words"]):
        assert (piece["start"], piece["end"]) == (word["start"], word["end"])
        assert piece["words"] == [word]


def test_single_overwide_word_is_left_for_the_layout_to_shrink():
    chunk = chunk_words(_words("SUPERCALIFRAGILISTICEXPIALIDOCIOUS"), 3, {})[0]
    assert split_chunks_to_fit([chunk], lambda _t: False) == [chunk]


def test_overflowing_chunk_at_large_fontsize_is_split_with_correct_timings(monkeypatch):
    words = _words("gel injected into delicate tissue", step=0.5)
    style = resolve_caption_style("bangers")
    unsplit = chunk_words(words, 2, style)
    assert [c["text"] for c in unsplit] == ["gel injected", "into delicate", "tissue"]
    captured, _ = _capture_burn(monkeypatch, words, "bangers", 150,
                                words_per_chunk=2, gap_hold_frames=0, animation_override="none")
    assert len(captured) > len(unsplit)
    assert [w for c in captured for w in c["words"]] == [{k: w[k] for k in ("word", "start", "end")} for w in words]
    for c in captured:
        assert "\n" not in c["text"]
        assert (c["start"], c["end"]) == (c["words"][0]["start"], c["words"][-1]["end"])
        assert len(c["words"]) == 1 or fits_one_line(c["text"], 720, 1280, 150, style)


@pytest.mark.parametrize("name", list(CAPTION_STYLES))
@pytest.mark.parametrize("size", [40, 78, 140])
def test_layout_never_wraps_single_line_styles(name, size):
    style = resolve_caption_style(name)
    for text in ("absolutely exhausted all", "SUPERCALIFRAGILISTICEXPIALIDOCIOUS absolutely", "a\nb c"):
        laid = layout_caption(text, 720, 1280, size, style, .65)
        assert "\n" not in laid["text"]
        box, safe = laid["box"], laid["safe"]
        assert box["left"] >= safe["left"] and box["right"] <= safe["right"]


def test_karaoke_highlight_windows_follow_words_after_split(monkeypatch):
    words = [{"word": "lip", "start": 0.0, "end": 0.3},
             {"word": "filler", "start": 0.5, "end": 0.9},
             {"word": "really", "start": 1.0, "end": 1.4}]
    style = {"base": "karaoke", "three_word_rule": "any", "animation": {"type": "none"}}
    resolved = resolve_caption_style(style)
    # Pick a size where "LIP FILLER" fits one line but "LIP FILLER REALLY" does not.
    size = next(s for s in range(40, 200)
                if not fits_one_line("lip filler really", 720, 1280, s, resolved))
    assert fits_one_line("lip filler", 720, 1280, size, resolved)
    captured, graph = _capture_burn(monkeypatch, words, style, size, words_per_chunk=3)
    assert [c["text"] for c in captured] == ["lip filler", "really"]
    assert [[w["word"] for w in c["words"]] for c in captured] == [["lip", "filler"], ["really"]]
    hl = [p for p in graph.split(",drawtext=") if "box=1" in p]
    assert [re.search(r"text='(\w+)'", p).group(1) for p in hl] == ["LIP", "FILLER", "REALLY"]
    wins = [re.search(r"enable='gte\(t,([\d.]+)\)\*lt\(t,([\d.]+)\)'", p).groups() for p in hl]
    # Gap-hold carries the first chunk up to the second; highlights tile the timeline.
    assert wins == [("0.0", "0.5"), ("0.5", "1.0"), ("1.0", "1.4")]
    # Both chunks render on one line: every word of a chunk shares one y.
    ys = {re.search(r":y=(\d+):", p).group(1) for p in hl[:2]}
    assert len(ys) == 1


# ─── real ffmpeg ─────────────────────────────────────────────────────────

@_requires_drawtext
@pytest.mark.parametrize("name", ["karaoke", "soft"])
def test_real_burn_single_line(tmp_path, name):
    src = tmp_path / "in.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                    "-i", "color=gray:s=360x640:d=2:r=24", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", str(src)], check=True, capture_output=True)
    report = tmp_path / "report.json"
    words = tmp_path / "words.json"
    words.write_text(json.dumps(_words("Filler is a gel injected into delicate lip tissue.", 0.2)))
    out = burn_captions(str(src), str(words),
                        str(tmp_path / "out.mp4"), words_per_chunk=5, fontsize=40,
                        caption_style=name, report_path=str(report))
    assert Path(out).stat().st_size > 0
    boxes = json.loads(report.read_text())["boxes"]
    assert boxes and all("\n" not in b["text"] and len(b["text"].split()) <= 3 for b in boxes)
    assert all(b["inside_safe"] for b in boxes)


# ─── default policy: 1-2 words, 3 only for function words ───────────────

PA9 = json.loads((Path(__file__).parent / "fixtures" / "pa9_words.json").read_text())
PA9_SEQUENCE = ["Here’s what a", "lip filler", "really does.", "Filler is a",
                "gel injected", "into delicate", "lip tissue.", "It attracts"]


@pytest.mark.parametrize("name", list(CAPTION_STYLES))
@pytest.mark.parametrize("wpc", ["smart", 3])
def test_default_policy_on_pa9_words(name, wpc):
    chunks = chunk_words(PA9, wpc, resolve_caption_style(name))
    assert [c["text"] for c in chunks] == PA9_SEQUENCE
    triples = [c["text"] for c in chunks if len(c["words"]) == 3]
    assert triples == ["Here’s what a", "Filler is a"]


def test_content_triples_rejected():
    assert [c["text"] for c in chunk_words(_words("gel injected into"), "smart", style)] == ["gel", "injected into"]
    assert [c["text"] for c in chunk_words(_words("gel injected into"), "smart", style)] == ["gel injected", "into"]  # 15 letters > 14; legacy greedy split
    assert [c["text"] for c in chunk_words(_words("really does."), "smart", style)] == ["really does."]
    assert [c["text"] for c in chunk_words(_words("lip filler really"), "smart", style)] == ["lip filler", "really"]
    for text in ("lip filler really", "gel injected into", "tiny delicate lip tissue"):
        assert all(len(c["words"]) <= 2 for c in chunk_words(_words(text), "smart", style))


def test_function_word_matching_ignores_case_punctuation_and_curly_apostrophes():
    style = resolve_caption_style("karaoke")
    for text in ("HERE’S WHAT A", "Here's what, a", "“It’s the” gel"):
        assert len(chunk_words(_words(text), "smart", style)[0]["words"]) >= 2
    assert [c["text"] for c in chunk_words(_words("IT’S THE GEL"), "smart", style)] == ["IT’S THE GEL"]
    assert [c["text"] for c in chunk_words(_words("Here’s what a"), "smart", style)] == ["Here’s what a"]


def test_no_dangling_function_word_cards():
    style = resolve_caption_style("karaoke")
    # trailing function word is not stranded alone, nor does a pair end on "into"
    assert [c["text"] for c in chunk_words(_words("big gel into it."), "smart", style)] == ["big gel", "into it."]
    assert [c["text"] for c in chunk_words(_words("injected into delicate tissue"), "smart", style)] == \
        ["injected", "into delicate", "tissue"]
    # a lone "a" joins the previous allowed unit
    assert [c["text"] for c in chunk_words(_words("Filler is a"), "smart", style)] == ["Filler is a"]


def test_integer_two_caps_at_two_and_one_is_single():
    style = resolve_caption_style("karaoke")
    assert all(len(c["words"]) <= 2 for c in chunk_words(PA9, 2, style))
    assert [c["text"] for c in chunk_words(PA9, 2, style)][:2] == ["Here’s what", "a lip"]
    assert all(len(c["words"]) == 1 for c in chunk_words(PA9, 1, style))


def test_rule_override_via_inline_json():
    words = _words("gel injected into")
    any_style = resolve_caption_style(_parse_caption_style('{"base":"karaoke","three_word_rule":"any"}'))
    assert [c["text"] for c in chunk_words(words, "smart", any_style)] == ["gel injected into"]
    four = resolve_caption_style(_parse_caption_style('{"base":"karaoke","three_word_rule":"any","max_words":4}'))
    assert [len(c["words"]) for c in chunk_words(_words("a b c d e"), 4, four)] == [4, 1]
    letters = resolve_caption_style({"base": "karaoke", "three_word_rule": "letters"})
    assert [c["text"] for c in chunk_words(words, 3, letters)] == ["gel injected", "into"]  # 15 letters > 14; legacy greedy split
    with pytest.raises(ValueError, match="three_word_rule"):
        chunk_words(words, "smart", {"three_word_rule": "bogus"})


def test_default_burn_without_words_per_chunk_makes_multiword_chunks(monkeypatch):
    captured, _ = _capture_burn(monkeypatch, PA9, "karaoke", 55, animation_override="none")
    assert [c["text"] for c in captured] == PA9_SEQUENCE
    assert not all(len(c["words"]) == 1 for c in captured)


def test_default_burn_karaoke_highlights_match_word_timings(monkeypatch):
    captured, graph = _capture_burn(monkeypatch, PA9, "karaoke", 55,
                                    gap_hold_frames=0, animation_override="none")
    assert [w for c in captured for w in c["words"]] == [{k: w[k] for k in ("word", "start", "end")} for w in PA9]
    hl = [p for p in graph.split(",drawtext=") if "box=1" in p]
    wins = [tuple(map(float, re.search(r"enable='gte\(t,([\d.]+)\)\*lt\(t,([\d.]+)\)'", p).groups())) for p in hl]
    assert len(wins) == len(PA9)
    assert [w[0] for w in wins] == pytest.approx([w["start"] for w in PA9], abs=0.001)


def test_srt_default_follows_policy():
    srt = _build_srt(PA9)
    texts = [block.split("\n")[2] for block in srt.strip().split("\n\n")]
    assert texts == PA9_SEQUENCE
