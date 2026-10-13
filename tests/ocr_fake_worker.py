"""A fake OCR worker for the tests, talking with scriptorium through the real ocr_worker.

Started as `python ocr_fake_worker.py`, it reads every page as the boxes
the test tells it, in the variable FAKE_OCR (JSON), knowing the page by the
image's name (page-<number>-<the file's hash>.png, as the runner names it):

    boxes      the boxes it reads on every page, in pixels (default: one; an engine's
               option "boxes" comes first, for two fake engines reading differently)
    fail       page numbers it can't read
    crash      page numbers it stops on, as a worker that crashes would
    fatal      a reason it can't start
    devices    the devices it has (default: ["cpu"])
    chatter    something it prints to stdout, as engines' libraries do
    turns      how each page is turned, {"<page>": [clockwise, confidence]} (default: upright)
    no_orient  that it can't tell how pages are turned
    crash_orient  that it stops when asked how a page is turned
"""

import json
import os
import sys
from pathlib import Path
from typing import Any

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "workers"))

import ocr_worker

DEFAULT_BOXES = [
    {"text": "合計 ¥1,234", "x0": 10, "y0": 20, "x1": 110, "y1": 40, "confidence": 0.9}
]


def _page(image: Path) -> int:
    return int(image.stem.removeprefix("page-").split("-")[0])


class FakeEngine:
    name = "fake"
    version = "1.0"
    model = "fake-model"

    def __init__(self, device: str, options: dict[str, Any], behaviour: dict[str, Any]) -> None:
        self.device = device
        self.options = options
        self.behaviour = behaviour

    def read(self, image: Path) -> ocr_worker.Result:
        page = _page(image)
        if page in self.behaviour.get("crash", []):
            os._exit(3)
        if page in self.behaviour.get("fail", []):
            raise RuntimeError(f"can't read page {page}")
        if "chatter" in self.behaviour:
            # As a library would: straight to the process's stdout.
            os.write(1, (self.behaviour["chatter"] + "\n").encode())
        with Image.open(image) as opened:
            size = opened.size
        boxes = [
            ocr_worker.Box(**b)
            for b in self.options.get("boxes") or self.behaviour.get("boxes", DEFAULT_BOXES)
        ]
        return ocr_worker.Result(boxes, {"options": self.options, "size": list(size)})


class OrientingFakeEngine(FakeEngine):
    def orient(self, image: Path) -> tuple[int, float]:
        if self.behaviour.get("crash_orient"):
            os._exit(4)
        page = str(_page(image))
        clockwise, confidence = self.behaviour.get("turns", {}).get(page, [0, 9.0])
        return int(clockwise), float(confidence)


def start(device: str, options: dict[str, Any]) -> FakeEngine:
    behaviour = json.loads(os.environ.get("FAKE_OCR", "{}"))
    if "fatal" in behaviour:
        raise ocr_worker.StartError(behaviour["fatal"])
    picked = ocr_worker.pick_device(device, behaviour.get("devices", ["cpu"]))
    engine = FakeEngine if behaviour.get("no_orient") else OrientingFakeEngine
    return engine(picked, options, behaviour)


if __name__ == "__main__":
    ocr_worker.serve(start)
