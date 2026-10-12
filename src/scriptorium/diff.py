"""Where engines read the same page differently, for documents without a truth.

For real documents there's nothing to score against: where engines
disagree is where a person should look. The pieces of text engines read
are grouped by where they lie: pieces of different engines that overlap
(by half of the smaller, or more) are in one group, and so is everything
joined to them so. Each engine's pieces in a group are joined in the order
it read them, written alike (as `compare.normalize` does; amounts' way for
a group with a digit), and compared.

Numbers count most (an amount read wrong is a wrong value), so groups with
a digit are told apart from the rest. They can also be compared by their
digits and signs only (`Group.digits_agreed`): what changes a value (a
digit, a minus, a decimal point) still counts, the text around the numbers
(〒 read as デ, a kanji of an address) doesn't, and neither does a thousands
comma read as a period (194.800 for 194,800; see `amounts`). A minus or a
decimal point counts only before a digit: amounts' way takes a long vowel
(ー, as in オリーブ) for a minus, and a period may be a comma read so.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Literal

from scriptorium.amounts import without_thousands_periods
from scriptorium.compare import normalize, score_page
from scriptorium.models import OcrPage, OcrWord
from scriptorium.samples import Truth

# Pieces of different engines overlapping by this share of the smaller one are grouped.
MIN_SHARE = 0.5


@dataclass(frozen=True)
class Group:
    """Text that lies in one place, as each engine read it ("" where it read none)."""

    x0: float
    top: float
    texts: dict[str, str]

    @property
    def has_number(self) -> bool:
        return any(c.isdigit() for text in self.texts.values() for c in text)

    @property
    def read_by_all(self) -> bool:
        return all(text.strip() for text in self.texts.values())

    @property
    def agreed(self) -> bool:
        kind = "amount" if self.has_number else "text"
        return len({normalize(text, kind) for text in self.texts.values()}) == 1

    @property
    def digits_agreed(self) -> bool:
        """Read alike by the digits and signs (a group without a digit: as `agreed`)."""
        if not self.has_number:
            return self.agreed
        return len({digits(text) for text in self.texts.values()}) == 1


def digits(text: str) -> str:
    """The digits of `text` and the signs that change a number's value (minus signs, decimal
    points, before a digit), written as amounts are (so a thousands comma, or a period read
    for one, or ¥ doesn't count)."""
    written = without_thousands_periods(normalize(text, "amount"))
    return "".join(
        c for c, after in pairwise(written + " ") if c.isdigit() or (c in "-." and after.isdigit())
    )


def groups(readings: dict[str, OcrPage]) -> list[Group]:
    """The groups of what each engine (by name) read on one page, from the top of the page."""
    pieces = [
        (engine, index, word)
        for engine, page in readings.items()
        for index, word in enumerate(page.words)
        if word.text.strip()
    ]
    parent = list(range(len(pieces)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, (engine_a, _, a) in enumerate(pieces):
        for j in range(i + 1, len(pieces)):
            engine_b, _, b = pieces[j]
            if engine_a != engine_b and _overlap(a, b) >= MIN_SHARE:
                parent[root(i)] = root(j)

    members: dict[int, list[int]] = {}
    for i in range(len(pieces)):
        members.setdefault(root(i), []).append(i)
    found = []
    for indexes in members.values():
        words = [pieces[i][2] for i in indexes]
        texts = {}
        for engine in readings:
            # In the order the engine read them.
            mine = sorted((pieces[i][1], pieces[i][2]) for i in indexes if pieces[i][0] == engine)
            texts[engine] = "".join(word.text for _, word in mine)
        found.append(Group(min(w.x0 for w in words), min(w.top for w in words), texts))
    return sorted(found, key=lambda g: (round(g.top), g.x0))


def _overlap(a: OcrWord, b: OcrWord) -> float:
    """How much `a` and `b` overlap, as a share of the smaller."""
    width = min(a.x1, b.x1) - max(a.x0, b.x0)
    height = min(a.bottom, b.bottom) - max(a.top, b.top)
    if width <= 0 or height <= 0:
        return 0.0
    smaller = min((a.x1 - a.x0) * (a.bottom - a.top), (b.x1 - b.x0) * (b.bottom - b.top))
    return width * height / smaller if smaller > 0 else 0.0


@dataclass
class Count:
    """Groups of the pages compared, added up."""

    digits: bool = False  # numbers taken as alike by their digits and signs (`digits_agreed`)
    pages: int = 0
    numbers: int = 0  # groups with a digit
    numbers_agreed: int = 0
    numbers_missed: int = 0  # read by some engines only
    texts: int = 0
    texts_agreed: int = 0

    def add(self, found: Sequence[Group]) -> None:
        self.pages += 1
        for group in found:
            if group.has_number:
                self.numbers += 1
                self.numbers_agreed += group.digits_agreed if self.digits else group.agreed
                self.numbers_missed += not group.read_by_all
            else:
                self.texts += 1
                self.texts_agreed += group.agreed


# Which of two engines read a line wrong.
Wrong = Literal["none", "a", "b", "both"]
_WRONG: dict[tuple[bool, bool], Wrong] = {
    (True, True): "none",
    (False, True): "a",
    (True, False): "b",
    (False, False): "both",
}


@dataclass
class Agreement:
    """How two engines read the amounts and dates of fictional samples, against their truth.

        For each amount and date: which of the engines read it wrong, and whether they read it
        differently (shown by `ocr diff-engines`, for a person to look at) or alike (an error
        cross-checking can't find). Line by line, where `compare` places what's read: a little
        kinder than `groups`, which goes by where the pieces lie, so an upper bound of what's found.
        By digits, a reading is also right or wrong by its digits and signs (a missing yen sign or
        a thousands period isn't an error to look for). What only one engine reads wrong is always
    shown; what both read wrong alike (`wrong="both", shown=False`) is all cross-checking misses.
    """

    digits: bool = False  # compared by their digits and signs only (as --digits)
    pages: int = 0
    lines: Counter[tuple[str, Wrong, bool]] = field(default_factory=Counter)  # kind, wrong, shown

    def add(self, truth: Truth, a: OcrPage, b: OcrPage) -> None:
        """Add a page of `truth`, as engine a and engine b read it."""
        self.pages += 1
        for line_a, line_b in zip(
            score_page(truth, a).lines, score_page(truth, b).lines, strict=True
        ):
            kind = line_a.truth.kind
            if kind not in ("amount", "date"):
                continue
            truth_written = self._written(line_a.truth.text, kind)
            written_a = self._written(line_a.read, kind)
            written_b = self._written(line_b.read, kind)
            wrong = _WRONG[written_a == truth_written, written_b == truth_written]
            self.lines[kind, wrong, written_a != written_b] += 1

    def _written(self, text: str, kind: str) -> str:
        return digits(text) if self.digits else normalize(text, kind)

    def count(self, kind: str, *, wrong: Wrong | None = None, shown: bool | None = None) -> int:
        return sum(
            n
            for (k, w, s), n in self.lines.items()
            if k == kind and wrong in (None, w) and shown in (None, s)
        )
