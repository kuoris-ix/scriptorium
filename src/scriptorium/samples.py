"""Fictional documents to compare OCR engines on, with what they say (the truth).

The documents are given by the program (`Document`s by name): each draws a
page (`Page`) with made-up values, and each piece of text drawn is a line of
the truth, with its kind (an amount, a date, another number, or text) and its
box.

Each document is then made into variants as scans come: drawn at lower or
higher resolutions, slanted, turned, saved as JPEG, with noise, blur, a
shadow, in black and white, and as an image-only PDF. A variant is written
as its file and `<name>.truth.json` (the truth's boxes in points, from the
page's top left, as `OcrWord`s are), for `ocr score-engines` to score engines.
"""

import json
import math
import random
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Literal

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

Kind = Literal["amount", "date", "number", "text"]
Font = ImageFont.FreeTypeFont | ImageFont.ImageFont
# A font of a size in pixels.
FontLoader = Callable[[int], Font]

TRUTH_SUFFIX = ".truth.json"

# Japanese fonts found on each system, tried in turn when none is given.
FONT_PLACES = (
    "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "C:/Windows/Fonts/YuGothM.ttc",
    "C:/Windows/Fonts/meiryo.ttc",
    "C:/Windows/Fonts/msgothic.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf",
    "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
)


@dataclass(frozen=True)
class TruthLine:
    """A piece of text on the page, and where (pixels while drawn; points once written)."""

    text: str
    kind: Kind
    x0: float
    x1: float
    top: float
    bottom: float


@dataclass(frozen=True)
class Truth:
    """What a sample says, as written next to it."""

    file: str
    width: float  # points
    height: float
    lines: list[TruthLine]


def find_font(places: Sequence[str] = FONT_PLACES) -> Path | None:
    """A Japanese font of this system, if one of the usual ones is there."""
    for place in places:
        if Path(place).is_file():
            return Path(place)
    return None


def font_loader(path: Path) -> FontLoader:
    cache: dict[int, Font] = {}

    def load(size: int) -> Font:
        if size not in cache:
            cache[size] = ImageFont.truetype(str(path), size)
        return cache[size]

    return load


class Page:
    """A white page being drawn on at `dpi`, keeping each piece of text drawn."""

    def __init__(self, width_mm: float, height_mm: float, dpi: float, font: FontLoader) -> None:
        self.dpi = dpi
        self.image = Image.new("RGB", (self.px(width_mm), self.px(height_mm)), "white")
        self.draw = ImageDraw.Draw(self.image)
        self.font = font
        self.lines: list[TruthLine] = []

    def px(self, mm: float) -> int:
        return round(mm / 25.4 * self.dpi)

    def text(
        self,
        x_mm: float,
        y_mm: float,
        text: str,
        *,
        pt: float = 9,
        kind: Kind = "text",
        align: Literal["left", "right", "center"] = "left",
        fill: str = "black",
        truth: bool = True,
    ) -> None:
        """Draw `text` with its top at `y_mm`, its left (right, centre) at `x_mm`."""
        font = self.font(max(round(pt / 72 * self.dpi), 6))
        anchor = {"left": "la", "right": "ra", "center": "ma"}[align]
        xy = (self.px(x_mm), self.px(y_mm))
        self.draw.text(xy, text, font=font, fill=fill, anchor=anchor)
        if truth:
            x0, top, x1, bottom = self.draw.textbbox(xy, text, font=font, anchor=anchor)
            self.lines.append(TruthLine(text, kind, x0, x1, top, bottom))

    def rule(self, x0_mm: float, y0_mm: float, x1_mm: float, y1_mm: float) -> None:
        width = max(round(self.dpi / 150), 1)
        self.draw.line(
            [(self.px(x0_mm), self.px(y0_mm)), (self.px(x1_mm), self.px(y1_mm))],
            fill="black",
            width=width,
        )

    def stamp(self, x_mm: float, y_mm: float, text: str) -> None:
        """A red seal over whatever is there (not part of the truth: it isn't text to read)."""
        r = self.px(9)
        cx, cy = self.px(x_mm), self.px(y_mm)
        self.draw.ellipse(
            [cx - r, cy - r, cx + r, cy + r], outline="#d22", width=max(self.px(0.6), 1)
        )
        self.text(x_mm, y_mm - 3, text, pt=12, align="center", fill="#d22", truth=False)


