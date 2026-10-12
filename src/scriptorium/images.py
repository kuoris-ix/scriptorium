"""The image covering the most of a PDF page: a scan's, whose resolution it's read at
(`raster`), and how much of the page it covers (`pdf` uses it too)."""

from dataclasses import dataclass
from typing import Any

import pdfplumber

# A share of the page, to two decimals.
_PRECISION = 2


@dataclass(frozen=True)
class PageImage:
    """The image covering the most of a page."""

    coverage: float  # share of the page it covers
    width: float  # as placed on the page, in points
    height: float
    pixels: tuple[int, int] | None  # its own width and height, if known


def largest_image(page: pdfplumber.page.Page) -> PageImage | None:
    """The image covering the most of `page` (only its part on the page counts), if any."""
    page_area = float(page.width * page.height)
    if not page.images or page_area <= 0:
        return None

    def visible(i: dict[str, Any]) -> float:
        width = min(float(i["x1"]), float(page.width)) - max(float(i["x0"]), 0.0)
        height = min(float(i["bottom"]), float(page.height)) - max(float(i["top"]), 0.0)
        return max(width, 0.0) * max(height, 0.0)

    image = max(page.images, key=visible)
    src = image.get("srcsize")
    pixels = (int(src[0]), int(src[1])) if src and all(src) else None
    return PageImage(
        coverage=round(visible(image) / page_area, _PRECISION),
        width=float(image["x1"]) - float(image["x0"]),
        height=float(image["bottom"]) - float(image["top"]),
        pixels=pixels,
    )
