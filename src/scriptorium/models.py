"""What an OCR engine reads from a page, in the same terms as `pdf.Word`."""

from dataclasses import dataclass, field
from typing import Any, Literal, get_args


@dataclass(frozen=True)
class OcrWord:
    """A piece of text as the engine groups it (a word, a line, ...).

    Positions are in PDF points with `top`/`bottom` measured from the page
    top, as in `pdf.Word`; for an image file, its size in points follows
    from its resolution (see `raster`).
    """

    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    confidence: float | None = None  # 0 to 1; None if the engine has none


@dataclass(frozen=True)
class OcrPage:
    page: int  # 1-based
    width: float  # points
    height: float
    dpi: float  # resolution of the image the engine read
    words: list[OcrWord]


@dataclass(frozen=True)
class EngineInfo:
    """Which engine read a page, as its worker tells."""

    name: str
    version: str
    model: str | None = None
    device: str | None = None  # cpu, cuda, mps, ...


@dataclass(frozen=True)
class StoredPage:
    """A page as kept (see `store`): what was read, and how."""

    page: OcrPage
    engine: EngineInfo
    seconds: float | None = None  # how long the engine took
    extra: dict[str, Any] = field(default_factory=dict)  # engine-specific, kept as it came


# Where engines run: auto (CUDA, else Apple's MPS, else the CPU), cpu, cuda or mps.
Device = Literal["auto", "cpu", "cuda", "mps"]
DEVICES: tuple[str, ...] = get_args(Device)

# How sure the engine telling pages' turns must be to have a page turned (Tesseract's measure,
# see docs/benchmark-results.ja.md: pages to turn were told at 6.7 or more, wrongly at 3 or less).
ORIENT_MIN_CONFIDENCE = 5.0


@dataclass(frozen=True)
class Orientation:
    """How far a page has to be turned clockwise to be upright, as an engine told."""

    rotate: int  # 0, 90, 180 or 270
    confidence: float  # the engine's own measure; 0 when it couldn't tell (too little text)
