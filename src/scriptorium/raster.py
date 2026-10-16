"""Which pages of a file to read by OCR, and the images of them the engines read.

A PDF page is rendered (drawn again) instead of having its embedded image
taken out: rendering handles the page's rotation and cropping, images
split into strips and every image encoding alike, and maps back to points
by a single scale. So as to lose nothing, a scanned page is rendered at the
resolution of its scan, kept within [min_dpi, max_dpi]: an engine reads
small text better at 300 dpi or so, and a finer image only costs time.

An image file is read as it is (turned upright by its EXIF orientation),
not resampled; its resolution only gives its size in points.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium
from PIL import Image, ImageOps

from scriptorium.images import PageImage, largest_image

PDF_SUFFIXES = (".pdf",)
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp")
PATTERNS = tuple(f"**/*{suffix}" for suffix in PDF_SUFFIXES + IMAGE_SUFFIXES)

# A page this much covered by one image is taken to be scanned.
SCANNED_IMAGE_COVERAGE = 0.5
# The resolution taken for an image file that doesn't tell a believable one:
# 72 dpi, say, is mostly a program's default rather than a scan's.
DEFAULT_IMAGE_DPI = 300.0
_BELIEVABLE_DPI = (100.0, 2400.0)
# EXIF orientations that turn the image by 90 degrees one way or the other.
_SIDEWAYS = {5, 6, 7, 8}
_EXIF_ORIENTATION = 0x0112
# How pdfminer gives characters whose code it has no Unicode for (a word of them, too).
_UNMAPPED = re.compile(r"(?:\(cid:\d+\))+")


class Reason(StrEnum):
    """Why a page is read by OCR or not."""

    SCANNED = "scanned"
    SCANNED_WITH_TEXT = "scanned, with a text layer"  # e.g. a scanner's own OCR
    NO_TEXT = "no text layer"  # e.g. text turned into outlines; maybe blank
    # Characters without their codes: drawn right, but only read as (cid:15) and so on.
    UNMAPPED_TEXT = "text layer without the characters' codes"
    IMAGE = "image file"
    TEXT = "text layer"  # read as text instead (not by OCR unless forced)


@dataclass(frozen=True)
class PagePlan:
    page: int  # 1-based
    width: float  # points, as the page is shown (after its rotation)
    height: float
    dpi: float  # to render a PDF page at; an image file's own
    reason: Reason

    @property
    def ocr(self) -> bool:
        """Whether the page is read by OCR (`TEXT` pages only when forced)."""
        return self.reason is not Reason.TEXT


def has_readable_text(chars: Iterable[str]) -> bool:
    """Whether some of a PDF page's characters or words (as pdfplumber reads them) can be read.

    A font with no table from its character codes to Unicode gives each
    character as "(cid:15)" and so on (a word as "(cid:15)(cid:16)"): a page
    of only those, like a page with no text, is read by OCR.
    """
    return any(text.strip() and not _UNMAPPED.fullmatch(text) for text in chars)


def is_pdf(path: Path) -> bool:
    return path.suffix.lower() in PDF_SUFFIXES


def plan(
    path: Path,
    *,
    password: str | None = None,
    min_dpi: float = 300,
    max_dpi: float = 600,
) -> list[PagePlan]:
    """Each page of the PDF or image file `path`, and whether and how it's read by OCR.

    Raises pdfplumber's PdfminerException for a PDF that's damaged or needs
    another password, and PIL's UnidentifiedImageError (an OSError) for an
    image that can't be read.
    """
    if is_pdf(path):
        return _plan_pdf(path, password=password, min_dpi=min_dpi, max_dpi=max_dpi)
    return plan_image(path)


def _plan_pdf(
    path: Path, *, password: str | None, min_dpi: float, max_dpi: float
) -> list[PagePlan]:
    plans = []
    with pdfplumber.open(path, password=password) as pdf:
        for number, page in enumerate(pdf.pages, start=1):
            has_text = has_readable_text(c["text"] for c in page.chars)
            image = largest_image(page)
            if image is not None and image.coverage >= SCANNED_IMAGE_COVERAGE:
                reason = Reason.SCANNED_WITH_TEXT if has_text else Reason.SCANNED
                dpi = render_dpi(image, min_dpi=min_dpi, max_dpi=max_dpi)
            else:
                if has_text:
                    reason = Reason.TEXT
                else:
                    reason = Reason.UNMAPPED_TEXT if page.chars else Reason.NO_TEXT
                dpi = float(min_dpi)
            plans.append(
                PagePlan(
                    page=number,
                    width=round(float(page.width), 2),
                    height=round(float(page.height), 2),
                    dpi=dpi,
                    reason=reason,
                )
            )
    return plans


def native_dpi(image: PageImage) -> float | None:
    """The resolution of an image as it's placed on its page, if its size in pixels is known."""
    if image.pixels is None or max(image.width, image.height) <= 0:
        return None
    # The longer side against the longer side: right for an image placed turned by 90 degrees too.
    return max(image.pixels) / (max(image.width, image.height) / 72)


