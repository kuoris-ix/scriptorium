from pathlib import Path

import pytest
from ocr_files import A4, a4_scan, dark_box, scan, unmapped_pdf, write_pdf
from PIL import Image

from scriptorium import raster
from scriptorium.images import PageImage
from scriptorium.raster import Reason


@pytest.mark.parametrize(("scan_dpi", "render_dpi"), [(150, 300), (400, 400)])
def test_a_scanned_page_is_rendered_at_its_scan_s_resolution_within_range(
    tmp_path: Path, scan_dpi: int, render_dpi: float
) -> None:
    path = write_pdf(tmp_path / "scan.pdf", [a4_scan(scan_dpi)])

    (page,) = raster.plan(path, min_dpi=300, max_dpi=600)

    assert page.reason is Reason.SCANNED
    assert page.ocr
    assert page.dpi == pytest.approx(render_dpi, abs=0.5)
    assert (page.width, page.height) == pytest.approx(A4, abs=0.01)


@pytest.mark.parametrize(
    ("pixels", "render_dpi"),
    [((9921, 14031), 600), ((14031, 9921), 600), ((2480, 3508), 300), (None, 300)],
)
def test_render_dpi_follows_the_scan_within_range(
    pixels: tuple[int, int] | None, render_dpi: float
) -> None:
    # An A4 scan at 1200 dpi (placed upright or turned), at 300, and of an unknown size.
    image = PageImage(coverage=1.0, width=A4[0], height=A4[1], pixels=pixels)

    assert raster.render_dpi(image, min_dpi=300, max_dpi=600) == pytest.approx(render_dpi, abs=0.5)


def test_pages_are_read_by_ocr_unless_they_have_text_and_aren_t_scans(tmp_path: Path) -> None:
    path = write_pdf(
        tmp_path / "mixed.pdf",
        [a4_scan(100), a4_scan(100), None, None, scan((100, 100))],
        texts=[None, "スキャナーが付けた文字", "本文", None, "小さい画像のある本文"],
        image_box=None,
    )

    plans = raster.plan(path)

    assert [(p.page, p.reason, p.ocr) for p in plans] == [
        (1, Reason.SCANNED, True),
        (2, Reason.SCANNED_WITH_TEXT, True),
        (3, Reason.TEXT, False),
        (4, Reason.NO_TEXT, True),
        # The small image is drawn over the whole page here; see the next test.
        (5, Reason.SCANNED_WITH_TEXT, True),
    ]
    # Not scanned: rendered at the lowest resolution, if forced.
    assert plans[2].dpi == 300


def test_a_page_whose_characters_have_no_codes_is_read_by_ocr(tmp_path: Path) -> None:
    (plan,) = raster.plan(unmapped_pdf(tmp_path / "unmapped.pdf"))

    assert (plan.reason, plan.ocr) == (Reason.UNMAPPED_TEXT, True)


@pytest.mark.parametrize(
    ("chars", "readable"),
    [
        (["(cid:15)", "(cid:16)"], False),
        ([" ", ""], False),
        (["(cid:15)", "円"], True),  # some can be read: the text is used
        (["(cid:x)"], True),
    ],
)
def test_text_is_readable_unless_every_character_lacks_its_code(
    chars: list[str], readable: bool
) -> None:
    assert raster.has_readable_text(chars) is readable


def test_a_small_image_doesn_t_make_a_page_a_scan(tmp_path: Path) -> None:
    path = write_pdf(
        tmp_path / "logo.pdf", [scan((100, 100))], texts=["請求書"], image_box=(50, 50, 100, 100)
    )

    (page,) = raster.plan(path)

    assert page.reason is Reason.TEXT


def test_render_draws_the_page_at_its_resolution(tmp_path: Path) -> None:
    # A box 1 inch from the left and 2 from the top, 1 by 0.5 inch, scanned at 200 dpi.
    path = write_pdf(tmp_path / "scan.pdf", [a4_scan(200, [(200, 400, 399, 499)])])
    (page,) = raster.plan(path, min_dpi=300, max_dpi=600)

    image = raster.render(path, page)

    # pdfium rounds the size up.
    assert image.size == pytest.approx((A4[0] * 300 / 72, A4[1] * 300 / 72), abs=1)
    box = dark_box(image)
    assert box is not None
    # Back in points: 72 from the left, 144 from the top.
    scale = page.width / image.width
    assert [v * scale for v in box] == pytest.approx([72, 144, 144, 180], abs=1.5)


