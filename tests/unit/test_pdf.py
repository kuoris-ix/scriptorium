import csv
import json
from pathlib import Path

import pytest
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

from scriptorium import pdf

# Built into reportlab, so the fake PDFs need no font file.
FONT = "HeiseiKakuGo-W5"
pdfmetrics.registerFont(UnicodeCIDFont(FONT))

PAGE_HEIGHT = 842.0


def _write_pdf(
    path: Path,
    pages: list[list[tuple[float, float, str]]],
    *,
    font: str = FONT,
    encrypt: str | None = None,
) -> None:
    """Write a PDF; each word is (x, top, text) with `top` measured from the page top."""
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=(595.0, PAGE_HEIGHT), encrypt=encrypt)
    for words in pages:
        c.setFont(font, 10)
        for x, top, text in words:
            # reportlab draws at the baseline, measured from the page bottom.
            c.drawString(x, PAGE_HEIGHT - top - 10, text)
        c.showPage()
    c.save()


def test_extract_all_returns_words_with_coordinates_per_file_and_page(tmp_path: Path) -> None:
    _write_pdf(tmp_path / "2026" / "b.pdf", [[(50, 100, "基本給"), (150, 100, "300,000")]])
    _write_pdf(tmp_path / "a.PDF", [[(50, 100, "支給")], [(60, 200, "控除")]])
    (tmp_path / "memo.txt").write_text("not a pdf")

    pdfs = pdf.extract_all(tmp_path)

    assert [pdf.file for pdf in pdfs] == ["2026/b.pdf", "a.PDF"]
    a_pages = pdfs[1].pages
    assert [(p.page, [w.text for w in p.words]) for p in a_pages] == [(1, ["支給"]), (2, ["控除"])]
    assert (a_pages[0].width, a_pages[0].height) == (595.0, PAGE_HEIGHT)

    label, amount = pdfs[0].pages[0].words
    assert (label.text, amount.text) == ("基本給", "300,000")
    assert label.x0 == pytest.approx(50)
    assert amount.x0 == pytest.approx(150)
    assert label.top == pytest.approx(amount.top)
    assert label.size == pytest.approx(10)


def test_extract_all_opens_password_protected_pdfs(tmp_path: Path) -> None:
    # A standard font: reportlab's encryption also encrypts the CID font's
    # Registry/Ordering strings, so pdfminer can't map its glyphs back to
    # text in this fixture (real PDFs usually carry a ToUnicode map instead).
    _write_pdf(
        tmp_path / "locked.pdf",
        [[(50, 100, "NET 250,000")]],
        font="Helvetica",
        encrypt="secret",
    )

    (locked,) = pdf.extract_all(tmp_path, password="secret")

    assert [w.text for w in locked.pages[0].words] == ["NET", "250,000"]


def test_write_dump_json_and_csv_hold_the_same_words(tmp_path: Path) -> None:
    _write_pdf(tmp_path / "in" / "p.pdf", [[(50, 100, "健康保険"), (150, 100, "△1,000")]])
    pdfs = pdf.extract_all(tmp_path / "in")

    pdf.write_dump(pdfs, tmp_path / "words.json")
    pdf.write_dump(pdfs, tmp_path / "words.csv")

    data = json.loads((tmp_path / "words.json").read_text(encoding="utf-8"))
    (file,) = data["files"]
    assert file["file"] == "p.pdf"
    assert [w["text"] for w in file["pages"][0]["words"]] == ["健康保険", "△1,000"]

    with (tmp_path / "words.csv").open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert list(rows[0]) == pdf.CSV_COLUMNS
    assert [(r["file"], r["page"], r["text"]) for r in rows] == [
        ("p.pdf", "1", "健康保険"),
        ("p.pdf", "1", "△1,000"),
    ]


def test_write_dump_rejects_unknown_suffix(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unsupported output format"):
        pdf.write_dump([], tmp_path / "words.txt")


def test_extract_all_reads_a_single_file_too(tmp_path: Path) -> None:
    _write_pdf(tmp_path / "in" / "w.pdf", [[(50, 100, "源泉徴収票")]])
    _write_pdf(tmp_path / "in" / "other.pdf", [[(50, 100, "別")]])

    (one,) = pdf.extract_all(tmp_path / "in" / "w.pdf")

    assert (one.file, [w.text for w in one.pages[0].words]) == ("w.pdf", ["源泉徴収票"])
