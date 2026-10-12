"""The fictional samples: drawn with Pillow's own font here (the tests can't count on a
Japanese one), so Japanese comes out as boxes, but where text lands is what's tested."""

from pathlib import Path

import pytest
from PIL import Image, ImageFont

from scriptorium import fictional, raster, samples
from scriptorium.samples import Variant


def font(size: int) -> samples.Font:
    return ImageFont.load_default(size)


def written(tmp_path: Path, *variants: Variant, document: str = "receipt") -> list[Path]:
    documents = {document: fictional.DOCUMENTS[document]}
    return list(samples.write(tmp_path, font, documents, count=1, variants=variants))


def dark_share(image: Image.Image, box: tuple[float, float, float, float]) -> float:
    """The share of dark pixels in `box` of `image`."""
    crop = image.convert("L").crop(box)
    histogram = crop.histogram()
    return sum(histogram[:128]) / max(sum(histogram), 1)


@pytest.mark.parametrize("document", list(fictional.DOCUMENTS))
@pytest.mark.parametrize(
    "variant",
    [
        Variant("300"),
        Variant("150", dpi=150),
        # Drawn at a low resolution where it doesn't matter, to be quick.
        Variant("turned90", dpi=150, turn=90),
        Variant("slant", dpi=150, rotate=3),
    ],
    ids=lambda v: v.name,
)
def test_the_truth_s_boxes_are_where_the_text_is(
    tmp_path: Path, document: str, variant: Variant
) -> None:
    (path,) = written(tmp_path, variant, document=document)
    truth = samples.load_truth(path)
    assert truth is not None
    (page,) = raster.plan(path)
    image = raster.render(path, page)

    assert (truth.width, truth.height) == pytest.approx((page.width, page.height), abs=0.5)
    scale = image.width / truth.width
    for line in truth.lines:
        box = (line.x0 * scale, line.top * scale, line.x1 * scale, line.bottom * scale)
        assert dark_share(image, box) > 0.02, line
    assert {line.kind for line in truth.lines} >= {"amount", "date", "text"}


def test_a_document_has_the_same_values_in_every_variant_and_run(tmp_path: Path) -> None:
    first = written(
        tmp_path / "a", Variant("150", dpi=150), Variant("noise", dpi=150, effects=("noise",))
    )
    again = written(tmp_path / "b", Variant("150", dpi=150))

    truths = [samples.load_truth(p) for p in (*first, *again)]
    texts = [[line.text for line in t.lines] if t else None for t in truths]

    assert texts[0] is not None
    assert texts[0] == texts[1] == texts[2]


def test_variants_are_saved_as_their_files_are(tmp_path: Path) -> None:
    paths = written(
        tmp_path,
        Variant("jpeg", suffix=".jpg", jpeg_quality=50),
        Variant("pdf", dpi=150, suffix=".pdf"),
        Variant("bw", effects=("bw",)),
    )

    assert [p.name for p in paths] == [
        "receipt-1-jpeg.jpg",
        "receipt-1-pdf.pdf",
        "receipt-1-bw.png",
    ]
    assert all(samples.truth_path_of(p).exists() for p in paths)
    (pdf_page,) = raster.plan(paths[1])
    assert pdf_page.reason is raster.Reason.SCANNED
    assert pdf_page.dpi == 300  # a 150 dpi scan, read at 300
    with Image.open(paths[2]) as bw:
        assert bw.mode == "1"


def test_a_sample_without_a_truth_has_none(tmp_path: Path) -> None:
    assert samples.load_truth(tmp_path / "scan.png") is None
