"""The workers' shared module (workers/ocr_worker.py), as far as it runs without an engine."""

import ocr_worker
import pytest


def test_a_box_around_slanted_text_holds_all_its_corners() -> None:
    box = ocr_worker.Box.around("合計", [[10, 22], [110, 20], [112, 40], [12, 42]], 0.5)

    assert (box.x0, box.y0, box.x1, box.y1, box.confidence) == (10, 20, 112, 42, 0.5)


@pytest.mark.parametrize(
    ("requested", "available", "picked"),
    [
        ("auto", ["cuda", "cpu"], "cuda"),
        ("auto", ["cpu"], "cpu"),
        ("cpu", ["cuda", "cpu"], "cpu"),
        ("mps", ["mps", "cpu"], "mps"),
    ],
)
def test_pick_device(requested: str, available: list[str], picked: str) -> None:
    assert ocr_worker.pick_device(requested, available) == picked


@pytest.mark.parametrize(
    ("requested", "message"),
    [("cuda", "isn't available here"), ("gpu", "unknown device")],
)
def test_pick_device_fails_rather_than_use_another(requested: str, message: str) -> None:
    with pytest.raises(ocr_worker.StartError, match=message):
        ocr_worker.pick_device(requested, ["cpu"])
