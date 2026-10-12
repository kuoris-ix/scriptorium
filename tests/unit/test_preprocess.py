import random

import pytest
import sample_documents
from ocr_files import dark_box, scan
from PIL import Image, ImageChops, ImageFont, ImageStat

from scriptorium import preprocess, samples
from scriptorium.samples import Variant


def receipt(variant: Variant) -> Image.Image:
    image, _ = samples.make(
        sample_documents.receipt, random.Random("1"), variant, ImageFont.load_default
    )
    return image


@pytest.mark.parametrize("slant", [-3.0, 2.0, 0.0])
def test_the_slant_of_a_page_is_found(slant: float) -> None:
    image = receipt(Variant("slant", dpi=150, rotate=slant))

    assert preprocess.skew(image) == pytest.approx(-slant, abs=0.3)


def test_a_straight_page_is_left_as_it_is() -> None:
    image = receipt(Variant("150", dpi=150))

    straightened, back = preprocess.deskew(image)

    assert straightened is image
    assert back((1, 2, 3, 4)) == (1, 2, 3, 4)


def test_a_slanted_page_is_straightened() -> None:
    image = receipt(Variant("slant", dpi=150, rotate=-3))

    straightened, _ = preprocess.deskew(image)

    assert preprocess.skew(straightened) == pytest.approx(0, abs=0.3)


def test_a_box_on_a_rotated_image_comes_back_where_it_was() -> None:
    image = scan((600, 400), [(100, 50, 199, 99)])

    turned, back = preprocess.rotated(image, 4)
    box = dark_box(turned)
    assert box is not None
    x0, y0, x1, y1 = back(box)

    # Back around the box (a little larger: the box around a turned box).
    assert ((x0 + x1) / 2, (y0 + y1) / 2) == pytest.approx((150, 75), abs=1.5)
    assert x0 <= 101 and y0 <= 51 and x1 >= 199 and y1 >= 99
    assert turned.width > image.width


def test_flatten_evens_out_a_shadow_and_keeps_the_text() -> None:
    page = scan((800, 1000), [(100, 100, 299, 139), (100, 800, 299, 839)])
    shade = Image.linear_gradient("L").resize(page.size).point(lambda v: 255 - v * 120 // 255)
    shadowed = ImageChops.multiply(page.convert("L"), shade)

    flat, back = preprocess.flatten(shadowed)

    def mean(image: Image.Image, box: tuple[int, int, int, int]) -> float:
        return float(ImageStat.Stat(image.crop(box)).mean[0])

    # The paper at the top and at the bottom was 255 and about 135; now about the same.
    assert mean(shadowed, (400, 0, 800, 50)) - mean(shadowed, (400, 950, 800, 1000)) > 100
    assert abs(mean(flat, (400, 0, 800, 50)) - mean(flat, (400, 950, 800, 1000))) < 15
    assert mean(flat, (120, 810, 280, 830)) < 40  # the text in the shadow is still dark
    assert flat.mode == "L"
    assert back((1, 2, 3, 4)) == (1, 2, 3, 4)


def test_steps_are_known_by_name() -> None:
    assert [s.name for s in preprocess.steps(["flatten", "deskew"])] == ["flatten", "deskew"]
    with pytest.raises(KeyError, match="use: flatten, deskew"):
        preprocess.steps(["sharpen"])


def test_the_way_back_goes_through_the_steps_in_reverse() -> None:
    def shift(image: Image.Image) -> tuple[Image.Image, preprocess.Back]:
        return image, lambda b: (b[0] - 10, b[1], b[2] - 10, b[3])

    def double(image: Image.Image) -> tuple[Image.Image, preprocess.Back]:
        return image, lambda b: (b[0] / 2, b[1] / 2, b[2] / 2, b[3] / 2)

    steps = [preprocess.Step("shift", "", shift), preprocess.Step("double", "", double)]
    _, back = preprocess.apply(scan((10, 10)), steps)

    # Doubled last, so halved first, then shifted back.
    assert back((40, 40, 60, 60)) == (10, 20, 20, 30)
