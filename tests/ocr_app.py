"""A program using the `ocr` commands, for the tests: `test ocr ...`, set by `TEST_OCR_*` variables.

It reads its settings as a program would (each from its variable, else its
default; paths relative to the working directory) and tells where each comes
from, and has the fictional documents of `scriptorium.fictional`.
"""

import os
from pathlib import Path
from typing import cast

import typer

from scriptorium import fictional
from scriptorium.commands import make_app
from scriptorium.config import OcrConfig, ProgramNames
from scriptorium.models import DEVICES, ORIENT_MIN_CONFIDENCE, Device

PREFIX = "TEST_OCR_"
NAMES = ProgramNames(
    command="test ocr", settings_prefix=PREFIX, password_variable="TEST_PDF_PASSWORD"
)


def _setting(name: str, default: str) -> str:
    return os.environ.get(PREFIX + name.upper(), default)


def config() -> OcrConfig:
    device = _setting("device", "auto")
    assert device in DEVICES, device
    return OcrConfig(
        ocr_dir=Path(_setting("dir", "data/ocr")).absolute(),
        workers_dir=Path(_setting("workers_dir", "workers")).absolute(),
        documents=fictional.DOCUMENTS,
        check_document=fictional.receipt,
        engines=_setting("engines", ""),
        device=cast(Device, device),
        orient=_setting("orient", ""),
        orient_min_confidence=float(_setting("orient_min_confidence", str(ORIENT_MIN_CONFIDENCE))),
        preprocess=_setting("preprocess", ""),
        min_dpi=int(_setting("min_dpi", "300")),
        max_dpi=int(_setting("max_dpi", "600")),
        worker_pages=int(_setting("worker_pages", "1000")),
        jobs=None if _setting("jobs", "auto") == "auto" else int(_setting("jobs", "")),
        origins={
            name: PREFIX + name.upper()
            for name in ("engines", "device", "orient", "preprocess", "jobs", "workers_dir")
            if PREFIX + name.upper() in os.environ
        },
    )


app = typer.Typer()
app.add_typer(make_app(config, NAMES), name="ocr")
