"""Dumps of what OCR engines read, for working out a document's layout (as `pdf` dumps do).

The engines' own extra output isn't dumped: it's kept in the store, and a
dump is meant to be redacted and shared.
"""

import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from scriptorium.models import StoredPage

CSV_COLUMNS = [
    "file",
    "engine",
    "page",
    "page_width",
    "page_height",
    "dpi",
    "text",
    "x0",
    "x1",
    "top",
    "bottom",
    "confidence",
]


@dataclass(frozen=True)
class FileRead:
    file: str
    engine: str
    pages: list[StoredPage]


def write_dump(files: list[FileRead], output: Path) -> None:
    """Write the dump as JSON or CSV, chosen by `output`'s suffix."""
    suffix = output.suffix.lower()
    if suffix == ".json":
        _write_json(files, output)
    elif suffix == ".csv":
        _write_csv(files, output)
    else:
        raise ValueError(f"unsupported output format {output.suffix!r} (use .json or .csv)")


def _write_json(files: list[FileRead], output: Path) -> None:
    data = {
        "files": [
            {
                "file": f.file,
                "engine": f.engine,
                "pages": [
                    {**asdict(p.page), "engine": asdict(p.engine), "seconds": p.seconds}
                    for p in f.pages
                ],
            }
            for f in files
        ]
    }
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_csv(files: list[FileRead], output: Path) -> None:
    # UTF-8 with BOM, so that Excel opens it right (it's redacted by hand before sharing).
    with output.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for file in files:
            for stored in file.pages:
                page = stored.page
                for word in page.words:
                    writer.writerow(
                        {
                            "file": file.file,
                            "engine": file.engine,
                            "page": page.page,
                            "page_width": page.width,
                            "page_height": page.height,
                            "dpi": page.dpi,
                            **asdict(word),
                        }
                    )
