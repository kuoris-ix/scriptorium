"""Reading files by OCR: which pages, with which engine, kept in the store.

`ocr run` is the only thing that runs the engines; everything
else reads what's kept (see `store`). A run may take hours and be stopped
at any point: each page is kept as soon as it's read, and a run again
reads only the pages not read yet.

With an engine to tell how pages are turned (`orient`), each page is first
told (and that's kept too), then turned upright before it's read; then come
the preprocessing steps asked for (`preprocess`). What's read is put back
through them all onto the page as it is.
"""

import hashlib
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import IO

from scriptorium import preprocess, raster
from scriptorium.engines import Engine
from scriptorium.files import find_files
from scriptorium.models import (
    ORIENT_MIN_CONFIDENCE,
    EngineInfo,
    OcrPage,
    OcrWord,
    Orientation,
    StoredPage,
)
from scriptorium.raster import PagePlan
from scriptorium.store import RunKey, Store
from scriptorium.worker import PageFailed, Result, Worker, WorkerFailed, load_result

# How to start an engine's worker, given the workers' folder (see `worker.uv_command`).
Launcher = Callable[[Engine, Path], list[str]]
Say = Callable[[str], None]

# A worker that stops this many times in a row (each time on a page) isn't started again.
MAX_STOPS_IN_A_ROW = 3


@dataclass(frozen=True)
class Target:
    """A file to read, and its pages."""

    path: Path
    name: str  # as shown: relative to the folder it was found in
    sha256: str
    pages: list[PagePlan]

    def to_read(self, *, force: bool) -> list[PagePlan]:
        """The pages read by OCR; with `force`, the pages with text too."""
        return list(self.pages) if force else [p for p in self.pages if p.ocr]


def find(paths: Sequence[Path]) -> list[tuple[str, Path]]:
    """The PDFs and images given, and under the folders given, each with the name to show."""
    found = []
    for path in paths:
        if path.is_dir():
            found += [
                (f.relative_to(path).as_posix(), f) for f in find_files(path, raster.PATTERNS)
            ]
        else:
            found.append((path.name, path))
    return found


def plan(
    files: Sequence[tuple[str, Path]],
    *,
    password: str | None,
    min_dpi: float,
    max_dpi: float,
) -> tuple[list[Target], dict[str, str]]:
    """The files' pages, and the files that couldn't be opened (by name, with why)."""
    targets = []
    failed = {}
    for name, path in files:
        try:
            pages = raster.plan(path, password=password, min_dpi=min_dpi, max_dpi=max_dpi)
            sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        except Exception as e:  # pdfplumber and PIL raise many kinds for a bad file
            failed[name] = f"{type(e).__name__}: {e}"
            continue
        targets.append(Target(path=path, name=name, sha256=sha256, pages=pages))
    return targets, failed


@dataclass
class Outcome:
    """What a run of one engine did."""

    read: int = 0
    failed: int = 0
    left: int = 0  # not read: the worker couldn't start, or stopped too often
    engine: EngineInfo | None = None
    error: str | None = None  # why pages were left
    log: Path | None = None
    failures: list[str] = field(default_factory=list)


def pending(
    targets: Sequence[Target], store: Store, key: RunKey, *, force: bool, reread: bool
) -> list[tuple[Target, PagePlan]]:
    """The pages still to read with `key` (all of them with `reread`)."""
    work = []
    for target in targets:
        done = set() if reread else store.done(target.sha256, key)
        work += [(target, p) for p in target.to_read(force=force) if p.page not in done]
    return work