def test_a_rotated_page_is_planned_and_rendered_as_it_is_shown(tmp_path: Path) -> None:
    path = write_pdf(tmp_path / "rotated.pdf", [a4_scan(150, [(0, 0, 149, 74)])], rotations=[90])

    (page,) = raster.plan(path)
    image = raster.render(path, page)

    assert page.reason is Reason.SCANNED
    assert page.dpi == 300  # 150 dpi, however the image is turned, raised to the lowest
    assert (page.width, page.height) == pytest.approx((A4[1], A4[0]), abs=0.01)
    assert image.width > image.height
    # The box at the scan's top left is at the top right once turned clockwise.
    box = dark_box(image)
    assert box is not None
    assert box[0] > image.width * 0.8
    assert box[1] < image.height * 0.1


def test_a_protected_pdf_is_planned_and_rendered_with_its_password(tmp_path: Path) -> None:
    path = write_pdf(tmp_path / "locked.pdf", [a4_scan(300)], password="secret")

    (page,) = raster.plan(path, password="secret")
    image = raster.render(path, page, password="secret")

    assert page.reason is Reason.SCANNED
    assert image.width == pytest.approx(A4[0] * 300 / 72, abs=1)


def test_an_image_file_keeps_its_pixels_and_its_resolution_gives_its_size(
    tmp_path: Path,
) -> None:
    path = tmp_path / "receipt.png"
    scan((400, 1000), [(10, 10, 50, 50)]).save(path, dpi=(200, 200))

    (page,) = raster.plan(path)
    image = raster.render(path, page)

    assert (page.reason, page.dpi) == (Reason.IMAGE, 200)
    assert (page.width, page.height) == pytest.approx((144, 360), abs=0.01)
    assert image.size == (400, 1000)


@pytest.mark.parametrize("dpi", [None, (72, 72), (1, 1)])
def test_an_image_without_a_believable_resolution_is_taken_at_300_dpi(
    tmp_path: Path, dpi: tuple[int, int] | None
) -> None:
    path = tmp_path / "photo.jpg"
    image = scan((600, 300))
    if dpi is None:
        image.save(path)
    else:
        image.save(path, dpi=dpi)

    (page,) = raster.plan(path)

    assert page.dpi == raster.DEFAULT_IMAGE_DPI
    assert (page.width, page.height) == pytest.approx((144, 72), abs=0.01)


def test_an_image_is_turned_upright_by_its_exif_orientation(tmp_path: Path) -> None:
    path = tmp_path / "phone.jpg"
    exif = Image.Exif()
    exif[0x0112] = 6  # shown turned 90 degrees clockwise
    scan((600, 300), [(0, 0, 59, 29)]).save(path, exif=exif)

    (page,) = raster.plan(path)
    image = raster.render(path, page)

    assert (page.width, page.height) == pytest.approx((72, 144), abs=0.01)
    assert image.size == (300, 600)
    box = dark_box(image)
    assert box is not None
    assert box[0] > 250  # the top left box is at the top right once turned


def test_each_page_of_a_tiff_is_a_page(tmp_path: Path) -> None:
    path = tmp_path / "pages.tiff"
    first, second = scan((300, 400)), scan((500, 400))
    first.save(path, save_all=True, append_images=[second], dpi=(300, 300))

    plans = raster.plan(path)

    assert [p.page for p in plans] == [1, 2]
    assert raster.render(path, plans[1]).size == (500, 400)


def test_an_unreadable_image_raises_os_error(tmp_path: Path) -> None:
    path = tmp_path / "broken.png"
    path.write_bytes(b"not an image")

    with pytest.raises(OSError):
        raster.plan(path)


@pytest.mark.parametrize("clockwise", [0, 90, 180, 270])
def test_a_box_on_a_turned_image_is_turned_back_onto_the_image(clockwise: int) -> None:
    image = scan((300, 200), [(30, 20, 89, 49)])
    turned = raster.turn(image, clockwise)
    box = dark_box(turned)
    assert box is not None

    back = raster.turn_back(box, clockwise, image.size)

    assert back == pytest.approx((30, 20, 90, 50))
    assert turned.size == (image.size if clockwise in (0, 180) else (200, 300))
