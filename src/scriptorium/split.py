"""Finding the documents on a scanned page, to cut out each (`ocr split-scans`).

A flatbed scanner takes several sheets of paper at once (two A5 statements, four
small forms), each a little slanted maybe. A document is told from the
white of the scanner's lid around it: its ink (rules, text) is grown a little
so that it all hangs together, and each group of ink large enough is a
document. Groups whose boxes overlap are one document (a slip whose frame
is faint in one place). A group's edges with no more than thin lines of ink
are trimmed off: the edge of the scanner's glass, a line along an empty part of
the bed, may run into a sheet and make one group with it. The page is looked
at small (`LOOK_DPI`), so this takes no time; what's cut out is at the page's
own resolution.

Each document is straightened as `preprocess.deskew` would (up to 6 degrees).
Only Pillow is used. Meant for pages such as four forms on an A4 page, some of
them slanted by a degree or so, two A5 sheets side by side, or one sheet on
half of a page whose other half is the empty bed.
"""

from collections import deque
from dataclasses import dataclass

from PIL import Image, ImageFilter, ImageOps

from scriptorium import preprocess

# A box (x0, y0, x1, y1) in pixels from the image's top left (x1, y1 exclusive).
Box = tuple[int, int, int, int]

LOOK_DPI = 50  # the page is looked at this small
# Darkness (0-255, after the lighting is evened out) counted as ink, as for the skew: the
# empty bed beside white is 62.
_INK = 80
_GROW = 5  # pixels at LOOK_DPI (2.5 mm): ink this near is of the same document
_SPARSE = 0.1  # an edge column (row) of a group with less of its height (width) inked is trimmed
MIN_SIDE_MM = 40.0  # a smaller group of ink is a speck, not a document
MARGIN_MM = 3.0  # white kept around a document cut out


@dataclass(frozen=True)
class Document:
    """A document found on a page, and the image of it cut out and straightened."""

    box: Box  # on the page's image
    skew: float  # degrees it was turned counterclockwise by (0 when left as it was)
    image: Image.Image


def find(page: Image.Image, dpi: float) -> list[Box]:
    """The boxes around the documents on `page` (scanned at `dpi`), as they're read.

    In rows from the top, each from left to right; with a margin of white,
    within the page.
    """
    scale = LOOK_DPI / dpi
    size = (max(round(page.width * scale), 1), max(round(page.height * scale), 1))
    small = page.resize(size, Image.Resampling.BOX)
    ink = ImageOps.invert(preprocess.flattened(small)).point(lambda v: 255 if v > _INK else 0)
    grown = ink.filter(ImageFilter.MaxFilter(_GROW))
    least = MIN_SIDE_MM / 25.4 * LOOK_DPI
    trimmed = (_trimmed(grown, b) for b in _groups(grown))
    found = [b for b in trimmed if b[2] - b[0] >= least and b[3] - b[1] >= least]
    documents = _in_reading_order(_merged(found))
    margin = MARGIN_MM / 25.4 * LOOK_DPI
    return [
        (
            max(round(x0 / scale), 0),
            max(round(y0 / scale), 0),
            min(round(x1 / scale), page.width),
            min(round(y1 / scale), page.height),
        )
        for x0, y0, x1, y1 in (_with_margin(d, documents, margin) for d in documents)
    ]


def _with_margin(box: Box, others: list[Box], margin: float) -> tuple[float, float, float, float]:
    """`box` grown by `margin`, but only up to halfway to a document beside it."""
    x0, y0, x1, y1 = box[0] - margin, box[1] - margin, box[2] + margin, box[3] + margin
    for o in others:
        beside = o[1] < box[3] and box[1] < o[3]  # in the same row
        above_or_below = o[0] < box[2] and box[0] < o[2]
        if beside and o[0] >= box[2]:
            x1 = min(x1, (box[2] + o[0]) / 2)
        if beside and o[2] <= box[0]:
            x0 = max(x0, (o[2] + box[0]) / 2)
        if above_or_below and o[1] >= box[3]:
            y1 = min(y1, (box[3] + o[1]) / 2)
        if above_or_below and o[3] <= box[1]:
            y0 = max(y0, (o[3] + box[1]) / 2)
    return x0, y0, x1, y1


def cut(page: Image.Image, box: Box) -> Document:
    """The document in `box` of `page`, straightened."""
    image = page.crop(box)
    angle = preprocess.skew(image)
    if abs(angle) < preprocess.SKEW_MIN:
        return Document(box, 0.0, image)
    return Document(box, angle, preprocess.rotated(image, angle)[0])


def _groups(image: Image.Image) -> list[Box]:
    """The box around each group of touching white pixels of `image` (black and white)."""
    width, height = image.size
    lit = bytearray(1 if v else 0 for v in image.tobytes())
    boxes = []
    for start in range(len(lit)):
        if not lit[start]:
            continue
        lit[start] = 0
        queue = deque([start])
        x0, y0, x1, y1 = width, height, 0, 0
        while queue:
            at = queue.popleft()
            y, x = divmod(at, width)
            x0, y0, x1, y1 = min(x0, x), min(y0, y), max(x1, x + 1), max(y1, y + 1)
            for near, inside in (
                (at - 1, x > 0),
                (at + 1, x < width - 1),
                (at - width, y > 0),
                (at + width, y < height - 1),
            ):
                if inside and lit[near]:
                    lit[near] = 0
                    queue.append(near)
        boxes.append((x0, y0, x1, y1))
    return boxes


def _trimmed(image: Image.Image, box: Box) -> Box:
    """`box` on `image` (black and white) without the columns and rows at its edges that are
    hardly inked (`_SPARSE`), until its edges are inked."""
    while True:
        x0, y0, x1, y1 = box
        width, height = x1 - x0, y1 - y0
        columns, rows = [0] * width, [0] * height
        for at, value in enumerate(image.crop(box).tobytes()):
            if value:
                columns[at % width] += 1
                rows[at // width] += 1
        left, right = _inked_span(columns, _SPARSE * height)
        top, bottom = _inked_span(rows, _SPARSE * width)
        if right <= left or bottom <= top:
            return x0, y0, x0, y0
        trimmed = (x0 + left, y0 + top, x0 + right, y0 + bottom)
        if trimmed == box:
            return box
        box = trimmed


def _inked_span(counts: list[int], least: float) -> tuple[int, int]:
    """Where the first and after the last of `counts` at least `least` are (0, 0 if none)."""
    inked = [i for i, count in enumerate(counts) if count >= least]
    return (inked[0], inked[-1] + 1) if inked else (0, 0)


def _merged(boxes: list[Box]) -> list[Box]:
    """`boxes`, those that overlap made one."""
    merged = list(boxes)
    joined = True
    while joined:
        joined = False
        for i, a in enumerate(merged):
            for j in range(i + 1, len(merged)):
                b = merged[j]
                if a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]:
                    merged[i] = (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))
                    del merged[j]
                    joined = True
                    break
            if joined:
                break
    return merged


def _in_reading_order(boxes: list[Box]) -> list[Box]:
    """`boxes` in rows from the top (a box whose middle is beside a row's first is in that
    row), each from left to right."""
    rows: list[list[Box]] = []
    for box in sorted(boxes, key=lambda b: b[1]):
        middle = (box[1] + box[3]) / 2
        if rows and rows[-1][0][1] <= middle < rows[-1][0][3]:
            rows[-1].append(box)
        else:
            rows.append([box])
    return [box for row in rows for box in sorted(row)]
