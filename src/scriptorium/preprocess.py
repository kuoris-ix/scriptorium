"""Making a page easier to read before an engine reads it: steps chosen by name.

Each step takes the page's image and gives the image to read, and the way
back: where a box on the new image lies on the one it was given. Steps run
in the order given (after the page is turned upright, see `runner`), and
what's read comes back through them in reverse, so what's kept is always
on the page as it is.

- `flatten`: evens out the lighting (a shadow, a photo's falling light).
  The paper's brightness is estimated as the brightest around each place
  (on a small copy, so text doesn't count) and every pixel divided by it.
  The page becomes grayscale; nothing moves.
- `deskew`: straightens a page scanned slightly slanted (up to 6 degrees).
  The slant is the angle at which the rows of ink are most uneven
  (projection profiles; text lines are rows of ink with gaps between),
  looked for on a flattened, small copy.

Only Pillow is used. The steps were tried on fictional samples (see
docs/benchmark-results.ja.md).
"""

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from PIL import Image, ImageFilter, ImageMath, ImageOps

# A box (x0, y0, x1, y1) in pixels from the image's top left.
Box = tuple[float, float, float, float]
Back = Callable[[Box], Box]

# Raised when a step changes what it does, so that pages read with it are read again.
REVISION = 1

_FLATTEN_SIZE = 400  # the longer side of the copy the paper's brightness is found on
_FLATTEN_REACH = 15  # pixels of that copy (about 7 mm of A4): more than large text
_SKEW_SIZE = 1000  # the longer side of the copy the slant is found on
_SKEW_MAX = 6.0  # degrees either way
SKEW_MIN = 0.2  # a slant less than this is left alone


@dataclass(frozen=True)
class Step:
    name: str
    description: str
    apply: Callable[[Image.Image], tuple[Image.Image, Back]]


def _same(box: Box) -> Box:
    return box


def _small(image: Image.Image, longer_side: int) -> Image.Image:
    scale = min(longer_side / max(image.size), 1.0)
    size = (max(round(image.width * scale), 1), max(round(image.height * scale), 1))
    return image.resize(size, Image.Resampling.BOX)


def flattened(image: Image.Image) -> Image.Image:
    """`image` in grayscale with its lighting evened out (see the module's description)."""
    gray = image.convert("L")
    paper = (
        _small(gray, _FLATTEN_SIZE)
        .filter(ImageFilter.MaxFilter(_FLATTEN_REACH))
        .filter(ImageFilter.GaussianBlur(_FLATTEN_REACH / 3))
        .resize(gray.size, Image.Resampling.BILINEAR)
    )
    evened = ImageMath.lambda_eval(
        lambda a: a["page"] * 255 / (a["paper"] + 1), page=gray, paper=paper
    )
    assert isinstance(evened, Image.Image)
    return evened.convert("L")


def flatten(image: Image.Image) -> tuple[Image.Image, Back]:
    return flattened(image), _same


def skew(image: Image.Image) -> float:
    """The angle (degrees, counterclockwise) to turn `image` by to have its lines level."""
    # Made small before its lighting is evened out: it's only looked at.
    ink = ImageOps.invert(flattened(_small(image, _SKEW_SIZE))).point(
        lambda v: 255 if v > 80 else 0
    )

    def unevenness(angle: float) -> float:
        turned = ink.rotate(angle, resample=Image.Resampling.BILINEAR, fillcolor=0)
        rows = list(turned.resize((1, turned.height), Image.Resampling.BOX).tobytes())
        mean = sum(rows) / len(rows)
        return sum((r - mean) ** 2 for r in rows)

    steps = round(_SKEW_MAX * 2)
    coarse = max((a / 2 for a in range(-steps, steps + 1)), key=unevenness)
    return max((coarse + a / 20 for a in range(-10, 11)), key=unevenness)


def rotated(image: Image.Image, degrees: float) -> tuple[Image.Image, Back]:
    """`image` turned `degrees` counterclockwise (grown to hold it all), and the way back."""
    turned = image.rotate(
        degrees, resample=Image.Resampling.BICUBIC, expand=True, fillcolor="white"
    )
    cx, cy = image.width / 2, image.height / 2
    nx, ny = turned.width / 2, turned.height / 2
    cos, sin = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))

    def back(box: Box) -> Box:
        x0, y0, x1, y1 = box
        # Each corner turned back clockwise, about the centre (y grows downwards).
        corners = [
            (cx + (x - nx) * cos - (y - ny) * sin, cy + (x - nx) * sin + (y - ny) * cos)
            for x in (x0, x1)
            for y in (y0, y1)
        ]
        xs, ys = [c[0] for c in corners], [c[1] for c in corners]
        return min(xs), min(ys), max(xs), max(ys)

    return turned, back


def deskew(image: Image.Image) -> tuple[Image.Image, Back]:
    angle = skew(image)
    if abs(angle) < SKEW_MIN:
        return image, _same
    return rotated(image, angle)


STEPS: dict[str, Step] = {
    step.name: step
    for step in (
        Step("flatten", "even out the lighting (shadows, photos)", flatten),
        Step("deskew", "straighten a page scanned slanted (up to 6 degrees)", deskew),
    )
}


def steps(names: Sequence[str]) -> list[Step]:
    """The steps named, in order; raises KeyError (listing the names) for one there isn't."""
    try:
        return [STEPS[name] for name in names]
    except KeyError as e:
        raise KeyError(f"no preprocessing step {e.args[0]!r} (use: {', '.join(STEPS)})") from e


def apply(image: Image.Image, chosen: Sequence[Step]) -> tuple[Image.Image, Back]:
    """`image` through each of `chosen`, and the way back through all of them."""
    backs = []
    for step in chosen:
        image, back = step.apply(image)
        backs.append(back)

    def back_all(box: Box) -> Box:
        for back in reversed(backs):
            box = back(box)
        return box

    return image, back_all
