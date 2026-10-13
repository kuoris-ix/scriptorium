"""scriptorium's OCR worker for YomiToku (https://github.com/kotaro-kinoshita/yomitoku).

Reads each page with YomiToku's DocumentAnalyzer, as its `yomitoku`
command does; each word it reads (a run of text, often a line) is a box,
with its recognition score as the confidence. The paragraphs, tables
(their cells, rows and columns) and figures are kept in the extra output,
and each word's direction, detection score and corners.

Its models are downloaded from Hugging Face the first time.

DocumentAnalyzer reads each page in two threads (the text, and the layout)
of a pool it makes for that page. PyTorch keeps some memory for each thread
that runs a model on the GPU and doesn't give it back when the thread ends,
so with a new pool for each page the worker grew by about 1.6MB a page; it
is given one pool, kept for every page.

On CUDA, PyTorch is given one thread on the CPU (unless OMP_NUM_THREADS is
set): the models run on the GPU, and a thread for every core only keeps
the CPU busy, without reading faster.

Options (scriptorium's list of engines): lite (YomiToku's lighter models,
as `yomitoku --lite`), and DocumentAnalyzer's reading_order,
split_text_across_cells, ignore_meta and ignore_ruby.
"""

import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ocr_worker

_ANALYZER_OPTIONS = ("reading_order", "split_text_across_cells", "ignore_meta", "ignore_ruby")
# The models `yomitoku` uses (yomitoku/cli/main.py), the lite ones with --lite.
_MODELS = {False: "parseq-middle-dynw-v5", True: "parseq-tiny-dynw-v5"}
_DETECTOR = "dbnetv2_1"


class _KeptPool:
    """Stands for ThreadPoolExecutor where DocumentAnalyzer makes one for each page
    (`with ThreadPoolExecutor(max_workers=2) as executor:`): the same pool each time."""

    def __init__(self) -> None:
        self.pool: ThreadPoolExecutor | None = None

    def __call__(self, max_workers: int) -> "_KeptPool":
        if self.pool is None:
            self.pool = ThreadPoolExecutor(max_workers=max_workers)
        return self

    def __enter__(self) -> ThreadPoolExecutor:
        assert self.pool is not None
        return self.pool

    def __exit__(self, *exc: object) -> None:
        pass  # not shut down: kept for the next page


class YomiToku:
    name = "yomitoku"

    def __init__(self, device: str, options: dict[str, Any]) -> None:
        import yomitoku
        import yomitoku.document_analyzer
        from yomitoku import DocumentAnalyzer
        from yomitoku.data.functions import load_image

        yomitoku.document_analyzer.ThreadPoolExecutor = _KeptPool()  # see above
        self.device = ocr_worker.pick_device(device, ocr_worker.torch_devices())
        if self.device == "cuda" and "OMP_NUM_THREADS" not in os.environ:
            import torch

            torch.set_num_threads(1)  # see above
        self.version = yomitoku.__version__
        lite = bool(options.get("lite", False))
        recognizer: dict[str, Any] = {"model_name": _MODELS[lite]}
        detector: dict[str, Any] = {"model_name": _DETECTOR}
        if lite:
            # As `yomitoku --lite` sets them.
            recognizer["source_downscale"] = True
            if self.device == "cpu":
                detector["infer_onnx"] = True
                recognizer["num_parallel_batches"] = 4
        self.model = f"{_DETECTOR} + {_MODELS[lite]}"
        self.analyzer = DocumentAnalyzer(
            configs={"ocr": {"text_detector": detector, "text_recognizer": recognizer}},
            device=self.device,
            visualize=False,
            **{k: options[k] for k in _ANALYZER_OPTIONS if k in options},
        )
        self.load_image = load_image

    def read(self, image: Path) -> ocr_worker.Result:
        (page,) = self.load_image(str(image))  # BGR, as YomiToku reads it
        results, _, _ = self.analyzer(page)
        boxes = [
            ocr_worker.Box.around(word.content, word.points, round(float(word.rec_score), 4))
            for word in results.words
        ]
        words = [
            {"direction": w.direction, "det_score": w.det_score, "points": w.points}
            for w in results.words
        ]
        layout = results.model_dump(mode="json", exclude={"words"})
        return ocr_worker.Result(boxes, {"words": words, **layout})


if __name__ == "__main__":
    ocr_worker.serve(YomiToku)
