"""Verification tolerates ASR splits/spellings but still catches real dropped words (rerun 2 strings)."""
from __future__ import annotations

from parallax.cut import (
    DEFAULT_VOICE_MODEL,
    _format_strict_vo_failure,
    diff_script_vs_transcript,
)

# Script and whisper transcripts taken from the roman-cut-run2 chunk verification (voiceover.verify.json).
SCRIPT_A = (
    "Because burning antler vaporizes the protein and leaves behind pure hydroxyapatite, the exact calcium "
    "crystal that makes up 97% of human tooth enamel. Case closed? Not by a long shot. Hand-ground antler ash "
    "polishes the outside of the tooth, but the crystals are way too big to reach inside the microscopic holes "
    "where decay actually starts."
)
HEARD_A = (
    "because burning antler vaporizes the protein and leaves behind pure hydroxyapatite the exact calcium "
    "crystal that makes up ninety seven percent of human tooth a namal case closed not by a long shot "
    "hand ground antler ash polishes the outside of the tooth but the crystals are way too big to reach inside "
    "the microscopic holes where decay actually starts"
)
SCRIPT_B = (
    "Researchers shrink hydroxyapatite down to the nano-scale, one thousand times smaller than Roman antler ash, "
    "so it fits right inside microscopic tooth pores."
)
HEARD_B = (
    "researchers shrink hydroxyapatite down to the nanoscale one thousand times smaller than roman antler ash "
    "so it fits right inside microscopic tooth pores"
)


def test_enamel_vs_a_namal_is_an_asr_spelling_not_a_drop() -> None:
    res = diff_script_vs_transcript(SCRIPT_A, HEARD_A)
    assert res["passed"] is True, res["missing_runs"]
    assert res["missing_runs"] == []
    assert {"expected": ["enamel"], "heard": ["a", "namal"]} in res["asr_equivalent_runs"]
    assert res["similarity"] >= 0.95


def test_nano_scale_vs_nanoscale_is_an_asr_join_not_a_drop() -> None:
    res = diff_script_vs_transcript(SCRIPT_B, HEARD_B)
    assert res["passed"] is True, res["missing_runs"]
    assert res["missing_runs"] == []


def test_rerun2_chunk_split_variants_pass() -> None:
    res = diff_script_vs_transcript(SCRIPT_B, HEARD_B.replace("nanoscale", "nano scale"))
    assert res["passed"] is True


def test_dropped_tooth_enamel_still_fails() -> None:
    dropped = HEARD_A.replace("tooth a namal ", "")
    res = diff_script_vs_transcript(SCRIPT_A, dropped)
    assert res["passed"] is False
    assert any("tooth" in r["words"] and "enamel" in r["words"] and not r["replaced_by"] for r in res["missing_runs"])


def test_dropped_longer_phrase_still_fails() -> None:
    dropped = HEARD_A.replace("of human tooth a namal ", "")
    res = diff_script_vs_transcript(SCRIPT_A, dropped)
    assert res["passed"] is False
    assert res["max_missing_run"] >= 4


def test_dropped_two_words_without_replacement_fails_even_with_high_similarity() -> None:
    dropped = HEARD_B.replace("roman antler ", "")
    res = diff_script_vs_transcript(SCRIPT_B, dropped)
    assert res["passed"] is False


def test_unrelated_substitution_is_not_treated_as_asr_equivalent() -> None:
    res = diff_script_vs_transcript(SCRIPT_B, HEARD_B.replace("microscopic tooth pores", "banana smoothie recipe"))
    assert res["passed"] is False
    assert all("banana" not in r["heard"] for r in res["asr_equivalent_runs"])
    assert any(r["replaced_by"] and "banana" in r["replaced_by"] for r in res["missing_runs"])


def test_failure_hint_only_suggests_tts_gemini_for_other_models() -> None:
    item = {"chunk_index": 1, "similarity": 0.9, "actual_words": ["a"], "missing_runs": [
        {"text": "tooth enamel", "start_s": 3.0, "replaced_by": []}]}
    gem = _format_strict_vo_failure([item], voice_model=DEFAULT_VOICE_MODEL, retries=2)
    mini = _format_strict_vo_failure([item], voice_model="tts-mini", retries=2)
    assert "Try --voice-model" not in gem and "--no-strict-vo" in gem
    assert "Try --voice-model tts-gemini" in mini
    assert "'tooth enamel' at 3.0s (not heard)" in gem


def test_rerun2_proper_noun_mishearings_pass_but_are_reported() -> None:
    script = (
        "They discover a way to combine nano-hydroxyapatite with wild spruce sap, chicle, mastic, myrrh. "
        "Then the founders of Larine Co partner with a specialist formulation lab to solve the problem once and for all."
    )
    heard = (
        "they discover a way to combine nano hydroxyapatite with wild spruce sap cheekle mastic myrrh "
        "then the founders of lorenzo partner with a specialist formulation lab to solve the problem once and for all"
    )
    pad = " the quick brown fox jumps over the lazy dog while the tide comes in at dawn" * 8  # chunk-sized
    res = diff_script_vs_transcript(script + pad, heard + pad)
    assert res["passed"] is True, res["missing_runs"]
    assert res["similarity"] >= 0.95
    texts = {r["text"]: r["replaced_by"] for r in res["missing_runs"]}
    assert texts.get("chicle") == ["cheekle"]
    assert texts.get("larine co") == ["lorenzo"]


def test_three_word_substitution_still_fails() -> None:
    res = diff_script_vs_transcript(SCRIPT_B, HEARD_B.replace("microscopic tooth pores", "banana smoothie recipe"))
    assert res["passed"] is False


def test_buy3_get3_digit_glue_matches_spelled_numbers() -> None:
    script = "Buy 3 get 3 free today" + " and then the tide comes in at dawn" * 6
    heard = "buy3 get3 free today" + " and then the tide comes in at dawn" * 6
    res = diff_script_vs_transcript(script, heard)
    assert res["passed"] is True, res["missing_runs"]
    assert res["missing_runs"] == []


def test_similarity_never_exceeds_one_with_uneven_equivalent_runs() -> None:
    res = diff_script_vs_transcript("founders of Larine Co partner with a lab and more words", "founders of lorinco partner with a lab and more words")
    assert 0.0 <= res["similarity"] <= 1.0
    res = diff_script_vs_transcript("the exact enamel crystal", "the exact a name will crystal")
    assert 0.0 <= res["similarity"] <= 1.0