def run(
    targets: Sequence[Target],
    engine: Engine,
    *,
    store: Store,
    workers_dir: Path,
    launcher: Launcher,
    device: str,
    min_dpi: float,
    max_dpi: float,
    password: str | None = None,
    force: bool = False,
    reread: bool = False,
    orient: Engine | None = None,
    orient_min_confidence: float = ORIENT_MIN_CONFIDENCE,
    steps: Sequence[str] = (),
    worker_pages: int = 0,
    say: Say = lambda _: None,
) -> Outcome:
    """Read the pages of `targets` not read yet with `engine`, keeping each in `store`.

    With `orient`, the pages are first turned upright as that engine tells;
    then each of the preprocessing `steps` (names, see `preprocess`) is done.
    The worker is started again after every `worker_pages` pages (0 or less: never).
    """
    chosen = preprocess.steps(steps)
    key = RunKey.of(
        engine,
        min_dpi=min_dpi,
        max_dpi=max_dpi,
        orient=orient.name if orient else None,
        orient_min_confidence=orient_min_confidence,
        preprocess=tuple(steps),
    )
    work = pending(targets, store, key, force=force, reread=reread)
    outcome = Outcome()
    # Where each file kept or to be kept is, read before or not, for `ocr remove-missing`.
    reading = {t.sha256 for t, _ in work}
    for target in targets:
        if target.sha256 in reading or store.has(target.sha256):
            store.note_source(target.sha256, target.name, target.pages, target.path)
    if not work:
        return outcome

    total = len(work)
    outcome.log = store.new_log(engine.name)
    stops_in_a_row = 0
    with (
        outcome.log.open("a", encoding="utf-8") as log,
        tempfile.TemporaryDirectory(dir=store.scratch()) as scratch,
    ):
        turns: dict[str, dict[int, Orientation]] = {}
        untold: dict[tuple[str, int], str] = {}  # why a page's turn couldn't be told
        if orient is not None:
            context = _Context(store, workers_dir, launcher, Path(scratch), password, log, say)
            outcome.error, untold = _orient(work, orient, context)
            if outcome.error is not None:
                outcome.left = len(work)
                return outcome
            turns = {t.sha256: store.orientations(t.sha256, orient.name) for t, _ in work}
        while work:
            info = None
            try:
                with Worker(launcher(engine, workers_dir), log) as worker:
                    info = outcome.engine = worker.start(device, engine.options)
                    model = info.model or "its default model"
                    say(f"{info.name} {info.version} ({model}) on {info.device or '?'}")
                    given = 0  # pages this worker has been given
                    while work:
                        if worker_pages > 0 and given >= worker_pages:
                            say(f"starting the worker again (after {given} pages)")
                            break
                        given += 1
                        target, page = work[0]
                        told = turns.get(target.sha256, {}).get(page.page)
                        clockwise = (
                            told.rotate if told and told.confidence >= orient_min_confidence else 0
                        )
                        read: StoredPage | str
                        if orient is not None and told is None:
                            # Not read the way it might be turned: told and read again next time.
                            why = untold.get((target.sha256, page.page), "not told")
                            read = f"couldn't tell how it's turned: {why}"
                        else:
                            read = _read_page(
                                worker,
                                info,
                                target,
                                page,
                                Path(scratch),
                                password,
                                clockwise=clockwise,
                                steps=chosen,
                            )
                        work.pop(0)
                        stops_in_a_row = 0
                        if isinstance(read, StoredPage):
                            store.save(target.sha256, key, read)
                        else:
                            store.save_failure(target.sha256, key, page.page, read, info)
                        say(_tally(outcome, target, page, read, f"[{total - len(work)}/{total}]"))
            except WorkerFailed as e:
                if info is None:  # it couldn't start: leave the rest
                    outcome.error = f"couldn't start: {e}"
                    break
                # It stopped on the first page left, so that page failed.
                target, page = work.pop(0)
                error = f"the worker stopped: {e}"
                store.save_failure(target.sha256, key, page.page, error, info)
                say(_tally(outcome, target, page, error, f"[{total - len(work)}/{total}]"))
                stops_in_a_row += 1
                if stops_in_a_row >= MAX_STOPS_IN_A_ROW:
                    outcome.error = f"the worker stopped {stops_in_a_row} times in a row"
                    break
                if work:
                    say("starting the worker again")
    outcome.left = len(work)
    return outcome


@dataclass(frozen=True)
class _Context:
    """What a pass over the pages needs besides the pages."""

    store: Store
    workers_dir: Path
    launcher: Launcher
    scratch: Path
    password: str | None
    log: IO[str]
    say: Say


def _orient(
    work: Sequence[tuple[Target, PagePlan]], orient: Engine, c: _Context
) -> tuple[str | None, dict[tuple[str, int], str]]:
    """Have `orient` tell how each page of `work` not told yet is turned, keeping each.

    Returns why it couldn't tell at all (it couldn't start or stopped), if so, and why it
    couldn't tell each page it couldn't (not kept, so told again next time).
    """
    told = {t.sha256: c.store.orientations(t.sha256, orient.name) for t, _ in work}
    todo = [(t, p) for t, p in work if p.page not in told[t.sha256]]
    untold: dict[tuple[str, int], str] = {}
    if not todo:
        return None, untold
    c.say(f"telling how {len(todo)} page(s) are turned, with {orient.name}")
    try:
        with Worker(c.launcher(orient, c.workers_dir), c.log) as worker:
            worker.start("auto", orient.options)
            for target, page in todo:
                where = f"  {target.name} p{page.page}"
                image_path = c.scratch / f"page-{page.page}.png"
                try:
                    image_path, _, _ = _save_image(target, page, c.scratch, c.password)
                    orientation = worker.orient(f"{target.sha256[:12]}-p{page.page}", image_path)
                except PageFailed as e:
                    untold[target.sha256, page.page] = str(e)
                    c.say(f"{where}: couldn't tell how it's turned: {e}")
                    continue
                except (OSError, ValueError) as e:  # rendering the page
                    untold[target.sha256, page.page] = f"{type(e).__name__}: {e}"
                    c.say(f"{where}: couldn't render it: {type(e).__name__}: {e}")
                    continue
                finally:
                    image_path.unlink(missing_ok=True)
                c.store.save_orientation(target.sha256, orient.name, page.page, orientation)
                if orientation.rotate:
                    c.say(
                        f"{where}: turned {orientation.rotate} degrees clockwise to be upright "
                        f"(confidence {orientation.confidence:g})"
                    )
    except WorkerFailed as e:
        return f"couldn't tell how pages are turned with {orient.name}: {e}", untold
    return None, untold


