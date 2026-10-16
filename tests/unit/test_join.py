from pathlib import Path

import pypdfium2 as pdfium
import pytest
from ocr_files import form, write_pdf
from PIL import Image, ImageChops, ImageDraw

from scriptorium import join, raster
from scriptorium.join import Half

DPI = 100


def px(mm: float) -> int:
    return round(mm / 25.4 * DPI)


def a3_form() -> Image.Image:
    """A fictional A3 form, landscape: rules, and blocks of "text" running across the middle."""
    sheet = form((px(420), px(297)), paper=px(5))
    draw = ImageDraw.Draw(sheet)
    for top in (40, 52, 61, 90, 133, 141, 200, 236, 251):
        draw.rectangle((px(180), px(top), px(240), px(top + 3)), fill="black")
    return sheet


def halves(sheet: Image.Image, lower: int = 0) -> tuple[Half, Half]:
    """The halves of `sheet` as scanned a page each, the right one laid `lower` pixels lower on
    the glass (higher if negative) than the left one."""
    middle = sheet.width // 2
    left = sheet.crop((0, 0, middle, sheet.height))
    right = Image.new("RGB", (sheet.width - middle, sheet.height), "white")
    right.paste(sheet.crop((middle, 0, sheet.width, sheet.height)), (0, lower))
    return Half(left, DPI), Half(right, DPI)


@pytest.mark.parametrize("lower", [0, px(1.7), -px(3), px(8)])
def test_the_right_half_is_moved_to_line_up_with_the_left_one(lower: int) -> None:
    sheet = a3_form()
    left, right = halves(sheet, lower)

    joined = join.join(left, right)

    assert joined.shift == -lower
    assert joined.agreement is not None and joined.agreement > 0.9
    assert joined.dpi == DPI
    assert joined.image.size == (sheet.width, sheet.height + abs(lower))
    # The sheet again, but for what the right half's page didn't hold.
    top = min(0, joined.shift)
    again = joined.image.crop((0, -top, sheet.width, sheet.height - top))
    margin = abs(lower)
    difference = ImageChops.difference(again, sheet).crop(
        (0, margin, sheet.width, sheet.height - margin)
    )
    assert difference.getbbox() is None


def with_white_at_the_cut(left: Half, right: Half, widths: tuple[int, int]) -> tuple[Half, Half]:
    """`left` and `right` with columns of white added along the cut, as a scanner may give the
    edge of its glass it can't read."""
    wider_left = Image.new("RGB", (left.image.width + widths[0], left.image.height), "white")
    wider_left.paste(left.image, (0, 0))
    wider_right = Image.new("RGB", (right.image.width + widths[1], right.image.height), "white")
    wider_right.paste(right.image, (widths[1], 0))
    return Half(wider_left, DPI), Half(wider_right, DPI)


def test_white_along_the_cut_is_trimmed_off_both_halves() -> None:
    sheet = a3_form()
    left, right = with_white_at_the_cut(*halves(sheet, px(1)), (px(1.1), px(1.4)))

    joined = join.join(left, right)

    assert joined.trimmed == (px(1.1), px(1.4))
    assert joined.shift == -px(1)
    assert joined.image.width == sheet.width
    again = joined.image.crop((0, px(1), sheet.width, sheet.height + px(1)))
    difference = ImageChops.difference(again, sheet).crop(
        (0, px(1), sheet.width, sheet.height - px(1))
    )
    assert difference.getbbox() is None


def test_white_along_the_cut_can_be_kept() -> None:
    sheet = a3_form()
    left, right = with_white_at_the_cut(*halves(sheet), (px(1.1), px(1.4)))

    joined = join.join(left, right, trim=False)

    assert joined.trimmed == (0, 0)
    assert joined.image.width == sheet.width + px(1.1) + px(1.4)


def test_white_with_specks_is_trimmed_but_not_a_column_a_rule_crosses() -> None:
    sheet = a3_form()
    left, right = halves(sheet)
    # Specks of dust in the white along the cut: a pixel in a column, here and there.
    left, right = with_white_at_the_cut(left, right, (px(1), px(1)))
    draw = ImageDraw.Draw(left.image)
    for x, y in ((left.image.width - 1, 100), (left.image.width - 3, 700)):
        draw.point((x, y), fill="black")

    joined = join.join(left, right)

    assert joined.trimmed == (px(1), px(1))
    assert joined.image.width == sheet.width


def test_columns_with_a_few_rules_crossing_them_are_kept() -> None:
    image = Image.new("RGB", (px(10), px(297)), "white")
    draw = ImageDraw.Draw(image)
    for top in (50, 120, 200):  # three thin rules, ending short of the right edge
        draw.line((0, px(top), px(10) - 5, px(top)), fill="black", width=1)

    assert join.blank_columns(image, DPI, side="right") == 4
    assert join.blank_columns(image, DPI, side="left") == 0


