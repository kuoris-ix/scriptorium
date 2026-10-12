# scriptorium

English | [日本語](README.ja.md)

Reading scanned documents (images, and PDFs of them) with OCR engines, each run in an
environment of its own, and keeping what they read. Made for Japanese documents such as
payslips, receipts and tax forms. scriptorium is a Python library: a project adds its `ocr`
commands to its own command line (a Typer app), and reads what was kept from its own code. It
has no command line of its own yet.

- Several engines side by side (YomiToku, PaddleOCR, NDLOCR-Lite, Apple Vision, Windows OCR,
  Tesseract), each in its own Python environment, talked with over a small JSON protocol.
- What's read is kept page by page, keyed by the file's contents and by everything the reading
  depends on, so a run can be stopped and resumed, and nothing is read twice.
- Tools to choose engines: fictional documents with their truth to score engines on, and a
  diff of what engines read differently on real documents (which have no truth).
- Reading the text layer of PDFs that have one (`scriptorium.pdf`), in the same terms.

Status: 0.x. The commands' help and most of `docs/` are in Japanese for now.

## Requirements

- Python 3.14 or later, and [uv](https://docs.astral.sh/uv/)
- The engines' environments: YomiToku, PaddleOCR and NDLOCR-Lite need Python 3.10–3.13 (uv
  fetches it), and several GB of disk for their libraries and models

## Using it in a project

Depend on a tagged release:

```toml
[project]
dependencies = ["scriptorium @ git+https://github.com/kuoris-ix/scriptorium@v0.1.0"]
```

and add the commands to your Typer app, with your settings (`OcrConfig`), and the command and
variable names to show in help and messages (`ProgramNames`):

```python
from pathlib import Path

from scriptorium.commands import make_app
from scriptorium.config import OcrConfig, ProgramNames


def config() -> OcrConfig:
    # Read your own settings here (environment, .env, ...); called for each command run.
    return OcrConfig(
        ocr_dir=Path("data/ocr").absolute(),
        workers_dir=Path("~/scriptorium/workers").expanduser(),
        documents=MY_SAMPLE_DOCUMENTS,  # fictional documents for `ocr make-samples`
        check_document=MY_SAMPLE_DOCUMENTS["receipt"],  # what `ocr check-engines` reads
        engines="yomitoku-lite",
    )


names = ProgramNames(
    command="myapp ocr", settings_prefix="MYAPP_OCR_", password_variable="MYAPP_PDF_PASSWORD"
)
app.add_typer(make_app(config, names), name="ocr")
```

Then `myapp ocr list-engines`, `myapp ocr run <folder>`, `myapp ocr status <folder>` and so on
(`myapp ocr --help` lists them). `tests/ocr_app.py` is a small program doing this.

## Installing the engines

The workers live in `workers/` of this repository. Clone it once per machine and make the
environment of each engine you want; every project using scriptorium points its
`OcrConfig.workers_dir` there.

```sh
git clone https://github.com/kuoris-ix/scriptorium
uv sync --project scriptorium/workers/yomitoku
```

`ocr list-engines` tells which engines are installed, and `ocr check-engines` has each read a
fictional document to see that it works. Tesseract is installed apart (with its Japanese data);
Apple Vision runs on macOS only, Windows OCR on Windows only. `docs/engine-setup.md` has the
steps.

| Engine | Worker | License of the engine |
|---|---|---|
| `yomitoku`, `yomitoku-lite` | `yomitoku` | CC BY-NC-SA 4.0 (non-commercial use) |
| `paddleocr` | `paddleocr` | Apache-2.0 |
| `ndlocr-lite` | `ndlocr_lite` | CC BY 4.0 |
| `apple-vision` | `apple_vision` | macOS's Vision framework (through ocrmac, MIT) |
| `windows-ocr` | `windows_ocr` | Windows's OCR (through winocr) |
| `tesseract` | `tesseract` | Apache-2.0 |

scriptorium includes none of the engines: each worker's `pyproject.toml` names its engine as a
dependency, installed by `uv sync`. Check an engine's license before using it; YomiToku's
doesn't allow commercial use. Models are downloaded on first use (no images are sent).

## What's kept, and privacy

Everything read stays on your machine, in `OcrConfig.ocr_dir`: the text of your documents, in
JSON files. Treat that folder as you treat the scans. `ocr remove-missing` removes what was
read from files you've deleted.

## Development

```sh
uv sync
uv run ruff check && uv run ruff format --check && uv run mypy && uv run pytest
```

The tests use a fake worker that returns set results instead of a real engine, and fictional
documents made with made-up values; no engine is needed. See
[CONTRIBUTING.md](CONTRIBUTING.md) before sending a change, `docs/spec.md` for the protocol and
the store's format, and `docs/design.md` and `docs/benchmark-results.md` for the design and the
engines' benchmarks (in Japanese for now).

## License

MIT (see `LICENSE`). The engines have licenses of their own (above).
