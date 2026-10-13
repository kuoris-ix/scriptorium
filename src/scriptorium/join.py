"""Putting a sheet scanned in two halves back together (`ocr join-scans`).

A sheet larger than the scanner's glass (an A3 form on an A4 scanner) is
scanned in two halves, a page each: its left half, then its right half.
Read so, a row of a table is cut in two, its label on one page and its
values on the other. The halves are put side by side again, as one image of
the sheet.

The halves are seldom laid on the glass at quite the same height, so the
right one is moved up or down to line up with the left one. How far is told
by the rows of ink running across the cut (rules, lines of text): the
darkness of each row of a strip on either side of it (`STRIP_MM` wide) is
compared at each shift up to `MAX_SHIFT_MM`, and the shift at which they
agree best (by correlation) is taken; looked for at `LOOK_DPI`, then
refined at the scan's own resolution. If they don't agree well enough
(`MIN_AGREEMENT`), the halves aren't joined, rather than joined wrong.
Halves left or right of where they should be (a strip missing at the cut,
or scanned twice) aren't told: the image joined is to be looked at.

A scanner may not read the last millimetre or so along the glass's edge, and
give it white: each half's columns along the cut that are white are cut off
first (up to `MAX_TRIM_MM`), so that the halves meet where their scans do. A
column is white if no more than `_SPECKS` of its height is ink (pixels
darker than `_INK`): a white band has a few specks of dust or noise in a
column, a column of the paper with text or a few rules crossing it many
times more. What the scanner didn't read can't be got back; the white of the
paper itself along the cut (a margin between two halves of a form) may go
too.

A PDF page's image is taken out as it's embedded, its own pixels (not
rendered again, as `raster` does for reading), when the page is one image
filling it, upright; an image file's page is used as it is. Both halves
must be at the same resolution. What the shift leaves empty is white. Only
Pillow is used (and pypdfium2, to take the images out).
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from PIL import Image

from scriptorium import raster

LOOK_DPI = 100  # the shift is looked for this small first
STRIP_MM = 15.0  # how wide a strip on either side of the cut is compared
MAX_SHIFT_MM = 10.0  # the right half is looked for this far up or down
# How well the rows along the cut must agree (correlation, 1 at best) for the halves to be
# joined. Halves of one sheet lined up agree closely (above 0.9 for the fictional forms of
# the tests; less for a scan's noise and faint lines); halves of different sheets much less.
MIN_AGREEMENT = 0.6
# How far (points) a PDF page's image may be from the page's edges and still fill it.
_FILL_TOLERANCE = 1.0
MAX_TRIM_MM = 3.0  # at most this much white is cut off each half along the cut
_INK = 128  # a pixel darker than this (0-255) is ink
# A column with no more of its height inked than this is white: its ink is specks.
_SPECKS = 0.001


class Unjoinable(Exception):
    """Two pages that can't be joined as halves of a sheet, and why (the message)."""


@dataclass(frozen=True)
class Half:
    """A half of a sheet: the image of its page, and that image's resolution."""

    image: Image.Image
    dpi: float


@dataclass(frozen=True)
class Joined:
    """A sheet joined from its halves."""

    image: Image.Image
    dpi: float
    # Pixels the right half was moved down (up if negative) to line up with the left one.
    shift: int
    agreement: float | None  # how well the rows along the cut agreed; None if not lined up
    # Columns of white along the cut cut off the left half and the right half.
    trimmed: tuple[int, int] = (0, 0)


def half(path: Path, page: raster.PagePlan, *, password: str | None = None) -> Half:
    """The image of `page` of the file `path`, as a half of a sheet.

    A PDF page's embedded image, as it is; raises `Unjoinable` if the page
    isn't one image filling it, upright.
    """
    if not raster.is_pdf(path):
        return Half(raster.render(path, page), page.dpi)
    pdf = pdfium.PdfDocument(path, password=password)
    try:
        return _embedded(pdf[page.page - 1])
    finally:
        pdf.close()


def _embedded(page: pdfium.PdfPage) -> Half:
    if page.get_rotation():
        raise Unjoinable("the page is turned (/Rotate): its image isn't upright")
    images = list(page.get_objects(filter=(pdfium_c.FPDF_PAGEOBJ_IMAGE,)))
    if len(images) != 1:
        raise Unjoinable(f"the page has {len(images)} images, not one filling it")
    (image,) = images
    matrix = image.get_matrix()
    if matrix.b or matrix.c or matrix.a <= 0 or matrix.d <= 0:
        raise Unjoinable("the page's image is placed turned or mirrored")
    left, bottom, right, top = image.get_bounds()
    width, height = page.get_size()
    if max(abs(left), abs(bottom), abs(right - width), abs(top - height)) > _FILL_TOLERANCE:
        raise Unjoinable("the page's image doesn't fill the page")
    bitmap = image.get_bitmap(render=False).to_pil()
    # Copied either way: the bitmap goes with the document.
    pixels = bitmap.copy() if bitmap.mode in ("RGB", "L") else bitmap.convert("RGB")
    return Half(pixels, round(pixels.width / (width / 72), 1))


