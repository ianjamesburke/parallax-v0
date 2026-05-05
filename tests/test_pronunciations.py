"""Tests for pronunciation hint substitution and restoration."""
from parallax.voiceover import apply_pronunciations, _restore_pronunciations


def test_apply_substitutes_known_word():
    result = apply_pronunciations("Take Shilajit daily.", {"Shilajit": "shilajit"})
    assert "shilajit" in result
    assert "Shilajit" not in result


def test_apply_case_insensitive():
    result = apply_pronunciations("SHILAJIT is great.", {"Shilajit": "shilajit"})
    assert "SHILAJIT" not in result


def test_apply_word_boundary_only():
    # "PreShilajit" should NOT be substituted
    result = apply_pronunciations("PreShilajit is not the same.", {"Shilajit": "phonetic"})
    assert "PreShilajit" in result


def test_apply_empty_pronunciations():
    text = "Hello world."
    assert apply_pronunciations(text, {}) == text


def test_restore_replaces_phonetic():
    words = [{"word": "shilajit", "start": 0.0, "end": 0.5}]
    result = _restore_pronunciations(words, {"Shilajit": "shilajit"})
    assert result[0]["word"] == "Shilajit"


def test_restore_handles_hyphenated_phonetic():
    words = [{"word": "shilAHjit", "start": 0.0, "end": 0.5}]
    result = _restore_pronunciations(words, {"Shilajit": "shil-AH-jit"})
    assert result[0]["word"] == "Shilajit"


def test_restore_preserves_timestamps():
    words = [{"word": "shilajit", "start": 1.2, "end": 1.9}]
    result = _restore_pronunciations(words, {"Shilajit": "shilajit"})
    assert result[0]["start"] == 1.2
    assert result[0]["end"] == 1.9


def test_restore_no_match_unchanged():
    words = [{"word": "hello", "start": 0.0, "end": 0.3}]
    result = _restore_pronunciations(words, {"Shilajit": "shilajit"})
    assert result[0]["word"] == "hello"


def test_restore_empty_pronunciations():
    words = [{"word": "foo", "start": 0.0, "end": 0.3}]
    result = _restore_pronunciations(words, {})
    assert result == words
