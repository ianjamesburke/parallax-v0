"""Smart caption-chunker tests.

The captioner shows one word at a time by default but glues very short
adjacent words ("a", "to", "in a") into the same chunk so single-letter
flashes don't appear on screen for a fraction of a second. Locks in the
behaviour so future tweaks don't accidentally regress the rule.
"""

from __future__ import annotations

from parallax.tools_video import _smart_chunk_words


def _w(word: str, start: float, end: float) -> dict:
    return {"word": word, "start": start, "end": end}


def test_long_words_stay_alone():
    words = [_w("Tired", 0.0, 0.5), _w("coffee", 0.5, 1.2), _w("everywhere", 1.2, 2.0)]
    chunks = _smart_chunk_words(words, max_letters=4)
    assert [c["text"] for c in chunks] == ["Tired", "coffee", "everywhere"]


def test_tiny_pair_combines():
    words = [_w("in", 0.0, 0.2), _w("a", 0.2, 0.3), _w("hurry", 0.3, 0.9)]
    chunks = _smart_chunk_words(words, max_letters=4)
    assert [c["text"] for c in chunks] == ["in a", "hurry"]


def test_two_letter_pair_at_threshold():
    """`to do` = 4 letters, fits exactly at max_letters=4."""
    words = [_w("to", 0.0, 0.2), _w("do", 0.2, 0.4)]
    chunks = _smart_chunk_words(words, max_letters=4)
    assert [c["text"] for c in chunks] == ["to do"]


def test_short_long_does_not_combine():
    """`a quick` = 6 letters exceeds 4; emit separately."""
    words = [_w("a", 0.0, 0.1), _w("quick", 0.1, 0.6)]
    chunks = _smart_chunk_words(words, max_letters=4)
    assert [c["text"] for c in chunks] == ["a", "quick"]


def test_chunk_timestamps_span_first_to_last_word():
    words = [_w("in", 0.0, 0.2), _w("a", 0.25, 0.4), _w("hurry", 0.45, 1.0)]
    chunks = _smart_chunk_words(words, max_letters=4)
    assert chunks[0]["start"] == 0.0
    assert chunks[0]["end"] == 0.4   # ends at "a"
    assert chunks[1]["start"] == 0.45
    assert chunks[1]["end"] == 1.0


def test_empty_input_returns_empty_list():
    assert _smart_chunk_words([], max_letters=4) == []


def test_brewforge_line_chunks_as_expected():
    """Sanity check on real ad copy. 'Tired of the same flat coffee?' should
    keep small connectors paired and big nouns alone."""
    text = ["Tired", "of", "the", "same", "flat", "coffee"]
    words = [_w(t, i * 0.4, (i + 1) * 0.4) for i, t in enumerate(text)]
    chunks = _smart_chunk_words(words, max_letters=4)
    out = [c["text"] for c in chunks]
    # 'of the' = 5 → too long; 'of' alone (2) then 'the' alone (3)
    # 'Tired' (5), 'same' (4), 'flat' (4), 'coffee' (6) all alone
    assert out == ["Tired", "of", "the", "same", "flat", "coffee"]
