import pytest

from scriptorium.amounts import read_yen, without_thousands_periods


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("¥1,234", 1234),
        ("1,234円", 1234),
        ("\uffe5\uff13\uff17\uff0c\uff18\uff11\uff18", 37818),  # full-width ¥37,818
        ("\\3,438", 3438),  # ¥ as a backslash
        ("¥16, 619", 16619),  # a space in it
        ("0", 0),
        ("▲5,960", -5960),
        ("△1,980", -1980),
        ("-20", -20),
        ("ー40", -40),  # the long vowel mark read for a minus
        # A thousands comma read as a period.
        ("194.800", 194800),
        ("¥1.234.567", 1234567),
    ],
)
def test_reads_amounts_of_yen_as_engines_write_them(text: str, value: int) -> None:
    assert read_yen(text) == value


@pytest.mark.parametrize(
    "text",
    [
        "",
        "合計",
        "29.80",  # a price with decimals
        "2026.10.09",  # a date
        "1.2345",  # digits after the period not by three
        "1,234.567",  # a comma and a period
        "¥915*",  # something else read in it
    ],
)
def test_what_isn_t_an_amount_of_yen_is_none(text: str) -> None:
    assert read_yen(text) is None


def test_only_periods_grouping_digits_by_three_are_left_out() -> None:
    text = "194.800 と 29.80 と 2026.10.09"

    assert without_thousands_periods(text) == "194800 と 29.80 と 2026.10.09"
