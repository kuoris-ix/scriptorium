from scriptorium.diff import Agreement, Count, groups
from scriptorium.models import OcrPage, OcrWord
from scriptorium.samples import Truth, TruthLine


def word(text: str, x0: float, x1: float, top: float = 100, bottom: float = 112) -> OcrWord:
    return OcrWord(text, x0, x1, top, bottom)


def page(*words: OcrWord) -> OcrPage:
    return OcrPage(page=1, width=600, height=800, dpi=300, words=list(words))


def test_pieces_in_one_place_are_one_group_whatever_each_engine_s_pieces_are() -> None:
    found = groups(
        {
            # A line as one piece, and as words.
            "lines": page(word("合計 ¥1,234", 10, 120)),
            "words": page(word("合計", 10, 40), word("¥1,", 60, 90), word("234", 90, 120)),
        }
    )

    (group,) = found
    assert group.texts == {"lines": "合計 ¥1,234", "words": "合計¥1,234"}
    assert (group.has_number, group.read_by_all, group.agreed) == (True, True, True)


def test_numbers_read_differently_disagree() -> None:
    (group,) = groups({"a": page(word("¥1,234", 10, 60)), "b": page(word("¥1.284", 12, 62))})

    assert group.has_number
    assert not group.agreed


def test_what_only_some_engines_read_is_told() -> None:
    found = groups(
        {"a": page(word("合計", 10, 40), word("500", 300, 330)), "b": page(word("合計", 10, 40))}
    )

    total, alone = found
    assert (total.agreed, total.has_number) == (True, False)
    assert alone.texts == {"a": "500", "b": ""}
    assert (alone.read_by_all, alone.agreed) == (False, False)


def test_text_is_compared_written_alike() -> None:
    (group,) = groups({"a": page(word("カード 利用", 10, 80)), "b": page(word("ｶｰﾄﾞ利用", 10, 80))})

    assert group.agreed  # half-width katakana and spaces aside


def test_numbers_can_be_compared_by_their_digits_and_signs_only() -> None:
    def pair(a: str, b: str) -> tuple[bool, bool]:
        (group,) = groups({"a": page(word(a, 10, 120)), "b": page(word(b, 12, 122))})
        return group.agreed, group.digits_agreed

    # The text around the numbers aside.
    assert pair("〒000-0000 架空県1-2-3", "デ000-0000 架空県1-2-3") == (False, True)
    assert pair("令和13年9月16日", "今和13年9月16日") == (False, True)
    # Nor a thousands comma read as a period.
    assert pair("194,800", "194.800") == (False, True)
    # Nor a long vowel (a minus as amounts are written) or a period not before a digit.
    assert pair("オリーブ油1/4カップ", "オリ一ブ油1/4カップ") == (False, True)
    assert pair("小さじ2、一味", "小さじ2.一味") == (False, True)
    # Not what changes a value: a decimal point, a minus left out, a digit.
    assert pair("1,5", "1.5") == (False, False)
    assert pair("▲5,960", "5,960") == (False, False)
    assert pair("¥1,234", "\\1234") == (True, True)
    assert pair("0カロリーゼリー", "ロカロリーゼリー") == (False, False)


def test_groups_go_from_the_top_of_the_page_and_count_up() -> None:
    found = groups(
        {
            "a": page(word("2", 10, 20, 300, 312), word("1", 10, 20)),
            "b": page(word("2", 10, 20, 300, 312), word("7", 10, 20)),
        }
    )
    count = Count()
    count.add(found)

    assert [g.texts["a"] for g in found] == ["1", "2"]
    assert (count.pages, count.numbers, count.numbers_agreed, count.numbers_missed) == (1, 2, 1, 0)


def test_counting_by_digits_takes_numbers_alike_whatever_is_around_them() -> None:
    found = groups({"a": page(word("〒000-0000", 10, 90)), "b": page(word("デ000-0000", 10, 90))})
    by_text, by_digits = Count(), Count(digits=True)
    by_text.add(found)
    by_digits.add(found)

    assert (by_text.numbers, by_text.numbers_agreed) == (1, 0)
    assert (by_digits.numbers, by_digits.numbers_agreed) == (1, 1)


TRUTH = Truth(
    file="receipt-1-300.png",
    width=600,
    height=800,
    lines=[
        TruthLine("¥194,800", "amount", 10, 120, 100, 112),
        TruthLine("▲5,960", "amount", 10, 120, 200, 212),
        TruthLine("¥1,234", "amount", 10, 120, 300, 312),
        TruthLine("令和12年5月1日", "date", 10, 120, 400, 412),
        TruthLine("合計", "text", 10, 120, 500, 512),
    ],
)


def read(*texts: str) -> OcrPage:
    """A page with `texts` on the truth's first lines (amounts and the date), in turn."""
    return page(*(word(t, 10, 120, 100 * (i + 1), 100 * (i + 1) + 12) for i, t in enumerate(texts)))


def test_agreement_tells_what_one_engine_reads_wrong_from_what_both_read_wrong_alike() -> None:
    tally = Agreement()
    tally.add(
        TRUTH,
        read("194.800", "5,960", "¥1,234", "令和12年5月1日"),  # a comma for a period, ▲ left out
        read("¥194,800", "5,960", "¥1,234", "令和12年5月1日"),  # ▲ left out too
    )

    assert tally.pages == 1
    assert tally.count("amount") == 3
    assert tally.count("amount", wrong="none") == 1
    assert tally.count("amount", wrong="a", shown=True) == 1  # 194.800 against 194,800
    assert tally.count("amount", wrong="both", shown=False) == 1  # both read 5,960
    assert tally.count("date", wrong="none") == 1


def test_by_digits_agreement_takes_only_what_differs_around_the_numbers_as_right() -> None:
    by_text, by_digits = Agreement(), Agreement(digits=True)
    a, b = read("194.800", "▲5,960", "Y1,234"), read("¥194,800", "▲5,960", "¥1,284")
    by_text.add(TRUTH, a, b)
    by_digits.add(TRUTH, a, b)

    assert by_text.count("amount", wrong="a", shown=True) == 1  # 194.800 isn't ¥194,800
    assert by_text.count("amount", wrong="both", shown=True) == 1  # Y1,234 isn't ¥1,234
    assert by_digits.count("amount", wrong="none") == 2  # but both have its digits
    assert by_digits.count("amount", wrong="b", shown=True) == 1  # 1,284 still differs