def shift(left: Half, right: Half) -> tuple[int, float]:
    """How far (pixels) to move `right` down (up if negative) to line up with `left`, and how
    well the rows along the cut agree then.

    Raises `Unjoinable` if there's nothing along the cut to line them up by,
    or they don't agree well enough.
    """
    dpi = left.dpi
    scale = LOOK_DPI / dpi
    # Wanted: the shift s that has row y of `left` meet row y - s of `right`.
    a = _rows(left.image, dpi, side="right", scale=scale)
    b = _rows(right.image, dpi, side="left", scale=scale)
    reach = round(MAX_SHIFT_MM / 25.4 * LOOK_DPI)
    best = _best(a, b, range(-reach, reach + 1))
    if best is None:
        raise Unjoinable("there's no ink along the cut to line the halves up by")
    agreement, found = best
    if agreement < MIN_AGREEMENT:
        raise Unjoinable(
            f"the rows along the cut don't line up (agreeing {agreement:.2f} at best, "
            f"less than {MIN_AGREEMENT})"
        )
    # Refined at the scan's own resolution, near what was found small.
    a = _rows(left.image, dpi, side="right")
    b = _rows(right.image, dpi, side="left")
    near = round(found / scale)
    spread = math.ceil(1 / scale)
    refined = _best(a, b, range(near - spread, near + spread + 1))
    return (refined[1] if refined is not None else near), round(agreement, 2)


def join(left: Half, right: Half, *, align: bool = True, trim: bool = True) -> Joined:
    """The sheet whose halves are `left` and `right`, side by side; with `trim`, the white
    along the cut cut off them first (see `blank_columns`); with `align`, `right` moved up or
    down to line up with `left` (see `shift`), else as it is.

    Raises `Unjoinable` for halves of different resolutions, or (with
    `align`) halves that can't be lined up.
    """
    if abs(left.dpi - right.dpi) > 1:
        raise Unjoinable(f"the halves are at different resolutions ({left.dpi}, {right.dpi} dpi)")
    trimmed = (0, 0)
    if trim:
        trimmed = (
            blank_columns(left.image, left.dpi, side="right"),
            blank_columns(right.image, right.dpi, side="left"),
        )
        width, height = left.image.size
        left = Half(left.image.crop((0, 0, width - trimmed[0], height)), left.dpi)
        width, height = right.image.size
        right = Half(right.image.crop((trimmed[1], 0, width, height)), right.dpi)
    moved, agreement = shift(left, right) if align else (0, None)
    top = min(0, moved)
    bottom = max(left.image.height, moved + right.image.height)
    mode = "L" if left.image.mode == right.image.mode == "L" else "RGB"
    sheet = Image.new(mode, (left.image.width + right.image.width, bottom - top), "white")
    sheet.paste(left.image.convert(mode), (0, -top))
    sheet.paste(right.image.convert(mode), (left.image.width, moved - top))
    return Joined(sheet, left.dpi, moved, agreement, trimmed)


def blank_columns(image: Image.Image, dpi: float, *, side: str) -> int:
    """How many columns along `side` ("left" or "right") of `image` (at `dpi`) are white, up to
    `MAX_TRIM_MM`."""
    width = min(round(MAX_TRIM_MM / 25.4 * dpi), image.width)
    x0 = image.width - width if side == "right" else 0
    strip = image.crop((x0, 0, x0 + width, image.height)).convert("L")
    ink = strip.point(lambda v: 255 if v < _INK else 0)
    most = _SPECKS * image.height
    white = 0
    for x in range(width - 1, -1, -1) if side == "right" else range(width):
        if ink.crop((x, 0, x + 1, ink.height)).histogram()[255] > most:
            break
        white += 1
    return white


def _rows(image: Image.Image, dpi: float, *, side: str, scale: float = 1.0) -> list[float]:
    """The darkness (0-255) of each row of the strip along `side` of `image`, at `scale`."""
    width = min(round(STRIP_MM / 25.4 * dpi), image.width)
    x0 = image.width - width if side == "right" else 0
    strip = image.crop((x0, 0, x0 + width, image.height)).convert("L")
    height = max(round(strip.height * scale), 1)
    column = strip.resize((1, height), Image.Resampling.BOX)
    return [255.0 - v for v in column.tobytes()]


def _best(a: Sequence[float], b: Sequence[float], shifts: range) -> tuple[float, int] | None:
    """The best agreement of `a` and `b` over `shifts`, and its shift (the smallest shift of
    those as good); None if they can't be compared at any (no ink)."""
    found = [(c, s) for s in shifts if not math.isnan(c := _agreement(a, b, s))]
    if not found:
        return None
    return max(found, key=lambda found: (found[0], -abs(found[1])))


def _agreement(a: Sequence[float], b: Sequence[float], s: int) -> float:
    """The correlation of `a` and `b` with row y of `a` against row y - s of `b`, over the rows
    both have (NaN if either is even there, or they overlap by less than half)."""
    start, end = max(0, s), min(len(a), len(b) + s)
    n = end - start
    if n < min(len(a), len(b)) / 2:
        return math.nan
    x = a[start:end]
    y = b[start - s : end - s]
    mean_x, mean_y = sum(x) / n, sum(y) / n
    sxy = sum((p - mean_x) * (q - mean_y) for p, q in zip(x, y, strict=True))
    sxx = sum((p - mean_x) ** 2 for p in x)
    syy = sum((q - mean_y) ** 2 for q in y)
    if sxx == 0 or syy == 0:
        return math.nan
    return sxy / math.sqrt(sxx * syy)
