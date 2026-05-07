"""Expand digit tokens in vo_text to their spoken form before TTS synthesis.

Applied to the text sent to TTS and to plan_words in the aligner — vo_text in
the plan is never mutated (captions read the original form).
"""

from __future__ import annotations

import re

_ONES = [
    'zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine',
    'ten', 'eleven', 'twelve', 'thirteen', 'fourteen', 'fifteen', 'sixteen',
    'seventeen', 'eighteen', 'nineteen',
]
_TENS = ['', '', 'twenty', 'thirty', 'forty', 'fifty', 'sixty', 'seventy', 'eighty', 'ninety']


def _int_to_words(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        ones = n % 10
        return _TENS[n // 10] + ('-' + _ONES[ones] if ones else '')
    if n < 1_000:
        rest = n % 100
        return _ONES[n // 100] + ' hundred' + (' ' + _int_to_words(rest) if rest else '')
    if n < 1_000_000:
        thousands = n // 1_000
        rest = n % 1_000
        return _int_to_words(thousands) + ' thousand' + (' ' + _int_to_words(rest) if rest else '')
    if n < 1_000_000_000:
        millions = n // 1_000_000
        rest = n % 1_000_000
        return _int_to_words(millions) + ' million' + (' ' + _int_to_words(rest) if rest else '')
    billions = n // 1_000_000_000
    rest = n % 1_000_000_000
    return _int_to_words(billions) + ' billion' + (' ' + _int_to_words(rest) if rest else '')


def expand_digits(text: str) -> str:
    """Expand digit tokens in *text* to their spoken form.

    Handles:
    - Currency: ``$99`` → ``ninety-nine dollars``
    - Comma-formatted integers: ``120,000`` → ``one hundred twenty thousand``
    - Bare integers 0–999: ``3`` → ``three``

    Skips:
    - 4-digit numbers (years, model numbers like ``2024``, ``1080``)
    - Numbers adjacent to letters (e.g. ``3D``, ``B2B``, ``GPT4``)
    """
    # 1. Currency — must run first so the $ is consumed before the bare-number pass.
    def _currency(m: re.Match) -> str:
        n = int(m.group(1).replace(',', ''))
        return _int_to_words(n) + (' dollar' if n == 1 else ' dollars')

    text = re.sub(r'\$(\d[\d,]*)', _currency, text)

    # 2. Comma-formatted integers (e.g. 120,000 — always multi-word when spoken).
    def _comma_num(m: re.Match) -> str:
        return _int_to_words(int(m.group(0).replace(',', '')))

    text = re.sub(r'\b\d{1,3}(?:,\d{3})+\b', _comma_num, text)

    # 3. Bare integers 0–999 not adjacent to letters (skips 4-digit years, model numbers).
    def _bare(m: re.Match) -> str:
        return _int_to_words(int(m.group(0)))

    text = re.sub(r'(?<![A-Za-z\d,])\b[0-9]{1,3}\b(?![A-Za-z])', _bare, text)

    return text
