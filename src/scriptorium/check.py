"""Checking that an engine works here: start it and have it read one fictional page.

`ocr check-engines` uses it, to tell after installing the
engines which of them run (and on which device), before leaving them to
read many pages.
"""

import random
import time
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import IO

from scriptorium import samples
from scriptorium.compare import Tally, score_page
from scriptorium.engines import Engine
from scriptorium.models import EngineInfo, OcrPage, OcrWord
from scriptorium.samples import Document, FontLoader, Truth, Variant
from scriptorium.worker import PageFailed, Worker, WorkerFailed, load_result

_DPI = 300


@dataclass(frozen=True)
class Checked:
    engine: EngineInfo | None  # None if it didn't start
    error: str | None = None
    start_seconds: float = 0.0
    read_seconds: float = 0.0
    amounts: tuple[int, int] = (0, 0)  # read exactly, of
    tells_turns: bool = False


def sample_page(folder: Path, font: FontLoader, document: Document) -> tuple[Path, Truth]:
    """A fictional `document` as a PNG in `folder`, and its truth (in pixels)."""
    image, lines = samples.make(document, random.Random("check"), Variant("300"), font)
    path = folder / "page-1.png"  # named as the runner names pages
    image.save(path, dpi=(_DPI, _DPI))
    return path, Truth(path.name, float(image.width), float(image.height), lines)


def check(
    engine: Engine,
    command: list[str],
    *,
    device: str,
    image: Path,
    truth: Truth,
    log: IO[str],
    not_passed: Sequence[str] = (),
) -> Checked:
    """Start `engine`'s worker with `command` and have it read `image` (of `truth`).

    The worker isn't given the environment variables named in `not_passed`.
    """
    started = time.perf_counter()
    try:
        worker = Worker(command, log, not_passed=not_passed)
    except WorkerFailed as e:
        return Checked(None, f"couldn't start: {e}")
    with worker:
        try:
            info = worker.start(device, engine.options)
        except WorkerFailed as e:
            return Checked(None, f"couldn't start: {e}")
        start_seconds = time.perf_counter() - started
        output = image.with_suffix(".json")
        try:
            read_started = time.perf_counter()
            worker.read("check", image, output)
            read_seconds = time.perf_counter() - read_started
            result = load_result(output)
        except (PageFailed, ValueError) as e:
            return Checked(info, f"couldn't read the page: {e}", start_seconds)
        except WorkerFailed as e:
            return Checked(info, f"stopped reading the page: {e}", start_seconds)
        finally:
            output.unlink(missing_ok=True)
        # The truth is in pixels, so the page is too.
        words = [OcrWord(b.text, b.x0, b.x1, b.y0, b.y1, b.confidence) for b in result.boxes]
        tally = Tally()
        tally.add(score_page(truth, OcrPage(1, truth.width, truth.height, _DPI, words)))
        checked = Checked(
            info,
            None,
            start_seconds,
            read_seconds,
            (tally.exact_by_kind["amount"], tally.by_kind["amount"]),
        )
        try:
            worker.orient("check", image)
        except PageFailed:
            return checked
        except WorkerFailed as e:
            return replace(checked, error=f"stopped telling how the page is turned: {e}")
    return replace(checked, tells_turns=True)