def _save_image(
    target: Target,
    page: PagePlan,
    scratch: Path,
    password: str | None,
    *,
    clockwise: int = 0,
    steps: Sequence[preprocess.Step] = (),
) -> tuple[Path, tuple[int, int], preprocess.Back]:
    """The page rendered, turned `clockwise` and through `steps`, as a PNG in `scratch`.

    Also its size as rendered, and the way back from a box on the PNG to one on the page
    as rendered.
    """
    image = raster.render(target.path, page, password=password)
    size = image.size
    upright = raster.turn(image, clockwise)
    ready, back_steps = preprocess.apply(upright, steps)
    # Named after the page, for whoever reads the log.
    path = scratch / f"page-{page.page}.png"
    # With its resolution: some engines (Tesseract) size the text by it.
    dpi = image.width / (page.width / 72)
    ready.save(path, compress_level=1, dpi=(dpi, dpi))

    def back(box: preprocess.Box) -> preprocess.Box:
        return raster.turn_back(back_steps(box), clockwise, size)

    return path, size, back


def _read_page(
    worker: Worker,
    info: EngineInfo,
    target: Target,
    page: PagePlan,
    scratch: Path,
    password: str | None,
    *,
    clockwise: int = 0,
    steps: Sequence[preprocess.Step] = (),
) -> StoredPage | str:
    """One page read with `worker` (turned `clockwise` and through `steps` first), or why
    it couldn't be.

    Raises `WorkerFailed` if the worker stops.
    """
    image_path, output = scratch / f"page-{page.page}.png", scratch / f"page-{page.page}.json"
    try:
        image_path, size, back = _save_image(
            target, page, scratch, password, clockwise=clockwise, steps=steps
        )
        seconds = worker.read(f"{target.sha256[:12]}-p{page.page}", image_path, output)
        result = _put_back(load_result(output), back)
    except PageFailed as e:
        return str(e)
    except (OSError, ValueError) as e:  # rendering, or what the worker wrote
        return f"{type(e).__name__}: {e}"
    finally:
        image_path.unlink(missing_ok=True)
        output.unlink(missing_ok=True)
    return StoredPage(
        page=to_page(page, size, result), engine=info, seconds=seconds, extra=result.extra
    )


def _tally(
    outcome: Outcome, target: Target, page: PagePlan, read: StoredPage | str, count: str
) -> str:
    """Count a page in `outcome`; the line telling how it went."""
    where = f"  {count} {target.name} p{page.page}"
    if isinstance(read, str):
        outcome.failed += 1
        outcome.failures.append(f"{target.name} p{page.page}: {read}")
        return f"{where}: FAILED: {read}"
    outcome.read += 1
    seconds = f" in {read.seconds:.1f}s" if read.seconds is not None else ""
    return f"{where}: {len(read.page.words)} piece(s) of text{seconds}"


def _put_back(result: Result, back: preprocess.Back) -> Result:
    """What was read on the image as given to the worker, on the page as rendered."""
    boxes = []
    for box in result.boxes:
        x0, y0, x1, y1 = back((box.x0, box.y0, box.x1, box.y1))
        boxes.append(replace(box, x0=x0, y0=y0, x1=x1, y1=y1))
    return Result(boxes=boxes, extra=result.extra)


def to_page(page: PagePlan, size: tuple[int, int], result: Result) -> OcrPage:
    """What a worker read from the image of `page` (`size` pixels), in points."""
    width, height = size
    x_scale, y_scale = page.width / width, page.height / height
    return OcrPage(
        page=page.page,
        width=page.width,
        height=page.height,
        dpi=round(width / (page.width / 72), 1),
        words=[
            OcrWord(
                text=box.text,
                x0=round(box.x0 * x_scale, 2),
                x1=round(box.x1 * x_scale, 2),
                top=round(box.y0 * y_scale, 2),
                bottom=round(box.y1 * y_scale, 2),
                confidence=None if box.confidence is None else round(box.confidence, 4),
            )
            for box in result.boxes
            if box.text.strip()
        ],
    )
