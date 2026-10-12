"""Scoring what OCR engines read against the truth of fictional samples (see `samples`).

Engines split text differently (a line, a word, a character or two), so
the truth's lines are the unit: each piece of text an engine read goes to
the truth line it mostly lies on, and what a line read as is its pieces
joined in the order the engine read them (along the line as the engine
saw it, which on a page upside down runs right to left). A piece holding
several truth lines of one row (an engine reading "合計 …… ¥1,234" as one
line, as NDLOCR-Lite does) is split between them, along the way its text
matches theirs best. Both sides are written alike before comparing: NFKC
(full-width to half-width), no spaces, and for numbers one minus sign (for
△, ▲ and the dashes) and, for amounts, no ¥, 円 or thousands commas.

Amounts and dates count most: a single character wrong there is a wrong
value, so they're scored as read exactly or not, besides the character
error rate over all the text. Amounts are also scored by their value, as
an import would read them (`amounts.read_yen`: a thousands comma read as a
period still says the same amount).
"""

import unicodedata
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from scriptorium.amounts import MINUS, NOT_IN_AMOUNTS, read_yen
from scriptorium.models import EngineInfo, OcrPage, OcrWord, StoredPage
from scriptorium.samples import Truth, TruthLine

# A piece of text belongs to the truth line holding at least this share of it.
MIN_SHARE = 0.5
# The truth lines' boxes are widened by this share of their height (engines' boxes are looser).
MARGIN = 0.25


def normalize(text: str, kind: str) -> str:
    """`text` written alike for comparing (see the module's description)."""
    text = "".join(unicodedata.normalize("NFKC", text).split())
    if kind in ("amount", "date", "number"):
        text = text.translate(MINUS)
    if kind == "amount":
        text = text.translate(NOT_IN_AMOUNTS)
    return text


def distance(a: str, b: str) -> int:
    """The edit distance (Levenshtein) between `a` and `b`."""
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


@dataclass(frozen=True)
class LineScore:
    truth: TruthLine
    read: str  # what the engine read on the line, joined (as read, not normalized)
    distance: int  # between both normalized
    length: int  # of the truth normalized

    @property
    def exact(self) -> bool:
        return self.distance == 0

    @property
    def missed(self) -> bool:
        return not self.read.strip()

    @property
    def same_value(self) -> bool:
        """Whether what was read says the truth's amount of yen (for an amount only)."""
        value = read_yen(self.truth.text)
        return value is not None and read_yen(self.read) == value


@dataclass(frozen=True)
class PageScore:
    lines: list[LineScore]
    extra: list[OcrWord]  # read where the truth has no text (e.g. a seal or a rule)


def score_page(truth: Truth, page: OcrPage) -> PageScore:
    """Score what was read on `page` against `truth` (of the same page)."""
    # The truth's points and the page's may differ slightly (rounding of sizes).
    sx, sy = page.width / truth.width, page.height / truth.height
    boxes = [(line.x0 * sx, line.top * sy, line.x1 * sx, line.bottom * sy) for line in truth.lines]
    on_line: list[list[str]] = [[] for _ in truth.lines]
    extra = []
    for word in page.words:
        row = _row_held(word, boxes)
        if row:
            texts = [normalize(truth.lines[i].text, "text") for i in row]
            for index, part in zip(row, _split(normalize(word.text, "text"), texts), strict=True):
                on_line[index].append(part)
            continue
        best, share = _best_line(word, boxes)
        if best is None or share < MIN_SHARE:
            extra.append(word)
        else:
            on_line[best].append(word.text)
    lines = []
    for line, parts in zip(truth.lines, on_line, strict=True):
        read = "".join(parts)
        expected = normalize(line.text, line.kind)
        lines.append(
            LineScore(
                truth=line,
                read=read,
                distance=distance(expected, normalize(read, line.kind)),
                length=len(expected),
            )
        )
    return PageScore(lines=lines, extra=extra)