def render_dpi(image: PageImage, *, min_dpi: float, max_dpi: float) -> float:
    """The resolution to render a page scanned as `image` at: the scan's, kept within range."""
    native = native_dpi(image)
    dpi = native if native is not None else min_dpi
    return round(min(max(dpi, min_dpi), max_dpi), 1)


def plan_image(path: Path) -> list[PagePlan]:
    """Each page of the image file `path` (a TIFF may have several), whatever its name."""
    plans = []
    with Image.open(path) as image:
        for index in range(getattr(image, "n_frames", 1)):
            image.seek(index)
            dpi = image_dpi(image)
            width, height = image.size
            if image.getexif().get(_EXIF_ORIENTATION) in _SIDEWAYS:
                width, height = height, width
            plans.append(
                PagePlan(
                    page=index + 1,
                    width=round(width * 72 / dpi, 2),
                    height=round(height * 72 / dpi, 2),
                    dpi=dpi,
                    reason=Reason.IMAGE,
                )
            )
    return plans


def image_dpi(image: Image.Image) -> float:
    """The resolution an image file tells, if believable; else `DEFAULT_IMAGE_DPI`."""
    dpi = image.info.get("dpi")
    try:
        value = float(dpi[0]) if dpi else 0.0
    except TypeError, ValueError:
        value = 0.0
    low, high = _BELIEVABLE_DPI
    return round(value, 1) if low <= value <= high else DEFAULT_IMAGE_DPI


# The most pixels a page is rendered at (or an image file's page read with): about the most
# Pillow opens an image file with (twice `Image.MAX_IMAGE_PIXELS`). A page of A2 at 600 dpi
# has 140 million. A larger page fails, rather than taking up the memory.
MAX_PIXELS = 180_000_000


def _check_pixels(width: int, height: int, page: PagePlan) -> None:
    if width * height > MAX_PIXELS:
        raise ValueError(
            f"page {page.page} would be {width}x{height} pixels at {page.dpi:g} dpi, "
            f"more than {MAX_PIXELS:,}"
        )


def open_pdf(path: Path, *, password: str | None = None) -> pdfium.PdfDocument:
    """The PDF `path` opened with pdfium; raises ValueError if it can't be."""
    try:
        return pdfium.PdfDocument(path, password=password)
    except pdfium.PdfiumError as e:
        raise ValueError(f"pdfium can't open it: {e}") from e


def render(path: Path, page: PagePlan, *, password: str | None = None) -> Image.Image:
    """The image of `page` of the file `path`, in RGB or grayscale, the way it's shown.

    Raises ValueError for a page too large to render (`MAX_PIXELS`) or that pdfium can't render.
    """
    if is_pdf(path):
        scale = page.dpi / 72
        _check_pixels(round(page.width * scale), round(page.height * scale), page)
        pdf = open_pdf(path, password=password)
        try:
            # Copied by the conversion: the bitmap goes with the document.
            image: Image.Image = pdf[page.page - 1].render(scale=scale).to_pil().convert("RGB")
        except pdfium.PdfiumError as e:
            raise ValueError(f"pdfium can't render the page: {e}") from e
        finally:
            pdf.close()
        return image
    with Image.open(path) as file:
        file.seek(page.page - 1)
        _check_pixels(*file.size, page)
        upright = ImageOps.exif_transpose(file)
    return upright if upright.mode in ("RGB", "L") else upright.convert("RGB")


# Turning an image clockwise by 90, 180 or 270 degrees.
_TURNS = {
    90: Image.Transpose.ROTATE_270,
    180: Image.Transpose.ROTATE_180,
    270: Image.Transpose.ROTATE_90,
}


def turn(image: Image.Image, clockwise: int) -> Image.Image:
    """`image` turned clockwise by 0, 90, 180 or 270 degrees."""
    return image if clockwise % 360 == 0 else image.transpose(_TURNS[clockwise % 360])


def turn_back(
    box: tuple[float, float, float, float], clockwise: int, size: tuple[int, int]
) -> tuple[float, float, float, float]:
    """A box (x0, y0, x1, y1) on an image of `size` turned clockwise, on the image as it was."""
    width, height = size
    x0, y0, x1, y1 = box
    match clockwise % 360:
        case 90:  # a point (x, y) went to (height - y, x)
            return y0, height - x1, y1, height - x0
        case 180:
            return width - x1, height - y1, width - x0, height - y0
        case 270:  # a point (x, y) went to (y, width - x)
            return width - y1, x0, width - y0, x1
        case _:
            return box
