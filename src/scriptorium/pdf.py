"""Words and their positions from PDF files, and dumps of them.

Documents like payslips don't come out in label-then-amount order with
plain text extraction, so they're parsed from word positions instead. A
dump of those words (`write_dump`) is what a new document's layout is worked
out from; the user redacts it (names, employee IDs, company names) before
sharing it, so it must stay a plain, self-contained file. What OCR reads is
dumped alike (`dump`).
"""

import csv
import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

import pdfplumber

from scriptorium.dump import spreadsheet_text
from scriptorium.files import find_files
from scriptorium.images import largest_image

# pdfminer warns of every font without a FontBBox (some tax forms', say), page after page;
# it reads the characters all the same, so only its errors are shown.
logging.getLogger("pdfminer").setLevel(logging.ERROR)

# Coordinates are PDF points; two decimals is far finer than any layout gap
# and keeps dumps of them readable.
_PRECISION = 2


@dataclass(frozen=True)
class Word:
    """A word as pdfplumber groups it, with `top`/`bottom` measured from the page top."""

    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    fontname: str
    size: float


@dataclass(frozen=True)
class Page:
    page: int  # 1-based
    width: float
    height: float
    words: list[Word]
    # Largest share of the page covered by one image: close to 1 for a
    # scanned page, whose words (if any) come from OCR.
    image_coverage: float = 0.0


@dataclass(frozen=True)
class PdfWords:
    file: str  # path relative to the scanned directory, with forward slashes
    pages: list[Page]


PATTERNS = ("**/*.pdf",)

CSV_COLUMNS = [
    "file",
    "page",
    "page_width",
    "page_height",
    "text",
    "x0",
    "x1",
    "top",
    "bottom",
    "fontname",
    "size",
]


def find_pdfs(pdf_dir: Path) -> list[Path]:
    """Return every PDF under `pdf_dir` (recursively), sorted by path."""
    return find_files(pdf_dir, PATTERNS)


def extract_pdf(
    path: Path,
    *,
    name: str,
    password: str | None = None,
    x_tolerance: float = 3,
    y_tolerance: float = 3,
) -> PdfWords:
    pages = []
    with pdfplumber.open(path, password=password) as pdf:
        for number, page in enumerate(pdf.pages, start=1):
            words = [
                Word(
                    text=w["text"],
                    x0=round(w["x0"], _PRECISION),
                    x1=round(w["x1"], _PRECISION),
                    top=round(w["top"], _PRECISION),
                    bottom=round(w["bottom"], _PRECISION),
                    fontname=w["fontname"],
                    size=round(w["size"], _PRECISION),
                )
                for w in page.extract_words(
                    x_tolerance=x_tolerance,
                    y_tolerance=y_tolerance,
                    extra_attrs=["fontname", "size"],
                )
            ]
            pages.append(
                Page(
                    page=number,
                    width=round(page.width, _PRECISION),
                    height=round(page.height, _PRECISION),
                    words=words,
                    image_coverage=_image_coverage(page),
                )
            )
    return PdfWords(file=name, pages=pages)


def _image_coverage(page: pdfplumber.page.Page) -> float:
    image = largest_image(page)
    return image.coverage if image else 0.0


def extract_all(
    path: Path,
    *,
    password: str | None = None,
    x_tolerance: float = 3,
    y_tolerance: float = 3,
) -> list[PdfWords]:
    """Extract words from the PDF `path`, or from every PDF under the folder `path`.

    `x_tolerance`/`y_tolerance` are pdfplumber's: characters closer than
    this are joined into one word. Lower `x_tolerance` if a label and its
    amount come out as a single word.
    """
    if path.is_file():
        files, base = [path], path.parent
    else:
        files, base = find_pdfs(path), path
    return [
        extract_pdf(
            file,
            name=file.relative_to(base).as_posix(),
            password=password,
            x_tolerance=x_tolerance,
            y_tolerance=y_tolerance,
        )
        for file in files
    ]


def write_dump(pdfs: list[PdfWords], output: Path) -> None:
    """Write the dump as JSON or CSV, chosen by `output`'s suffix."""
    suffix = output.suffix.lower()
    if suffix == ".json":
        write_json(pdfs, output)
    elif suffix == ".csv":
        write_csv(pdfs, output)
    else:
        raise ValueError(f"unsupported output format {output.suffix!r} (use .json or .csv)")


def write_json(pdfs: list[PdfWords], output: Path) -> None:
    """Write one JSON document: files → pages → words."""
    data = {"files": [asdict(pdf) for pdf in pdfs]}
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_csv(pdfs: list[PdfWords], output: Path) -> None:
    """Write one row per word.

    UTF-8 with BOM so Excel opens it with the right encoding, which matters
    since the user redacts this file by hand before sharing it.
    """
    with output.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for pdf in pdfs:
            for page in pdf.pages:
                for word in page.words:
                    row = {
                        "file": pdf.file,
                        "page": page.page,
                        "page_width": page.width,
                        "page_height": page.height,
                        **asdict(word),
                    }
                    writer.writerow(
                        {
                            k: spreadsheet_text(v) if isinstance(v, str) else v
                            for k, v in row.items()
                        }
                    )