def test_no_more_than_max_trim_mm_is_trimmed() -> None:
    white = Image.new("RGB", (px(10), px(297)), "white")

    assert join.blank_columns(white, DPI, side="left") == px(join.MAX_TRIM_MM)


def test_without_aligning_the_halves_are_put_side_by_side_as_they_are() -> None:
    left, right = halves(a3_form(), px(2))

    joined = join.join(left, right, align=False)

    assert (joined.shift, joined.agreement) == (0, None)
    assert joined.image.size == (left.image.width + right.image.width, left.image.height)


def test_grayscale_halves_make_a_grayscale_sheet() -> None:
    left, right = halves(a3_form())

    joined = join.join(Half(left.image.convert("L"), DPI), Half(right.image.convert("L"), DPI))

    assert joined.image.mode == "L"


def test_halves_with_nothing_along_the_cut_are_not_joined() -> None:
    blank = Image.new("RGB", (px(210), px(297)), "white")

    with pytest.raises(join.Unjoinable, match="no ink along the cut"):
        join.join(Half(blank, DPI), Half(blank.copy(), DPI))


def test_halves_of_different_sheets_are_not_joined() -> None:
    left, _ = halves(a3_form())
    other = Image.new("RGB", (px(210), px(297)), "white")
    draw = ImageDraw.Draw(other)
    for top in range(20, 280, 7):
        draw.rectangle((0, px(top), px(30), px(top + 1)), fill="black")

    with pytest.raises(join.Unjoinable, match="don't line up"):
        join.join(left, Half(other, DPI))


def test_halves_at_different_resolutions_are_not_joined() -> None:
    left, right = halves(a3_form())

    with pytest.raises(join.Unjoinable, match="different resolutions"):
        join.join(left, Half(right.image, 2 * DPI))


def test_a_pdf_page_s_image_is_taken_out_as_it_is(tmp_path: Path) -> None:
    left, right = halves(a3_form(), px(2))
    path = write_pdf(tmp_path / "halves.pdf", [left.image, right.image])
    pages = raster.plan(path)

    taken = [join.half(path, page) for page in pages]

    assert [h.dpi for h in taken] == [DPI, DPI]
    for got, wanted in zip(taken, (left, right), strict=True):
        assert got.image.size == wanted.image.size
        assert ImageChops.difference(got.image.convert("RGB"), wanted.image).getbbox() is None


def test_an_image_file_is_a_half_as_it_is(tmp_path: Path) -> None:
    left, _ = halves(a3_form())
    path = tmp_path / "left.png"
    # Not at 100 dpi: a PNG keeps pixels per metre, which is read back as 99.99 (taken as unknown).
    left.image.save(path, dpi=(200, 200))
    (page,) = raster.plan(path)

    taken = join.half(path, page)

    assert taken.dpi == 200
    assert ImageChops.difference(taken.image, left.image).getbbox() is None


def test_a_pdf_page_not_filled_by_its_image_is_not_a_half(tmp_path: Path) -> None:
    left, _ = halves(a3_form())
    path = write_pdf(tmp_path / "small.pdf", [left.image], image_box=(50, 50, 300, 400))
    (page,) = raster.plan(path)

    with pytest.raises(join.Unjoinable, match="doesn't fill the page"):
        join.half(path, page)


def test_a_pdf_page_s_image_of_more_pixels_than_the_limit_is_not_a_half(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    left, _ = halves(a3_form())
    path = write_pdf(tmp_path / "large.pdf", [left.image])
    (page,) = raster.plan(path)
    monkeypatch.setattr(raster, "MAX_PIXELS", 10_000)

    with pytest.raises(ValueError, match="more than 10,000"):
        join.half(path, page)


def test_a_pdf_pdfium_can_t_open_is_not_a_half(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    left, _ = halves(a3_form())
    path = write_pdf(tmp_path / "broken.pdf", [left.image])
    (page,) = raster.plan(path)

    def opening(*args: object, **kwargs: object) -> object:
        raise pdfium.PdfiumError("Failed to load document")

    monkeypatch.setattr(pdfium, "PdfDocument", opening)

    with pytest.raises(ValueError, match="pdfium can't open it"):
        join.half(path, page)


def test_a_turned_pdf_page_is_not_a_half(tmp_path: Path) -> None:
    left, _ = halves(a3_form())
    path = write_pdf(tmp_path / "turned.pdf", [left.image], rotations=[90])
    (page,) = raster.plan(path)

    with pytest.raises(join.Unjoinable, match="turned"):
        join.half(path, page)
