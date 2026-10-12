"""Amounts of yen as OCR engines read them: written alike, and read as values.

Engines write an amount's signs in many ways: a minus as △, ▲, a dash or the
katakana long vowel mark, the yen sign as ¥, a backslash (the yen sign of
Japanese encodings) or 円 (`MINUS` and `NOT_IN_AMOUNTS` write them alike,
as `compare.normalize` does).

They also read a thousands comma as a period now and then (yomitoku reads
194,800 as 194.800). Digits with a period every three are taken as with
commas (`without_thousands_periods`), for amounts of yen only: they have no
decimals, and a price of 29.80 or a date of 2026.10.09 doesn't look so, but
a quantity of 1.500 would.
"""

import re
import unicodedata

# What's written for a minus: △ and ▲, and the minus sign, hyphens, dashes, the
# full-width hyphen-minus and the katakana long vowel mark (by code point: they
# look like "-").
_DASHES = (0x2212, 0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015, 0xFF0D, 0x30FC)
MINUS = str.maketrans(dict.fromkeys(["△", "▲", *map(chr, _DASHES)], "-"))
# Tesseract and others read ¥ as a backslash (the yen sign of Japanese encodings).
NOT_IN_AMOUNTS = str.maketrans(dict.fromkeys("¥\\円,", ""))
# Digits with a period every three (1.234, 12.345.678), not next to other digits or periods.
_THOUSANDS_PERIODS = re.compile(r"(?<![\d.])\d{1,3}(?:\.\d{3})+(?![\d.])")


def without_thousands_periods(text: str) -> str:
    """`text` without the periods of digits grouped by three (as thousands commas)."""
    return _THOUSANDS_PERIODS.sub(lambda m: m[0].replace(".", ""), text)


def read_yen(text: str) -> int | None:
    """The amount of yen `text` says (e.g. "▲¥1,234円" is -1234), or None if it doesn't
    say one: a number with nothing else but the signs above (see the module's description)."""
    text = "".join(unicodedata.normalize("NFKC", text).split())
    text = without_thousands_periods(text.translate(MINUS).translate(NOT_IN_AMOUNTS))
    return int(text) if re.fullmatch(r"-?\d+", text) else None
