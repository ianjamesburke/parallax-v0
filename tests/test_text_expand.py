"""Unit tests for text_expand.expand_digits."""

from parallax.text_expand import expand_digits, _int_to_words


class TestIntToWords:
    def test_zero(self):
        assert _int_to_words(0) == "zero"

    def test_teens(self):
        assert _int_to_words(13) == "thirteen"
        assert _int_to_words(19) == "nineteen"

    def test_tens(self):
        assert _int_to_words(20) == "twenty"
        assert _int_to_words(99) == "ninety-nine"

    def test_hundreds(self):
        assert _int_to_words(100) == "one hundred"
        assert _int_to_words(120) == "one hundred twenty"
        assert _int_to_words(999) == "nine hundred ninety-nine"

    def test_thousands(self):
        assert _int_to_words(1000) == "one thousand"
        assert _int_to_words(120_000) == "one hundred twenty thousand"
        assert _int_to_words(120_500) == "one hundred twenty thousand five hundred"

    def test_millions(self):
        assert _int_to_words(1_000_000) == "one million"
        assert _int_to_words(2_500_000) == "two million five hundred thousand"

    def test_billions(self):
        assert _int_to_words(1_000_000_000) == "one billion"


class TestExpandDigits:
    def test_comma_number(self):
        assert expand_digits("Over 120,000 people") == "Over one hundred twenty thousand people"

    def test_currency_simple(self):
        assert expand_digits("Costs $99") == "Costs ninety-nine dollars"

    def test_currency_large(self):
        assert expand_digits("Worth $1,000,000") == "Worth one million dollars"

    def test_currency_singular(self):
        assert expand_digits("Just $1") == "Just one dollar"

    def test_bare_small_integer(self):
        assert expand_digits("Only 3 remain") == "Only three remain"

    def test_bare_zero(self):
        assert expand_digits("0 errors") == "zero errors"

    def test_skips_four_digit_year(self):
        assert expand_digits("In 2024 we launched") == "In 2024 we launched"

    def test_skips_letter_adjacent(self):
        assert expand_digits("3D printing") == "3D printing"
        assert expand_digits("B2B sales") == "B2B sales"
        assert expand_digits("GPT4 model") == "GPT4 model"

    def test_mixed(self):
        result = expand_digits("In 2023, 120,000 units sold at $99 each, that is 3 times last year.")
        assert "2023" in result
        assert "one hundred twenty thousand" in result
        assert "ninety-nine dollars" in result
        assert "three" in result

    def test_no_digits(self):
        text = "No numbers here"
        assert expand_digits(text) == text

    def test_bracket_tags_preserved(self):
        result = expand_digits("[pause] Then 5 seconds passed")
        assert "[pause]" in result
        assert "five" in result

    def test_word_count_matches_tts_expectation(self):
        """120,000 expands to 4 words — the core bug this fixes."""
        expanded = expand_digits("120,000")
        assert len(expanded.split()) == 4
