import pytest

from scriptorium.compare import Tally, distance, normalize, score_page
from scriptorium.models import OcrPage, OcrWord
from scriptorium.samples import Truth, TruthLine

TRUTH = Truth(
    file="receipt-1-300.png",
    width=200,
    height=300,
    lines=[
        TruthLine("合計", "text", 10, 40, 100, 112),
        TruthLine("¥1,234", "amount", 120, 180, 100, 112),
        TruthLine("令和12年5月1日", "date", 10, 100, 50, 62),
    ],
)


def word(text: str, x0: float, x1: float, top: float = 100, bottom: float = 112) -> OcrWord:
    return OcrWord(text, x0, x1, top, bottom, 0.9)


def page(*words: OcrWord) -> OcrPage:
    return OcrPage(page=1, width=200, height=300, dpi=300, words=list(words))


@pytest.mark.parametrize(
    ("text", "kind", "normalized"),
    [
        ("\uffe5\uff11\uff0c\uff12\uff13\uff14", "amount", "1234"),  # full-width ¥1,234
        ("1,234円", "amount", "1234"),
        ("\\1,234", "amount", "1234"),  # a yen sign read as a backslash
        ("△1,200", "amount", "-1200"),
        ("▲ 500", "amount", "-500"),
        ("17-06-28", "date", "17-06-28"),
        ("令和 12年", "date", "令和12年"),
        ("カ ード", "text", "カード"),
        ("ー", "text", "ー"),  # only a minus in numbers
    ],
)
def test_normalize(text: str, kind: str, normalized: str) -> None:
    assert normalize(text, kind) == normalized


def test_distance() -> None:
    assert distance("1234", "1234") == 0
    assert distance("1234", "1284") == 1
    assert distance("1234", "") == 4
    assert distance("カード", "力ード") == 1


def test_what_s_read_right_is_exact_and_pieces_of_a_line_are_joined() -> None:
    score = score_page(
        TRUTH,
        page(
            word("合計", 11, 39),
            word("¥1,", 121, 150),
            word("234", 151, 179),
            word("令和12年", 10, 60, 50, 62),
            word("5月1日", 61, 100, 50, 62),
        ),
    )

    assert [(s.read, s.exact) for s in score.lines] == [
        ("合計", True),
        ("¥1,234", True),
        ("令和12年5月1日", True),
    ]
    assert score.extra == []


def test_wrong_missed_and_extra_text_are_told_apart() -> None:
    score = score_page(TRUTH, page(word("¥1,284", 120, 180), word("印", 150, 190, 200, 240)))
    tally = Tally()
    tally.add(score)

    assert [s.missed for s in score.lines] == [True, False, True]
    assert (tally.missed, tally.extra) == (2, 1)
    assert tally.exact_rate("amount") == 0
    assert tally.wrong_amounts == [("¥1,234", "¥1,284")]
    # 2 + 1 + 9 characters wrong of 2 + 4 + 9.
    assert tally.character_error_rate == pytest.approx(12 / 15)


def test_an_amount_with_a_period_for_a_comma_isn_t_exact_but_has_the_same_value() -> None:
    tally = Tally()
    for read in ("¥1.234", "¥1,284", "1234円"):
        tally.add(score_page(TRUTH, page(word(read, 120, 180))))

    assert tally.exact_by_kind["amount"] == 1  # 1234円
    assert tally.amounts_by_value == 2  # and ¥1.234


def test_a_piece_goes_to_the_line_it_mostly_lies_on() -> None:
    # From the gap between the two bottom lines over the amount's: mostly on the amount's.
    mostly = score_page(TRUTH, page(word("1,234", 70, 180)))
    # Over the amount's and a third of the label's: less than half on either, nor holding both.
    across = score_page(TRUTH, page(word("計1,234", 30, 180)))

    assert [s.read for s in mostly.lines] == ["", "1,234", ""]
    assert [s.read for s in across.lines] == ["", "", ""]
    assert len(across.extra) == 1


def test_a_piece_holding_lines_of_a_row_is_split_between_them() -> None:
    # A line read as one, as NDLOCR-Lite does: the label, the dots and the amount.
    score = score_page(TRUTH, page(word("合計\\1,234", 8, 182)))

    assert [(s.read, s.exact) for s in score.lines] == [
        ("合計", True),
        ("\\1,234", True),
        ("", False),
    ]
    assert score.extra == []


def test_a_row_read_wrong_or_upside_down_is_split_where_it_matches_best() -> None:
    wrong = score_page(TRUTH, page(word("合許\\1,284", 8, 182)))
    # On a page upside down, the row read upright runs right to left on the page.
    upside_down = score_page(TRUTH, page(word("合計¥1,234", 8, 182)))
    tally = Tally()
    tally.add(wrong)

    assert [s.read for s in wrong.lines[:2]] == ["合許", "\\1,284"]
    assert tally.wrong_amounts == [("¥1,234", "\\1,284")]
    assert [s.exact for s in upside_down.lines[:2]] == [True, True]


def test_a_piece_over_two_rows_isn_t_split() -> None:
    # Over the date's line and the label's below it: not one row.
    score = score_page(TRUTH, page(word("令和12年5月1日合計", 8, 102, 48, 114)))

    assert [s.read for s in score.lines] == ["", "", ""]
    assert len(score.extra) == 1


def test_pieces_of_a_line_are_joined_in_the_order_they_were_read() -> None:
    # On a page upside down, a line read upright runs right to left on the page.
    score = score_page(TRUTH, page(word("234", 121, 150), word("¥1,", 151, 179)))

    assert score.lines[1].read == "234¥1,"
    assert not score.lines[1].exact
    upside_down = score_page(TRUTH, page(word("¥1,", 151, 179), word("234", 121, 150)))
    assert upside_down.lines[1].exact
