import pytest
from ocr_files import flatbed, form
from PIL import ImageDraw

from scriptorium import preprocess, split

DPI = 150


def px(mm: float) -> int:
    return round(mm / 25.4 * DPI)


A4_LANDSCAPE = (px(297), px(210))
SLIP = (px(140), px(100))
PAPER = px(2)  # white around a form's frame


def inside(inner: tuple[int, int, int, int], outer: tuple[int, int, int, int]) -> bool:
    return (
        outer[0] <= inner[0]
        and outer[1] <= inner[1]
        and inner[2] <= outer[2]
        and inner[3] <= outer[3]
    )


def test_four_sheets_are_found_in_rows_and_the_slanted_ones_straightened() -> None:
    places = [
        (px(6), px(2), -1.3),
        (px(151), px(4), 0.6),
        (px(4), px(108), 0),
        (px(152), px(106), 0),
    ]
    page = flatbed(A4_LANDSCAPE, [(form(SLIP, PAPER), x, y, turn) for x, y, turn in places])

    boxes = split.find(page, DPI)

    assert len(boxes) == 4
    for box, (x, y, _) in zip(boxes, places, strict=True):
        # Around the sheet (grown a little by its slant) and the margin, no more.
        assert inside((x + px(3), y + px(3), x + SLIP[0] - px(3), y + SLIP[1] - px(3)), box)
        assert box[2] - box[0] < SLIP[0] + px(2 * split.MARGIN_MM + 6)
    turns = [split.cut(page, box).skew for box in boxes]
    assert turns == pytest.approx([1.3, -0.6, 0, 0], abs=0.3)
    assert turns[2:] == [0, 0]  # level sheets are left as they are


def test_a_straightened_sheet_is_level() -> None:
    page = flatbed(A4_LANDSCAPE, [(form(SLIP), px(20), px(20), 2.0)])

    (box,) = split.find(page, DPI)
    document = split.cut(page, box)

    assert preprocess.skew(document.image) == pytest.approx(0, abs=0.3)
    assert document.image.width > box[2] - box[0]  # grown to hold it all


def test_sheets_close_together_are_cut_apart_without_the_other_s_edge() -> None:
    sheet = form((px(140), px(200)), PAPER)
    page = flatbed(A4_LANDSCAPE, [(sheet, px(4), px(5), 0), (sheet, px(144), px(5), 0)])

    left, right = split.find(page, DPI)

    assert left[2] <= right[0]
    # The frames are 4 mm apart, at 142 and 146 mm: cut halfway.
    assert px(143) <= left[2] <= right[0] <= px(145)


def test_specks_are_not_sheets_and_a_sheet_in_pieces_is_one() -> None:
    page = flatbed(
        A4_LANDSCAPE,
        [
            (form((px(10), px(10))), px(250), px(180), 0),  # a speck of dust, a stamp
            # A frame broken in one place: the pieces' boxes overlap.
            (form((px(100), px(60))), px(20), px(20), 0),
            (form((px(30), px(90))), px(100), px(40), 0),
        ],
    )

    assert len(split.find(page, DPI)) == 1


def test_a_blank_page_has_no_sheet() -> None:
    assert split.find(flatbed(A4_LANDSCAPE, []), DPI) == []


def test_lines_along_the_empty_bed_running_into_a_sheet_are_left_out() -> None:
    sheet = form((px(140), px(200)), PAPER)
    page = flatbed(A4_LANDSCAPE, [(sheet, px(150), px(5), 0)])
    draw = ImageDraw.Draw(page)
    draw.rectangle((0, 0, px(150), A4_LANDSCAPE[1]), fill=(195, 195, 195))  # the empty bed
    for y in (px(1), A4_LANDSCAPE[1] - px(1)):  # the glass's edges, running into the sheet
        draw.line((0, y, A4_LANDSCAPE[0], y), fill="black", width=3)

    (box,) = split.find(page, DPI)

    assert px(145) <= box[0] and box[2] >= px(290)
