"""scriptorium's OCR worker for Apple's Vision framework, built into macOS (through ocrmac).

Each piece of text Vision reads is a box, with its confidence (always 1
with the LiveText framework). It runs on Apple's own hardware, so the
device is told as "apple".

Options (scriptorium's list of engines): framework ("vision", the default,
or "livetext", macOS 14 and later), recognition_level ("accurate", the
default, or "fast"), languages (default ["ja-JP", "en-US"]).
"""

import platform
import sys
from importlib.metadata import version
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ocr_worker


class AppleVision:
    name = "apple-vision"
    device = "apple"

    def __init__(self, device: str, options: dict[str, Any]) -> None:
        if device not in ("auto", "mps"):
            raise ocr_worker.StartError(
                "Vision picks the CPU, GPU or Neural Engine itself: use the device auto"
            )
        from ocrmac import ocrmac
        from PIL import Image

        self.framework = str(options.get("framework", "vision"))
        self.level = str(options.get("recognition_level", "accurate"))
        self.languages = list(options.get("languages", ["ja-JP", "en-US"]))
        self.version = f"ocrmac {version('ocrmac')}, macOS {platform.mac_ver()[0]}"
        self.model = self.framework if self.framework == "livetext" else f"vision, {self.level}"
        self.ocrmac, self.image = ocrmac, Image

    def read(self, image: Path) -> ocr_worker.Result:
        with self.image.open(image) as opened:
            picture = opened.convert("RGB")
        options: dict[str, Any] = {"framework": self.framework}
        if self.framework != "livetext":
            options["recognition_level"] = self.level
        found = self.ocrmac.OCR(picture, language_preference=self.languages, **options)
        # In pixels from the top left (ocrmac turns Vision's bottom-left fractions so).
        boxes = [
            ocr_worker.Box(text, x0, y0, x1, y1, round(float(confidence), 4))
            for text, confidence, (x0, y0, x1, y1) in found.recognize(px=True)
        ]
        return ocr_worker.Result(boxes)


if __name__ == "__main__":
    ocr_worker.serve(AppleVision)