# A kind of document: draws one at a resolution (dpi) with a font, its values from the random
# numbers given (the same ones draw the same document).
Document = Callable[[random.Random, float, FontLoader], Page]


@dataclass(frozen=True)
class Variant:
    """A way a document is scanned: drawn at `dpi`, then each step done to it, saved as `suffix`."""

    name: str
    dpi: int = 300
    rotate: float = 0  # degrees counterclockwise, any angle
    turn: int = 0  # degrees clockwise, a multiple of 90
    effects: tuple[str, ...] = ()  # noise, blur, shadow, bw
    suffix: str = ".png"
    jpeg_quality: int = 90


VARIANTS = (
    Variant("300"),
    Variant("150", dpi=150),
    Variant("200", dpi=200),
    Variant("600", dpi=600),
    Variant("slant+1", rotate=1),
    Variant("slant-3", rotate=-3),
    Variant("turned90", turn=90),
    Variant("turned180", turn=180),
    Variant("jpeg50", suffix=".jpg", jpeg_quality=50),
    Variant("noise", effects=("noise",)),
    Variant("blur", effects=("blur",)),
    Variant("shadow", effects=("shadow",)),
    Variant("bw", effects=("bw",)),
    Variant("pdf", suffix=".pdf"),
    Variant("pdf150", dpi=150, suffix=".pdf"),
    Variant("photo", rotate=2, effects=("shadow", "noise"), suffix=".jpg", jpeg_quality=75),
)


def make(
    document: Document, rng: random.Random, variant: Variant, font: FontLoader
) -> tuple[Image.Image, list[TruthLine]]:
    """The document drawn and scanned as `variant`: the image, and its truth in pixels."""
    page = document(rng, variant.dpi, font)
    image, lines = page.image, page.lines
    if variant.rotate:
        image, lines = _rotate(image, lines, variant.rotate)
    if variant.turn:
        image, lines = _turn(image, lines, variant.turn)
    for effect in variant.effects:
        image = _EFFECTS[effect](image, rng)
    return image, lines


def _rotate(
    image: Image.Image, lines: list[TruthLine], degrees: float
) -> tuple[Image.Image, list[TruthLine]]:
    turned = image.rotate(
        degrees, resample=Image.Resampling.BICUBIC, expand=True, fillcolor="white"
    )
    cx, cy = image.width / 2, image.height / 2
    nx, ny = turned.width / 2, turned.height / 2
    cos, sin = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))

    def moved(x: float, y: float) -> tuple[float, float]:
        # Counterclockwise as seen, with y growing downwards.
        return nx + (x - cx) * cos + (y - cy) * sin, ny - (x - cx) * sin + (y - cy) * cos

    return turned, [_around(line, moved) for line in lines]


def _turn(
    image: Image.Image, lines: list[TruthLine], degrees: int
) -> tuple[Image.Image, list[TruthLine]]:
    w, h = image.size
    moves: dict[int, tuple[Image.Transpose, Callable[[float, float], tuple[float, float]]]] = {
        90: (Image.Transpose.ROTATE_270, lambda x, y: (h - y, x)),
        180: (Image.Transpose.ROTATE_180, lambda x, y: (w - x, h - y)),
        270: (Image.Transpose.ROTATE_90, lambda x, y: (y, w - x)),
    }
    transpose, moved = moves[degrees % 360]
    return image.transpose(transpose), [_around(line, moved) for line in lines]