def _best_line(
    word: OcrWord, boxes: Sequence[tuple[float, float, float, float]]
) -> tuple[int | None, float]:
    area = max(word.x1 - word.x0, 0.01) * max(word.bottom - word.top, 0.01)
    best, share = None, 0.0
    for index, (x0, top, x1, bottom) in enumerate(boxes):
        margin = MARGIN * min(x1 - x0, bottom - top)
        width = min(word.x1, x1 + margin) - max(word.x0, x0 - margin)
        height = min(word.bottom, bottom + margin) - max(word.top, top - margin)
        if width > 0 and height > 0 and width * height / area > share:
            best, share = index, width * height / area
    return best, share


Box = tuple[float, float, float, float]  # x0, top, x1, bottom


def _row_held(word: OcrWord, boxes: Sequence[Box]) -> list[int]:
    """The truth lines of one row that `word` mostly holds (two at least), along the row.

    Which way along it the engine read them is told by its text (`_split`).
    """
    held = []
    for index, (x0, top, x1, bottom) in enumerate(boxes):
        margin = MARGIN * min(x1 - x0, bottom - top)
        width = min(word.x1, x1 + margin) - max(word.x0, x0 - margin)
        height = min(word.bottom, bottom + margin) - max(word.top, top - margin)
        area = max(x1 - x0, 0.01) * max(bottom - top, 0.01)
        if width > 0 and height > 0 and width * height / area >= MIN_SHARE:
            held.append(index)
    if len(held) < 2:
        return []
    across = word.x1 - word.x0 >= word.bottom - word.top  # else a column (a page turned)
    start, end = (1, 3) if across else (0, 2)  # the other way: tops and bottoms, or x0s and x1s
    if max(boxes[i][start] for i in held) >= min(boxes[i][end] for i in held):
        return []  # not one row
    return sorted(held, key=lambda i: boxes[i][0 if across else 1])


def _split(text: str, parts: list[str]) -> list[str]:
    """`text` cut into what it read of each of `parts`, read one after the other.

    Along the alignment of `text` with `parts` joined that has the fewest
    edits, in the order (or the reverse order, as on a page upside down) of
    the fewer; what's read between two parts goes with the one before.
    """
    forward = _aligned(text, parts)
    backward = _aligned(text, parts[::-1])
    if backward[0] < forward[0]:
        return backward[1][::-1]
    return forward[1]


def _aligned(text: str, parts: list[str]) -> tuple[int, list[str]]:
    target = "".join(parts)
    owner = [index for index, part in enumerate(parts) for _ in part]
    rows, cols = len(target) + 1, len(text) + 1
    cost = [[0] * cols for _ in range(rows)]
    for i in range(rows):
        cost[i][0] = i
    for j in range(cols):
        cost[0][j] = j
    for i in range(1, rows):
        for j in range(1, cols):
            cost[i][j] = min(
                cost[i - 1][j] + 1,
                cost[i][j - 1] + 1,
                cost[i - 1][j - 1] + (target[i - 1] != text[j - 1]),
            )
    read: list[list[str]] = [[] for _ in parts]
    i, j = rows - 1, cols - 1
    while j > 0:
        if i > 0 and cost[i][j] == cost[i - 1][j - 1] + (target[i - 1] != text[j - 1]):
            read[owner[i - 1]].append(text[j - 1])
            i, j = i - 1, j - 1
        elif i > 0 and cost[i][j] == cost[i - 1][j] + 1:
            i -= 1  # a character of the parts not read
        else:
            read[owner[i - 1] if i > 0 else 0].append(text[j - 1])  # read in between
            j -= 1
    return cost[-1][-1], ["".join(reversed(chars)) for chars in read]


