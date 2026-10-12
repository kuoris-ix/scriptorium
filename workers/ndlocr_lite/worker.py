"""scriptorium's OCR worker for NDLOCR-Lite (https://github.com/ndl-lab/ndlocr-lite).

Loads NDLOCR-Lite's models once, when it starts (those its `ndlocr-lite`
command loads, with the command's settings), and reads each page with them
as the command reads an image, with the functions of its `ocr` module (as
they are at 1.2.3, which pyproject.toml pins): each line is a box, around
its corners, with NDLOCR-Lite's confidence (of finding the line, not of
reading it). Whether each line is vertical is kept in the extra output.

Running the command on each page instead loads the models each time: about
9 seconds a page on the CPU, against under 1 second.

Options (scriptorium's list of engines): none yet.
"""

import argparse
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ocr_worker


def _version() -> str:
    for name in ("ndlocr-lite", "ndlocr_lite"):
        try:
            return version(name)
        except PackageNotFoundError:
            continue
    return "unknown"


def _command_settings(ocr: Any) -> argparse.Namespace:
    """The settings `ndlocr-lite` runs with by default (the models' files, the thresholds).

    Its `main` makes them from its command line and hands them to `process`, which loads
    the models and reads; here `process` only keeps them.
    """
    kept: list[argparse.Namespace] = []
    process, argv = ocr.process, sys.argv
    ocr.process, sys.argv = kept.append, ["ndlocr-lite", "--output", "."]
    try:
        ocr.main()
    finally:
        ocr.process, sys.argv = process, argv
    return kept[0]


class NdlOcrLite:
    name = "ndlocr-lite"
    model = None

    def __init__(self, device: str, options: dict[str, Any]) -> None:
        import numpy
        import ocr  # NDLOCR-Lite's module, which its `ndlocr-lite` command runs
        from PIL import Image

        self.device = ocr_worker.pick_device(device, ["cpu"])
        self.version = _version()
        settings = _command_settings(ocr)
        settings.device = self.device
        # As the command loads them (ocr.process): one detector, and recognizers for
        # lines of up to 30, 50 and 100 characters.
        self.detector = ocr.get_detector(settings)
        self.recognizers = (
            ocr.get_recognizer(settings, weights_path=settings.rec_weights30),
            ocr.get_recognizer(settings, weights_path=settings.rec_weights50),
            ocr.get_recognizer(settings),
        )
        self.ocr, self.numpy, self.image = ocr, numpy, Image

    def read(self, image: Path) -> ocr_worker.Result:
        with self.image.open(image) as opened:
            pixels = self.numpy.array(opened.convert("RGB"))  # RGB, as the command reads it
        recognizer30, recognizer50, recognizer100 = self.recognizers
        # The command reads each page of a PDF with this; each image, with the same
        # steps written out in ocr.process.
        page = self.ocr._run_ocr_on_image_array(
            detector=self.detector,
            recognizer30=recognizer30,
            recognizer50=recognizer50,
            recognizer100=recognizer100,
            inputname=image.name,
            img=pixels,
            outputpath=str(image.parent),
            save_viz=False,
        )
        lines = page["json_lines"]
        boxes = [
            ocr_worker.Box.around(
                str(line["text"]), line["boundingBox"], float(line.get("confidence", 0.0))
            )
            for line in lines
        ]
        vertical = [line.get("isVertical") == "true" for line in lines]
        return ocr_worker.Result(boxes, {"vertical": vertical})


if __name__ == "__main__":
    ocr_worker.serve(NdlOcrLite)
