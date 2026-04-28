"""Word-level grouping for caption chunks.

`_smart_chunk_words` decides how many adjacent words go into a single
displayed caption chunk based on combined letter count.
"""

from __future__ import annotations


def _smart_chunk_words(words: list[dict], max_letters: int = 4) -> list[dict]:
    """Group adjacent words greedily so chunks stay ≤ `max_letters` letters.

    Default behaviour shows one word per chunk, except very small words
    (e.g. "a", "to", "in") merge with their neighbour to avoid one-letter
    chunks flashing on screen for a fraction of a second. Greedy: keep
    appending words while the running letter count stays ≤ max_letters,
    then close the chunk. Words longer than max_letters are always alone.
    """
    chunks: list[dict] = []
    cur_words: list[dict] = []
    cur_letters = 0
    for w in words:
        wl = len(w["word"])
        if not cur_words:
            cur_words = [w]
            cur_letters = wl
            continue
        if cur_letters + wl <= max_letters:
            cur_words.append(w)
            cur_letters += wl
        else:
            chunks.append({
                "text": " ".join(x["word"] for x in cur_words),
                "start": cur_words[0]["start"],
                "end": cur_words[-1]["end"],
            })
            cur_words = [w]
            cur_letters = wl
    if cur_words:
        chunks.append({
            "text": " ".join(x["word"] for x in cur_words),
            "start": cur_words[0]["start"],
            "end": cur_words[-1]["end"],
        })
    return chunks
