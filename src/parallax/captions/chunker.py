"""Caption grouping that respects authored sentence and phrase boundaries.

Fixed word counts and smart letter counts are upper bounds. Punctuation ends
chunks early; original text and word timestamps are retained unchanged.
"""
from __future__ import annotations

import re

# Titles and common abbreviations that normally introduce the next word.
_CONTINUING_ABBREVIATIONS = frozenset({
    "mr.", "mrs.", "ms.", "dr.", "prof.", "sr.", "jr.", "st.",
    "vs.", "e.g.", "i.e.", "no.", "fig.",
})
_CLOSING_MARKS = '\"\'”’»)]}'


def _ends_phrase(word: str, next_word: str = "") -> bool:
    """Small punctuation heuristic, not a language parser.

    Internal periods/commas (3.5, 1,000) are not boundaries. Honorifics and
    initials stay with their following names. Contextual abbreviations such
    as 'etc.' and 'U.S.' continue before lowercase words, but can end a
    sentence before a capitalized word.
    """
    token = word.rstrip(_CLOSING_MARKS)
    if not token:
        return False
    if token.endswith(("!", "?", ",", ";", ":", "—", "–", "…")):
        return True
    if not token.endswith("."):
        return False
    if token.endswith("..."):
        return True
    if token.lower() in _CONTINUING_ABBREVIATIONS:
        return False
    if re.fullmatch(r"[A-Z]\.", token):
        return False
    if token.lower() in {"etc.", "approx.", "a.m.", "p.m."} or re.fullmatch(r"(?:[A-Za-z]\.){2,}", token):
        following = next_word.lstrip('\"\'“‘«([{')
        return not following or not following[0].islower()
    return True


def _chunk_words(words: list[dict], *, max_words: int | None = None, max_letters: int | None = None) -> list[dict]:
    chunks: list[dict] = []
    current: list[dict] = []
    letters = 0

    def emit() -> None:
        chunks.append({
            "text": " ".join(word["word"] for word in current),
            "start": current[0]["start"],
            "end": current[-1]["end"],
        })

    for index, word in enumerate(words):
        length = len(word["word"])
        if current and ((max_words is not None and len(current) >= max_words)
                        or (max_letters is not None and letters + length > max_letters)):
            # Keep a trailing connector with the phrase it introduces when
            # the fixed-size limit would otherwise strand it on the prior card.
            carry = []
            if max_words is not None and len(current) > 1 and current[-1]["word"].lower() in {"and", "or", "but"}:
                carry = [current.pop()]
            emit()
            current = carry
            letters = sum(len(item["word"]) for item in carry)
        current.append(word)
        letters += length
        following = words[index + 1]["word"] if index + 1 < len(words) else ""
        if _ends_phrase(word["word"], following):
            emit()
            current = []
            letters = 0
    if current:
        emit()
    return chunks


def _fixed_chunk_words(words: list[dict], max_words: int) -> list[dict]:
    """At most N words per caption, ending early at phrase punctuation."""
    if max_words < 1:
        raise ValueError("words_per_chunk must be a positive integer or 'smart'")
    return _chunk_words(words, max_words=max_words)


def _smart_chunk_words(words: list[dict], max_letters: int = 4) -> list[dict]:
    """Combine tiny adjacent words up to a letter budget within each phrase.

    Words longer than the budget stay alone. Sentence and phrase punctuation
    always ends the current chunk, even when the next short word would fit.
    """
    if max_letters < 1:
        raise ValueError("max_letters must be positive")
    return _chunk_words(words, max_letters=max_letters)
