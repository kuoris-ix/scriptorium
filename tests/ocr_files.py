"""Write fake scans (images, and PDFs of them) to test OCR with.

A "scan" is a white image with black boxes at known places, so a test can
tell where a box lands after a page is rendered or turned; any text in them
is made up.
"""

import functools
import os
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
from PIL import Image, ImageDraw
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

A4 = (595.28, 841.89)
FONT = "HeiseiKakuGo-W5"  # built into reportlab; no font file needed
pdfmetrics.registerFont(UnicodeCIDFont(FONT))

# (x0, y0, x1, y1) in pixels from the image's top left.
Box = tuple[int, int, int, int]


def scan(size: tuple[int, int], boxes: Sequence[Box] = ()) -> Image.Image:
    """A white RGB image of `size` pixels with black `boxes`."""
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    for box in boxes:
        draw.rectangle(box, fill="black")
    return image


def a4_scan(dpi: float, boxes: Sequence[Box] = ()) -> Image.Image:
    """A scan of an A4 page at `dpi`."""
    return scan((round(A4[0] * dpi / 72), round(A4[1] * dpi / 72)), boxes)


def write_pdf(
    path: Path,
    pages: Sequence[Image.Image | None],
    *,
    texts: Sequence[str | None] = (),
    image_box: tuple[float, float, float, float] | None = None,
    rotations: Sequence[int] = (),
    password: str | None = None,
) -> Path:
    """Write an A4 PDF, one page per image (None for a page without one).

    Each image fills its page, or `image_box` (x, y from the bottom left,
    width, height in points); `texts` gives a page a line of text, and
    `rotations` its /Rotate (applied after drawing, as a viewer would turn it).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=A4, encrypt=password)
    for index, image in enumerate(pages):
        if image is not None:
            x, y, width, height = image_box or (0.0, 0.0, *A4)
            c.drawImage(ImageReader(image), x, y, width=width, height=height)
        text = texts[index] if index < len(texts) else None
        if text:
            c.setFont(FONT, 10)
            c.drawString(50, A4[1] - 100, text)
        c.showPage()
    c.save()
    if any(rotations):
        pdf = pdfium.PdfDocument(path, password=password)
        for index, rotation in enumerate(rotations):
            pdf[index].set_rotation(rotation)
        rotated = path.with_suffix(".rotated.pdf")
        pdf.save(rotated)
        pdf.close()
        rotated.replace(path)
    return path


def dark_box(image: Image.Image) -> tuple[int, int, int, int] | None:
    """The smallest box around everything dark in `image` (x0, y0, x1, y1 exclusive)."""
    gray = image.convert("L").point(lambda v: 255 if v < 128 else 0)
    return gray.getbbox()


def form(size: tuple[int, int], paper: int = 0) -> Image.Image:
    """A form of `size` pixels as a flatbed scanner sees it: a frame, rules and boxes of "text",
    with `paper` pixels of white around."""
    width, height = size
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((paper, paper, width - 1 - paper, height - 1 - paper), outline="black", width=3)
    for y in range(height // 8, height, height // 8):
        draw.line((paper, y, width - paper, y), fill="black", width=2)
        for x in range(width // 10, width - width // 5, width // 4):
            draw.rectangle((x, y + height // 40, x + width // 8, y + height // 20), fill="black")
    draw.line((width // 3, paper, width // 3, height - paper), fill="black", width=2)
    return image


def flatbed(
    size: tuple[int, int], sheets: Sequence[tuple[Image.Image, int, int, float]]
) -> Image.Image:
    """A page of `size` pixels with `sheets` (image, left, top, degrees turned counterclockwise)."""
    page = Image.new("RGB", size, "white")
    for image, left, top, degrees in sheets:
        turned = image.rotate(degrees, expand=True, fillcolor="white") if degrees else image
        page.paste(turned, (left, top))
    return page


def unmapped_pdf(path: Path) -> Path:
    """Write a one-page PDF whose text has no Unicode for its characters (read as "(cid:15)" and
    so on), as some programs write them: a Type0 font without a ToUnicode table."""
    stream = b"BT /F1 12 Tf 100 700 Td <000F00100011> Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n%b\nendstream" % (len(stream), stream),
        b"<< /Type /Font /Subtype /Type0 /BaseFont /Unmapped /Encoding /Identity-H"
        b" /DescendantFonts [6 0 R] >>",
        b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /Unmapped /CIDSystemInfo"
        b" << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> /FontDescriptor 7 0 R >>",
        b"<< /Type /FontDescriptor /FontName /Unmapped /Flags 4 /FontBBox [0 0 1000 1000]"
        b" /ItalicAngle 0 /Ascent 880 /Descent -120 /CapHeight 700 /StemV 80 >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%b\nendobj\n" % (number, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))
    return path


def prepare_that_ends_on(page: int, *, once: bool) -> Callable[..., Any]:
    """Stands for `runner._prepare` where the process ends on page `page` (once, or every
    time it's asked), as one out of memory would. Importable by name, as a process needs."""
    return functools.partial(_prepare_ending, page, once)


def _prepare_ending(ends_on: int, once: bool, page: Any, scratch: Path, *args: Any) -> Any:
    from scriptorium import runner

    marker = scratch / "ended"
    if page.page.page == ends_on and not (once and marker.exists()):
        marker.touch()
        os._exit(7)
    return runner._prepare(page, scratch, *args)  # as it is in the new process
