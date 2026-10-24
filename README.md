# scriptorium

[![CI](https://github.com/kuoris-ix/scriptorium/actions/workflows/ci.yml/badge.svg)](https://github.com/kuoris-ix/scriptorium/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.14+](https://img.shields.io/badge/python-3.14%2B-blue.svg)

English | [日本語](README.ja.md)

Reading scanned documents (images, and PDFs of them) with OCR engines, and keeping what they
read. Made for Japanese documents such as payslips, receipts and tax forms; everything stays on
your machine. scriptorium is a Python library: a project adds its `ocr` commands to its own
command line (a Typer app), and reads what was kept from its own code. To try it, or to compare
engines, the same commands run on their own as `scriptorium`.

- **Engines that don't get in each other's way.** Each engine (YomiToku, PaddleOCR, NDLOCR-Lite,
  Apple Vision, Windows OCR, Tesseract) runs in a Python environment of its own, talked with over
  a small JSON protocol, so engines whose libraries clash work side by side, and switching
  engines is a setting.
- **Nothing is read twice.** What's read is kept page by page, keyed by the file's contents and
  by everything the reading depends on, so a long run can be stopped and goes on where it was.
- **Choosing an engine with evidence.** Fictional documents with their truth to score engines
  on, a diff of what engines read differently on real documents (which have no truth), and the
  engines' results on Windows, macOS and Linux (`docs/benchmark-results.md`).

Status: 0.x. The commands' help and most of `docs/` are in Japanese for now.

## Requirements

- Python 3.14 or later, and [uv](https://docs.astral.sh/uv/)
- The engines' environments: YomiToku, PaddleOCR and NDLOCR-Lite need Python 3.11–3.13 (uv
  fetches it), and several GB of disk for their libraries and models

## Trying it out

The `scriptorium` command has the same commands on their own, with fictional documents to
score engines on. Run it in a clone:

```sh
git clone https://github.com/kuoris-ix/scriptorium
cd scriptorium
uv sync
uv sync --project workers/yomitoku                          # an engine (see "Installing the engines")
uv run scriptorium check-engines                            # does it work?
uv run scriptorium make-samples data/ocr-trial --count 1    # fictional documents, with their truth
uv run scriptorium run data/ocr-trial -e yomitoku           # read them
uv run scriptorium score-engines data/ocr-trial             # how well they were read
```

`uv run scriptorium --help` lists the commands. The settings are `SCRIPTORIUM_*` variables,
taken from the environment, else from `.env` in the working folder; paths are relative to the
working folder. Each command shows first the settings it uses, and where they're from.

| Variable | Default | |
|---|---|---|
| `SCRIPTORIUM_DIR` | `data/ocr` | where what's read is kept |
| `SCRIPTORIUM_WORKERS_DIR` | the clone's `workers/` | the engines' workers |
| `SCRIPTORIUM_ENGINES` | (none) | the engines used when none is named (`-e`), comma-separated |
| `SCRIPTORIUM_DEVICE` | `auto` | `auto`, `cpu`, `cuda` or `mps` |
| `SCRIPTORIUM_ORIENT` | (none) | the engine telling how pages are turned (`tesseract`) |
| `SCRIPTORIUM_ORIENT_MIN_CONFIDENCE` | `5` | pages told with less confidence aren't turned |
| `SCRIPTORIUM_PREPROCESS` | (none) | steps before reading, comma-separated (`flatten,deskew`) |
| `SCRIPTORIUM_MIN_DPI`, `SCRIPTORIUM_MAX_DPI` | `300`, `600` | the resolutions scanned pages are rendered at |
| `SCRIPTORIUM_WORKER_PAGES` | `1000` | a worker is started again after so many pages (`0`: never) |
| `SCRIPTORIUM_JOBS` | `auto` | the workers each engine reads with at once (`--jobs`); `auto`: several only for the engines faster so (`tesseract`, `windows-ocr`), as the cores and memory allow |
| `SCRIPTORIUM_PDF_PASSWORD` | (none) | a protected PDF's password |

## Using it in a project

Depend on a tagged release:

```toml
[project]
dependencies = ["scriptorium @ git+https://github.com/kuoris-ix/scriptorium@v0.3.0"]
```

and add the commands to your Typer app, with your settings (`OcrConfig`), and the command and
variable names to show in help and messages (`ProgramNames`):

```python
from pathlib import Path

import typer

from scriptorium import fictional
from scriptorium.commands import make_app
from scriptorium.config import OcrConfig, ProgramNames

app = typer.Typer()  # your own command line


def config() -> OcrConfig:
    # Read your own settings here (environment, .env, ...); called for each command run.
    return OcrConfig(
        ocr_dir=Path("data/ocr").absolute(),
        workers_dir=Path("~/scriptorium/workers").expanduser(),
        # Fictional documents for `ocr make-samples` (scriptorium's, or your own), and the one
        # `ocr check-engines` reads.
        documents=fictional.DOCUMENTS,
        check_document=fictional.receipt,
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

Everything read stays on your machine, in `OcrConfig.ocr_dir` (`SCRIPTORIUM_DIR`): the text of your documents, in
JSON files. Treat that folder as you treat the scans. scriptorium makes it readable and writable by its
user only (mode 700 on Linux and macOS), but leaves a folder that already exists as it is: on a
shared machine, run `chmod 700` on it. `ocr remove-missing` removes what was read from files
you've deleted.

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
