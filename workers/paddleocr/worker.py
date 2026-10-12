"""scriptorium's OCR worker for PaddleOCR (https://github.com/PaddlePaddle/PaddleOCR).

Reads each page with PaddleOCR's general OCR pipeline (PP-OCRv6 by
default, one model for Japanese, Chinese, English and Latin scripts); each
line it reads is a box, around its four corners, with its recognition score
as the confidence. The corners are kept in the extra output.

The pipeline's own turning and unwarping of the page are off: its boxes
would then be on the turned or unwarped image, not the page. Each line's
own direction (upside down or not) is on.

Its models are downloaded the first time. On Windows, the CUDA libraries
installed in its environment (NVIDIA's wheels) are put first on PATH, where
PaddlePaddle looks for them.

On the CPU, oneDNN (MKLDNN, for Intel and AMD CPUs) is off: the text
detection model fails with it on Windows (ConvertPirAttribute2RuntimeAttribute
not supported). Without it a page takes 20-60 seconds; macOS's PaddlePaddle
has no oneDNN anyway, so what it reads there is the same.

Options (scriptorium's list of engines): lang (default "japan"),
ocr_version (default "PP-OCRv6"), use_textline_orientation (default true),
text_det_limit_side_len, text_det_limit_type, text_rec_score_thresh.
"""

import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ocr_worker

_PASSED = ("text_det_limit_side_len", "text_det_limit_type", "text_rec_score_thresh")


def _find_nvidia_libraries() -> None:
    """Have the CUDA libraries of this environment found first (Windows): PaddlePaddle
    loads them by name, so from PATH, where a CUDA installed apart may be another one."""
    try:
        import nvidia  # the namespace of NVIDIA's wheels (nvidia/<library>/bin/*.dll)
    except ImportError:
        return
    folders = sorted(
        {str(dll.parent) for root in nvidia.__path__ for dll in Path(root).glob("*/bin/*.dll")}
    )
    os.environ["PATH"] = os.pathsep.join([*folders, os.environ.get("PATH", "")])
    for folder in folders:
        os.add_dll_directory(folder)


if sys.platform == "win32":
    _find_nvidia_libraries()


def _devices() -> list[str]:
    import paddle

    if paddle.device.is_compiled_with_cuda() and paddle.device.cuda.device_count() > 0:
        return ["cuda", "cpu"]
    return ["cpu"]


class PaddleOcr:
    name = "paddleocr"

    def __init__(self, device: str, options: dict[str, Any]) -> None:
        import numpy
        import paddle
        import paddleocr
        from PIL import Image

        self.device = ocr_worker.pick_device(device, _devices())
        lang = options.get("lang", "japan")
        ocr_version = options.get("ocr_version", "PP-OCRv6")
        self.version = f"{paddleocr.__version__} (PaddlePaddle {paddle.__version__})"
        self.model = f"{ocr_version}, {lang}"
        self.pipeline = paddleocr.PaddleOCR(
            lang=lang,
            ocr_version=ocr_version,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=bool(options.get("use_textline_orientation", True)),
            device="gpu:0" if self.device == "cuda" else "cpu",
            **({"enable_mkldnn": False} if self.device == "cpu" else {}),
            **{k: options[k] for k in _PASSED if k in options},
        )
        self.numpy, self.image = numpy, Image

    def read(self, image: Path) -> ocr_worker.Result:
        # Opened here (not by path) so that a path in Japanese works on Windows too;
        # BGR, as OpenCV's images that PaddleOCR takes.
        with self.image.open(image) as opened:
            rgb = self.numpy.asarray(opened.convert("RGB"))
        bgr = self.numpy.ascontiguousarray(rgb[:, :, ::-1])
        (result,) = self.pipeline.predict(bgr)
        data = result.json["res"]
        boxes = [
            ocr_worker.Box.around(text, polygon, round(float(score), 4))
            for text, score, polygon in zip(
                data["rec_texts"], data["rec_scores"], data["rec_polys"], strict=True
            )
        ]
        return ocr_worker.Result(boxes, {"polys": data["rec_polys"]})


if __name__ == "__main__":
    ocr_worker.serve(PaddleOcr)
