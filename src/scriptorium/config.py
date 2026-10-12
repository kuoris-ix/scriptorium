"""The settings the `ocr` commands use (see `commands`), as the program using them gives them.

The commands don't read any settings themselves: the program they're part of
reads its own (say, from `MYAPP_OCR_*` variables) and gives them here, with
where each comes from, to show with it; and tells how it names them
(`ProgramNames`), for the help and messages.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from scriptorium.models import ORIENT_MIN_CONFIDENCE, Device
from scriptorium.samples import Document


@dataclass(frozen=True)
class OcrConfig:
    ocr_dir: Path  # what's read is kept here (see `store`)
    workers_dir: Path  # the engines' workers (see `engines`)
    documents: Mapping[str, Document]  # the fictional documents of `make-samples`
    check_document: Document  # the one `check-engines` has engines read
    engines: str = ""  # used when none is named, comma-separated
    device: Device = "auto"
    orient: str = ""  # the engine telling how pages are turned; none if empty
    orient_min_confidence: float = ORIENT_MIN_CONFIDENCE
    preprocess: str = ""  # comma-separated steps; none if empty
    min_dpi: int = 300
    max_dpi: int = 600
    worker_pages: int = 1000  # a worker is started again after so many pages (0: never)
    # Where `engines`, `device`, `orient` and `preprocess` come from, to show with them
    # (e.g. "MYAPP_OCR_ENGINES in .env"); "default" if not given.
    origins: Mapping[str, str] = field(default_factory=dict)

    def origin(self, name: str) -> str:
        return self.origins.get(name, "default")


@dataclass(frozen=True)
class ProgramNames:
    """How the program names the `ocr` commands and their settings, to tell in their help and
    messages."""

    # How the commands are run, e.g. "myapp ocr".
    command: str = "ocr"
    # What the settings' environment variables start with: "MYAPP_OCR_" names the setting of
    # `OcrConfig.engines` MYAPP_OCR_ENGINES (and so on: _DIR, _ORIENT, _PREPROCESS, _DEVICE).
    settings_prefix: str = "OCR_"
    # The environment variable a protected PDF's password is read from.
    password_variable: str = "PDF_PASSWORD"

    def fill(self, text: str) -> str:
        """`text` with "{command}", "{settings}" and "{password}" in it replaced by the names."""
        for placeholder, name in (
            ("{command}", self.command),
            ("{settings}", self.settings_prefix),
            ("{password}", self.password_variable),
        ):
            text = text.replace(placeholder, name)
        return text
