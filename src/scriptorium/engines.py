"""The OCR engines scriptorium can use, each by name.

An engine runs as a worker in an environment of its own (its folder under
the workers' folder, `OcrConfig.workers_dir`, with its own Python and
libraries), since the engines' libraries don't support this package's Python
yet and would clash with each other; see workers/ocr_worker.py for how it's talked with.
Several engines may share a worker, with different options.

To add an engine, add its worker's folder (a pyproject.toml and a
worker.py) and a line here.
"""

import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PLATFORM_NAMES = {"darwin": "macOS", "win32": "Windows", "linux": "Linux"}


@dataclass(frozen=True)
class Engine:
    name: str
    worker: str  # its folder under the workers' folder
    description: str
    platforms: tuple[str, ...] = ()  # the `sys.platform`s it runs on; any if empty
    options: Mapping[str, Any] = field(default_factory=dict)  # passed to the worker
    # Raised when what the worker reads changes (e.g. how its results are
    # converted), to have the pages read again; see `store.RunKey`.
    revision: int = 1
    # Whether several workers read faster, each using about a core (see `jobs`). The others
    # read fastest with one: it uses every core (models run with ONNX Runtime or PyTorch on
    # the CPU), or the GPU.
    parallel: bool = False
    memory: float = 0.5  # GB a worker uses at most, about (of a parallel one)

    def folder(self, workers_dir: Path) -> Path:
        return workers_dir / self.worker

    def unavailable(self, workers_dir: Path, platform: str = sys.platform) -> str | None:
        """Why the engine can't run here, or None."""
        if self.platforms and platform not in self.platforms:
            names = ", ".join(PLATFORM_NAMES.get(p, p) for p in self.platforms)
            return f"runs on {names} only"
        if not (self.folder(workers_dir) / "worker.py").is_file():
            return f"no worker at {self.folder(workers_dir)}"
        return None

    def installed(self, workers_dir: Path) -> bool:
        """Whether its environment was made (`uv sync --project <its folder>`)."""
        return (self.folder(workers_dir) / ".venv").is_dir()


ENGINES: tuple[Engine, ...] = (
    Engine(
        "yomitoku",
        "yomitoku",
        "YomiToku: Japanese documents, with tables and layout (GPU: CUDA)",
    ),
    Engine(
        "yomitoku-lite",
        "yomitoku",
        "YomiToku's lighter models, for the CPU",
        options={"lite": True},
    ),
    Engine("paddleocr", "paddleocr", "PaddleOCR (PP-OCRv6), many languages (GPU: CUDA)"),
    Engine("ndlocr-lite", "ndlocr_lite", "NDLOCR-Lite of the National Diet Library, on the CPU"),
    Engine(
        "apple-vision",
        "apple_vision",
        "The Vision framework built into macOS",
        platforms=("darwin",),
    ),
    Engine(
        "windows-ocr",
        "windows_ocr",
        "The OCR built into Windows (Windows.Media.Ocr)",
        platforms=("win32",),
        parallel=True,
        memory=1.0,
    ),
    Engine(
        "tesseract",
        "tesseract",
        "Tesseract (installed apart), as a baseline",
        parallel=True,
        memory=0.5,
    ),
)


def find(name: str) -> Engine:
    """The engine named `name`; raises KeyError (listing the names) if there's none."""
    for engine in ENGINES:
        if engine.name == name:
            return engine
    raise KeyError(f"no OCR engine {name!r} (use one of: {', '.join(e.name for e in ENGINES)})")