def _around(line: TruthLine, moved: Callable[[float, float], tuple[float, float]]) -> TruthLine:
    corners = [moved(x, y) for x in (line.x0, line.x1) for y in (line.top, line.bottom)]
    xs, ys = [c[0] for c in corners], [c[1] for c in corners]
    return replace(line, x0=min(xs), x1=max(xs), top=min(ys), bottom=max(ys))


def _noise(image: Image.Image, rng: random.Random) -> Image.Image:
    noise = Image.effect_noise(image.size, 25).convert("L")
    gray = ImageChops.add(image.convert("L"), noise, scale=1.0, offset=-128)
    return gray.convert("RGB")


def _blur(image: Image.Image, rng: random.Random) -> Image.Image:
    return image.filter(ImageFilter.GaussianBlur(radius=image.width / 2480 * 3))


def _shadow(image: Image.Image, rng: random.Random) -> Image.Image:
    gradient = Image.linear_gradient("L").rotate(rng.choice((0, 90, 180, 270)))
    gradient = gradient.resize(image.size).point(lambda v: 150 + v * 105 // 255)
    return ImageChops.multiply(image.convert("L"), gradient).convert("RGB")


def _bw(image: Image.Image, rng: random.Random) -> Image.Image:
    return image.convert("L").point(lambda v: 255 if v > 160 else 0).convert("1")


_EFFECTS: dict[str, Callable[[Image.Image, random.Random], Image.Image]] = {
    "noise": _noise,
    "blur": _blur,
    "shadow": _shadow,
    "bw": _bw,
}


def write(
    folder: Path,
    font: FontLoader,
    documents: Mapping[str, Document],
    *,
    seed: int = 1,
    count: int = 2,
    variants: Sequence[Variant] | None = None,
) -> Iterator[Path]:
    """Write `count` of each of `documents` (by name) as each variant to `folder`; each file
    as it's written.

    Every variant unless given. The same seed makes the same documents (a variant of a
    document has the same values as the others).
    """
    folder.mkdir(parents=True, exist_ok=True)
    for name, document in documents.items():
        for number in range(1, count + 1):
            for variant in variants or VARIANTS:
                # The same values for every variant of a document.
                rng = random.Random(f"{seed}-{name}-{number}")
                image, lines = make(document, rng, variant, font)
                path = folder / f"{name}-{number}-{variant.name}{variant.suffix}"
                yield _save(path, image, lines, variant)


def _save(path: Path, image: Image.Image, lines: list[TruthLine], variant: Variant) -> Path:
    dpi = variant.dpi
    if variant.suffix == ".pdf":
        image.convert("RGB").save(path, resolution=dpi)
    elif variant.suffix == ".jpg":
        image.convert("RGB").save(path, quality=variant.jpeg_quality, dpi=(dpi, dpi))
    else:
        image.save(path, dpi=(dpi, dpi))
    scale = 72 / dpi
    truth = Truth(
        file=path.name,
        width=round(image.width * scale, 2),
        height=round(image.height * scale, 2),
        lines=[
            replace(
                line,
                x0=round(line.x0 * scale, 2),
                x1=round(line.x1 * scale, 2),
                top=round(line.top * scale, 2),
                bottom=round(line.bottom * scale, 2),
            )
            for line in lines
        ],
    )
    truth_path = truth_path_of(path)
    truth_path.write_text(
        json.dumps(asdict(truth), ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    return path


def truth_path_of(path: Path) -> Path:
    return path.with_name(path.name + TRUTH_SUFFIX)


def load_truth(path: Path) -> Truth | None:
    """The truth written next to the sample at `path`, if there is one."""
    truth_path = truth_path_of(path)
    if not truth_path.exists():
        return None
    data = json.loads(truth_path.read_text(encoding="utf-8"))
    return Truth(
        file=data["file"],
        width=float(data["width"]),
        height=float(data["height"]),
        lines=[TruthLine(**line) for line in data["lines"]],
    )
