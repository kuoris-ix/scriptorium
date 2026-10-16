import csv
import json
from pathlib import Path

import pytest

from scriptorium.dump import FileRead, spreadsheet_text, write_dump
from scriptorium.models import EngineInfo, OcrPage, OcrWord, StoredPage


@pytest.mark.parametrize(
    "text",
    ["=1+2", "+81 00-0000-0000", "-2+3", "@A1", "\t=1", "\r=1"],
)
def test_spreadsheet_text_marks_what_would_be_a_formula(text: str) -> None:
    assert spreadsheet_text(text) == "'" + text


@pytest.mark.parametrize(
    "text",
    ["-1,200", "+300", "-0.5", "1,000", "合計", "", "a=b"],
)
def test_spreadsheet_text_leaves_the_rest(text: str) -> None:
    assert spreadsheet_text(text) == text


def _read(text: str, amount: str) -> list[FileRead]:
    words = [OcrWord(text, 10.0, 60.0, 20.0, 30.0, 0.9), OcrWord(amount, 70.0, 120.0, 20.0, 30.0)]
    page = OcrPage(page=1, width=595.0, height=842.0, dpi=300.0, words=words)
    stored = StoredPage(page=page, engine=EngineInfo(name="fictional", version="1.0"))
    return [FileRead(file="sample.pdf", engine="fictional", pages=[stored])]


def test_write_dump_csv_keeps_text_from_being_a_formula(tmp_path: Path) -> None:
    formula = '=HYPERLINK("https://example.invalid","x")'

    write_dump(_read(formula, "-1,200"), tmp_path / "words.csv")

    with (tmp_path / "words.csv").open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert [r["text"] for r in rows] == ["'" + formula, "-1,200"]
    assert [r["x0"] for r in rows] == ["10.0", "70.0"]


def test_write_dump_json_keeps_the_text_as_it_is(tmp_path: Path) -> None:
    formula = '=HYPERLINK("https://example.invalid","x")'

    write_dump(_read(formula, "-1,200"), tmp_path / "words.json")

    data = json.loads((tmp_path / "words.json").read_text(encoding="utf-8"))
    words = data["files"][0]["pages"][0]["words"]
    assert [w["text"] for w in words] == [formula, "-1,200"]
