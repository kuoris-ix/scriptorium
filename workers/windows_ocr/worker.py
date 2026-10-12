"""scriptorium's OCR worker for the OCR built into Windows (Windows.Media.Ocr, through winocr).

Needs Windows' OCR for Japanese, which a Japanese Windows has; otherwise
add it in Settings (Time & language > Language), or in PowerShell as an
administrator: Add-WindowsCapability -Online -Name "Language.OCR~~~ja-JP~0.0.1.0".

Each word it reads is a box (Japanese words are often a character or
two); Windows' OCR has no confidence. The line of each box, and the angle
of the text, are kept in the extra output.

Options (scriptorium's list of engines): language (default "ja").
"""

import platform
import sys
from importlib.metadata import version
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ocr_worker


class WindowsOcr:
    name = "windows-ocr"
    model = None

    def __init__(self, device: str, options: dict[str, Any]) -> None:
        self.device = ocr_worker.pick_device(device, ["cpu"])
        import winocr
        from PIL import Image
        from winrt.windows.globalization import Language
        from winrt.windows.media.ocr import OcrEngine

        self.language = str(options.get("language", "ja"))
        if not OcrEngine.is_language_supported(Language(self.language)):
            raise ocr_worker.StartError(
                f"Windows has no OCR for {self.language!r}; add it in Settings > Time & language"
            )
        self.max_side = int(OcrEngine.max_image_dimension)
        self.version = f"winocr {version('winocr')}, Windows {platform.version()}"
        self.winocr, self.image = winocr, Image

    def read(self, image: Path) -> ocr_worker.Result:
        with self.image.open(image) as opened:
            picture = opened.convert("RGBA")
        if max(picture.size) > self.max_side:
            raise ValueError(
                f"the image is {picture.width}x{picture.height}; Windows' OCR reads up to "
                f"{self.max_side} pixels a side (lower the maximum dpi pages are rendered at)"
            )
        result = self.winocr.recognize_pil_sync(picture, self.language)
        boxes, lines = [], []
        for number, line in enumerate(result.get("lines") or []):
            for word in line.get("words") or []:
                rect = word["bounding_rect"]
                x, y = float(rect["x"]), float(rect["y"])
                boxes.append(
                    ocr_worker.Box(
                        word["text"], x, y, x + float(rect["width"]), y + float(rect["height"])
                    )
                )
                lines.append(number)
        # The line of each box, in the same order.
        return ocr_worker.Result(boxes, {"lines": lines, "text_angle": result.get("text_angle")})


if __name__ == "__main__":
    ocr_worker.serve(WindowsOcr)