@dataclass
class Tally:
    """Scores of pages added up: one engine's, or one variant's of it."""

    pages: int = 0
    lines: int = 0
    characters: int = 0
    errors: int = 0  # edit distance, summed
    missed: int = 0
    extra: int = 0
    by_kind: Counter[str] = field(default_factory=Counter)  # lines of each kind
    exact_by_kind: Counter[str] = field(default_factory=Counter)
    amounts_by_value: int = 0  # amounts saying the truth's value (`LineScore.same_value`)
    wrong_amounts: list[tuple[str, str]] = field(default_factory=list)  # (truth, read)
    wrong_dates: list[tuple[str, str]] = field(default_factory=list)

    def add(self, score: PageScore) -> None:
        self.pages += 1
        self.extra += len(score.extra)
        for line in score.lines:
            self.lines += 1
            self.characters += line.length
            self.errors += line.distance
            self.missed += line.missed
            kind = line.truth.kind
            self.by_kind[kind] += 1
            self.exact_by_kind[kind] += line.exact
            if kind == "amount":
                self.amounts_by_value += line.same_value
            if not line.exact and not line.missed:
                if kind == "amount":
                    self.wrong_amounts.append((line.truth.text, line.read))
                elif kind == "date":
                    self.wrong_dates.append((line.truth.text, line.read))

    @property
    def character_error_rate(self) -> float:
        return self.errors / self.characters if self.characters else 0.0

    def exact_rate(self, kind: str) -> float | None:
        count = self.by_kind[kind]
        return self.exact_by_kind[kind] / count if count else None


def variant_of(name: str) -> str:
    """The variant of a sample by its name (<document>-<number>-<variant>.<suffix>)."""
    parts = Path(name).stem.split("-", 2)
    return parts[2] if len(parts) == 3 else "?"


@dataclass
class EngineScore:
    """How one engine (with its settings) read the samples."""

    engine: str
    info: EngineInfo | None = None  # as the first page scored was read
    total: Tally = field(default_factory=Tally)
    by_variant: dict[str, Tally] = field(default_factory=dict)
    unread: int = 0
    seconds: list[float] = field(default_factory=list)

    @property
    def seconds_a_page(self) -> float | None:
        return sum(self.seconds) / len(self.seconds) if self.seconds else None


def score_engine(
    engine: str,
    samples: Sequence[tuple[str, Truth]],
    load: Callable[[int], StoredPage | None],
) -> EngineScore:
    """Score `engine` on `samples` (name and truth each), loading the n-th sample's page read."""
    score = EngineScore(engine)
    for index, (name, truth) in enumerate(samples):
        stored = load(index)
        if stored is None:
            score.unread += 1
            continue
        score.info = score.info or stored.engine
        if stored.seconds is not None:
            score.seconds.append(stored.seconds)
        page = score_page(truth, stored.page)
        score.total.add(page)
        score.by_variant.setdefault(variant_of(name), Tally()).add(page)
    return score


def _rate(tally: Tally, kind: str) -> str:
    count = tally.by_kind[kind]
    return f"{tally.exact_by_kind[kind] / count:.1%}" if count else "-"


def _value_rate(tally: Tally) -> str:
    count = tally.by_kind["amount"]
    return f"{tally.amounts_by_value / count:.1%}" if count else "-"


def markdown(scores: Sequence[EngineScore], settings: str, environment: str) -> str:
    """The scores as Markdown tables, to paste into docs/benchmark-results.ja.md.

    The tables are in Japanese, as the docs are.
    """
    lines = [
        f"- 環境: {environment}",
        f"- 設定: {settings}",
        "",
        "| エンジン | 版（モデル） | 装置 | 金額の完全一致 | 金額の値の一致 | 日付の完全一致 "
        "| 文字の誤り率 | 読み落とした行 | 1 ページの時間 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for s in scores:
        info = s.info
        version = (
            f"{info.version}（{info.model}）"
            if info and info.model
            else (info.version if info else "-")
        )
        seconds = s.seconds_a_page
        t = s.total
        lines.append(
            f"| {s.engine} | {version} | {(info.device if info else None) or '-'} "
            f"| {_rate(t, 'amount')} | {_value_rate(t)} | {_rate(t, 'date')} "
            f"| {t.character_error_rate:.1%} "
            f"| {t.missed}/{t.lines} | {f'{seconds:.1f} 秒' if seconds is not None else '-'} |"
        )
    variants = sorted({v for s in scores for v in s.by_variant})
    lines += [
        "",
        "劣化ごとの金額 / 日付の完全一致:",
        "",
        "| 劣化 | " + " | ".join(s.engine for s in scores) + " |",
        "|---|" + "---|" * len(scores),
    ]
    for variant in variants:
        cells = []
        for s in scores:
            tally = s.by_variant.get(variant)
            cells.append(f"{_rate(tally, 'amount')} / {_rate(tally, 'date')}" if tally else "-")
        lines.append(f"| `{variant}` | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"
