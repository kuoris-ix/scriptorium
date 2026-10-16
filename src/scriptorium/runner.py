"""Reading files by OCR: which pages, with which engine, kept in the store.

`ocr run` is the only thing that runs the engines; everything
else reads what's kept (see `store`). A run may take hours and be stopped
at any point: each page is kept as soon as it's read, and a run again
reads only the pages not read yet.

With an engine to tell how pages are turned (`orient`), each page is first
told (and that's kept too), then turned upright before it's read; then come
the preprocessing steps asked for (`preprocess`). What's read is put back
through them all onto the page as it is.

The pages are rendered ahead of the workers, in the background: in a
thread with one worker, in as many processes as workers with several
(`jobs`, see the module `jobs`), so that a worker is never left waiting for
its next page. The workers share the pages, each taking the next; what each
reads is kept as soon as it's read, in the thread that runs them.
"""

import functools
import hashlib
import queue
import signal
import tempfile
import threading
from collections import deque
from collections.abc import Callable, Sequence
from concurrent.futures import (
    BrokenExecutor,
    Executor,
    Future,
    ProcessPoolExecutor,
    ThreadPoolExecutor,
)
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import IO

from scriptorium import preprocess, raster
from scriptorium.engines import Engine
from scriptorium.files import find_files
from scriptorium.models import (
    DEVICES,
    ORIENT_MIN_CONFIDENCE,
    EngineInfo,
    OcrPage,
    OcrWord,
    Orientation,
    StoredPage,
)
from scriptorium.raster import PagePlan
from scriptorium.store import RunKey, Store, UnreadableFile
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
    # Pages not read again, as read before on another device than the one asked for, by device.
    read_elsewhere: dict[str, int] = field(default_factory=dict)


def pending(
    targets: Sequence[Target], store: Store, key: RunKey, *, force: bool, reread: bool
) -> list[tuple[Target, PagePlan]]:
    """The pages still to read with `key` (all of them with `reread`)."""
    work = []
    for target in targets:
        done = set() if reread else store.done(target.sha256, key)
        work += [(target, p) for p in target.to_read(force=force) if p.page not in done]
    return work


def read_elsewhere(
    targets: Sequence[Target], store: Store, key: RunKey, device: str, *, force: bool
) -> dict[str, int]:
    """The pages kept with `key` that were read on another device than `device`, by device.

    The device isn't in the RunKey, so these aren't read again on `device` without `reread`.
    Only for a device asked for: `auto` is the best there is, known once the worker has
    started. Apple Vision picks its own and keeps `apple`, which isn't one to ask for.
    """
    if device == "auto":
        return {}
    found: dict[str, int] = {}
    for target in targets:
        done = store.done(target.sha256, key)
        for page in target.to_read(force=force):
            if page.page not in done:
                continue
            stored = store.load(target.sha256, key, page.page)
            used = stored.engine.device if stored is not None else None
            if used is not None and used in DEVICES and used != device:
                found[used] = found.get(used, 0) + 1
    return found


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
    not_passed: Sequence[str] = (),
    force: bool = False,
    reread: bool = False,
    orient: Engine | None = None,
    orient_min_confidence: float = ORIENT_MIN_CONFIDENCE,
    steps: Sequence[str] = (),
    worker_pages: int = 0,
    jobs: int = 1,
    orient_jobs: int = 1,
    say: Say = lambda _: None,
) -> Outcome:
    """Read the pages of `targets` not read yet with `engine`, keeping each in `store`.

    With `orient`, the pages are first turned upright as that engine tells (with
    `orient_jobs` workers); then each of the preprocessing `steps` (names, see `preprocess`)
    is done. `jobs` workers read at once. Each is started again after every `worker_pages`
    pages it reads (0 or less: never). The workers aren't given the environment variables
    named in `not_passed`.
    """
    preprocess.steps(steps)  # raises KeyError for a step there isn't, before anything's read
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
    if not reread:
        outcome.read_elsewhere = read_elsewhere(targets, store, key, device, force=force)
    # Where each file kept or to be kept is, read before or not, for `ocr remove-missing`.
    reading = {t.sha256 for t, _ in work}
    for target in targets:
        if target.sha256 in reading or store.has(target.sha256):
            store.note_source(target.sha256, target.name, target.pages, target.path)
    if not work:
        return outcome

    total, handled = len(work), 0
    outcome.log = store.new_log(engine.name)
    with (
        outcome.log.open("a", encoding="utf-8") as log,
        tempfile.TemporaryDirectory(dir=store.scratch()) as scratch,
    ):
        context = _Context(
            store, workers_dir, launcher, Path(scratch), password, tuple(not_passed), log, say
        )
        turns: dict[str, dict[int, Orientation]] = {}
        untold: dict[tuple[str, int], str] = {}  # why a page's turn couldn't be told
        if orient is not None:
            outcome.error, untold = _orient(work, orient, context, jobs=orient_jobs)
            if outcome.error is not None:
                outcome.left = len(work)
                return outcome
            turns = {t.sha256: _orientations(store, t.sha256, orient.name, say) for t, _ in work}

        def done(
            target: Target, page: PagePlan, read: StoredPage | _Failure, info: EngineInfo | None
        ) -> None:
            nonlocal handled
            handled += 1
            if isinstance(read, _Failure):
                store.save_failure(target.sha256, key, page.page, read.why, info)
            else:
                store.save(target.sha256, key, read)
            shown = read.why if isinstance(read, _Failure) else read
            say(_tally(outcome, target, page, shown, f"[{handled}/{total}]"))

        to_read = []
        for target, page in work:
            told = turns.get(target.sha256, {}).get(page.page)
            if orient is not None and told is None:
                # Not read the way it might be turned: told and read again next time.
                why = untold.get((target.sha256, page.page), "not told")
                done(target, page, _Failure(f"couldn't tell how it's turned: {why}"), None)
                continue
            clockwise = told.rotate if told and told.confidence >= orient_min_confidence else 0
            to_read.append(_Page(target, page, clockwise))
        if to_read:
            outcome.engine, outcome.error, outcome.left = _work_through(
                to_read,
                engine,
                context,
                jobs=jobs,
                device=device,
                steps=tuple(steps),
                worker_pages=worker_pages,
                ask=_read,
                done=done,
            )
    return outcome


@dataclass(frozen=True)
class _Context:
    """What a pass over the pages needs besides the pages."""

    store: Store
    workers_dir: Path
    launcher: Launcher
    scratch: Path
    password: str | None
    not_passed: tuple[str, ...]
    log: IO[str]
    say: Say


def _orient(
    work: Sequence[tuple[Target, PagePlan]], orient: Engine, c: _Context, *, jobs: int
) -> tuple[str | None, dict[tuple[str, int], str]]:
    """Have `orient` tell how each page of `work` not told yet is turned, keeping each.

    Returns why it couldn't tell at all (it couldn't start or stopped), if so, and why it
    couldn't tell each page it couldn't (not kept, so told again next time).
    """
    told = {t.sha256: _orientations(c.store, t.sha256, orient.name, c.say) for t, _ in work}
    todo = [_Page(t, p, 0) for t, p in work if p.page not in told[t.sha256]]
    untold: dict[tuple[str, int], str] = {}
    if not todo:
        return None, untold
    c.say(f"telling how {len(todo)} page(s) are turned, with {orient.name}")

    def done(
        target: Target, page: PagePlan, turn: Orientation | _Failure, _: EngineInfo | None
    ) -> None:
        where = f"  {target.name} p{page.page}"
        if isinstance(turn, _Failure):
            untold[target.sha256, page.page] = turn.why
            what = "couldn't render it" if turn.rendering else "couldn't tell how it's turned"
            c.say(f"{where}: {what}: {turn.why}")
            return
        c.store.save_orientation(target.sha256, orient.name, page.page, turn)
        if turn.rotate:
            c.say(
                f"{where}: turned {turn.rotate} degrees clockwise to be upright "
                f"(confidence {turn.confidence:g})"
            )

    _, error, _ = _work_through(
        todo, orient, c, jobs=jobs, device="auto", steps=(), worker_pages=0, ask=_tell, done=done
    )
    if error is not None:
        return f"couldn't tell how pages are turned with {orient.name}: {error}", untold
    return None, untold


def _orientations(store: Store, sha256: str, engine: str, say: Say) -> dict[int, Orientation]:
    """How each page of the file is turned, as kept; none if that can't be read (told again)."""
    try:
        return store.orientations(sha256, engine)
    except UnreadableFile as e:
        say(f"{e}: how its pages are turned is told again")
        return {}


@dataclass(frozen=True)
class _Page:
    """A page to have a worker read (or tell how it's turned), turned `clockwise` first."""

    target: Target
    page: PagePlan
    clockwise: int


@dataclass(frozen=True)
class _Ready:
    """A page rendered, turned and preprocessed, as a PNG for a worker."""

    image: Path
    size: tuple[int, int]  # as rendered
    back: preprocess.Back  # from a box on the PNG to one on the page as rendered


@dataclass(frozen=True)
class _Failure:
    """Why a page couldn't be read (or told); `rendering` if it couldn't even be rendered."""

    why: str
    rendering: bool = False


@dataclass(frozen=True)
class _Over:
    """A worker's thread has ended; why it gave up the pages left, if it did."""

    error: str | None


def _read(worker: Worker, info: EngineInfo, page: _Page, ready: _Ready) -> StoredPage:
    """What the worker reads on the page. Raises `PageFailed` if it couldn't, `WorkerFailed` if
    it stopped, ValueError if what it wrote isn't a result."""
    output = ready.image.with_suffix(".json")
    try:
        seconds = worker.read(_page_id(page), ready.image, output)
        result = _put_back(load_result(output), ready.back)
    finally:
        output.unlink(missing_ok=True)
    return StoredPage(
        page=to_page(page.page, ready.size, result),
        engine=info,
        seconds=seconds,
        extra=result.extra,
    )


def _tell(worker: Worker, info: EngineInfo, page: _Page, ready: _Ready) -> Orientation:
    """How the worker's engine tells the page is turned (raises as `_read`)."""
    return worker.orient(_page_id(page), ready.image)


def _page_id(page: _Page) -> str:
    return f"{page.target.sha256[:12]}-p{page.page.page}"


def _work_through[T](
    pages: Sequence[_Page],
    engine: Engine,
    c: _Context,
    *,
    jobs: int,
    device: str,
    steps: tuple[str, ...],
    worker_pages: int,
    ask: Callable[[Worker, EngineInfo, _Page, _Ready], T],
    done: Callable[[Target, PagePlan, T | _Failure, EngineInfo | None], None],
) -> tuple[EngineInfo | None, str | None, int]:
    """Have `jobs` workers of `engine` take `pages` in turn, each rendered ahead (see the
    module's description), and `ask` a worker about each; `done` is told how each went, here,
    in this thread.

    Returns what a worker told of its engine (if one started), why pages were left (none of
    the workers could start, or each stopped too often in a row), and how many.
    """
    jobs = max(1, min(jobs, len(pages)))
    supply = _Supply(pages, c, steps, jobs)
    messages: queue.Queue[Callable[[], None] | _Over] = queue.Queue()
    stopping = threading.Event()
    running: set[Worker] = set()
    lock = threading.Lock()
    started: list[EngineInfo] = []

    def tell(text: str) -> None:
        messages.put(functools.partial(c.say, text))

    def serve() -> str | None:
        """Read with one worker while there are pages; why it gave up, if it did."""
        stops = 0
        while not stopping.is_set():
            info, taken = None, None
            try:
                with Worker(
                    c.launcher(engine, c.workers_dir), c.log, not_passed=c.not_passed
                ) as worker:
                    with lock:
                        running.add(worker)
                    try:
                        info = worker.start(device, engine.options)
                        messages.put(functools.partial(_started, started, info, c.say))
                        given = 0  # pages this worker has been given
                        while not stopping.is_set():
                            if worker_pages > 0 and given >= worker_pages:
                                tell(f"starting the worker again (after {given} pages)")
                                break
                            taken = supply.next()
                            if taken is None:
                                return None
                            given += 1
                            page, ready = taken
                            answer = _ask(
                                worker,
                                info,
                                page,
                                functools.partial(supply.result, page, ready),
                                ask,
                            )
                            taken, stops = None, 0
                            messages.put(
                                functools.partial(done, page.target, page.page, answer, info)
                            )
                    finally:
                        with lock:
                            running.discard(worker)
            except WorkerFailed as e:
                if stopping.is_set():
                    return None
                if info is None:  # it couldn't start: leave its pages to the others
                    return f"couldn't start: {e}"
                if taken is not None:  # it stopped on that page, so that page failed
                    page, _ = taken
                    failure = _Failure(f"the worker stopped: {e}")
                    messages.put(functools.partial(done, page.target, page.page, failure, info))
                stops += 1
                if stops >= MAX_STOPS_IN_A_ROW:
                    return f"the worker stopped {stops} times in a row"
                tell("starting the worker again")
        return None

    def thread() -> None:
        error = None
        try:
            error = serve()
        except BaseException as e:  # told in the main thread, which stops
            messages.put(functools.partial(_raise, e))
        finally:
            messages.put(_Over(error))

    threads = [threading.Thread(target=thread, daemon=True) for _ in range(jobs)]
    error = None
    try:
        for t in threads:
            t.start()
        active = len(threads)
        while active:
            try:
                # Not waiting long at once: on Windows, Ctrl+C is had only between waits.
                message = messages.get(timeout=0.2)
            except queue.Empty:
                continue
            if isinstance(message, _Over):
                active -= 1
                error = message.error or error
            else:
                message()
    finally:
        # Stopped (Ctrl+C, an error): the workers are stopped too, their pages left.
        stopping.set()
        with lock:
            for worker in running:
                worker.process.kill()
        for t in threads:
            t.join()
        left = supply.close()
    return (started[-1] if started else None), (error if left else None), left


def _started(started: list[EngineInfo], info: EngineInfo, say: Say) -> None:
    started.append(info)
    say(f"{info.name} {info.version} ({info.model or 'its default model'}) on {info.device or '?'}")


def _raise(error: BaseException) -> None:
    raise error


def _ask[T](
    worker: Worker,
    info: EngineInfo,
    page: _Page,
    ready: Callable[[], _Ready],
    ask: Callable[[Worker, EngineInfo, _Page, _Ready], T],
) -> T | _Failure:
    """`ask`'s answer about `page`, once it's ready, or why there's none.

    Raises `WorkerFailed` if the worker stops.
    """
    prepared = None
    try:
        try:
            prepared = ready()
        except (OSError, ValueError) as e:  # pdfplumber and PIL, as `plan` found the pages
            return _Failure(f"{type(e).__name__}: {e}", rendering=True)
        except BrokenExecutor:  # the process rendering it ended (e.g. out of memory)
            return _Failure("the process rendering the page stopped", rendering=True)
        return ask(worker, info, page, prepared)
    except PageFailed as e:
        return _Failure(str(e))
    except (OSError, ValueError) as e:  # what the worker wrote
        return _Failure(f"{type(e).__name__}: {e}")
    finally:
        if prepared is not None:
            prepared.image.unlink(missing_ok=True)


class _Supply:
    """The pages, handed out to the workers in turn, each rendered ahead: a few more than
    there are workers, in a thread with one worker, in as many processes as workers with
    more (pdfium renders one page at a time in a process)."""

    def __init__(
        self, pages: Sequence[_Page], c: _Context, steps: tuple[str, ...], jobs: int
    ) -> None:
        self.waiting = deque(pages)
        self.ahead: deque[tuple[_Page, Future[_Ready]]] = deque()
        self.lock = threading.Lock()
        self.depth = jobs + 1
        self.c, self.steps, self.jobs = c, steps, jobs
        self.renderer: Executor = self._new_renderer()

    def _new_renderer(self) -> Executor:
        if self.jobs == 1:
            return ThreadPoolExecutor(1)
        return ProcessPoolExecutor(self.jobs, initializer=_rendering_only)

    def _submit(self, page: _Page) -> Future[_Ready]:
        try:
            return self.renderer.submit(_prepare, page, self.c.scratch, self.c.password, self.steps)
        except BrokenExecutor:  # a process ended: the pages in it fail, new ones go to new ones
            self.renderer = self._new_renderer()
            return self.renderer.submit(_prepare, page, self.c.scratch, self.c.password, self.steps)

    def result(self, page: _Page, ready: Future[_Ready]) -> _Ready:
        """The page rendered. If the process rendering it ended (it, or another page in it,
        used up the memory, say), it's rendered once more in a process of its own, so that
        only a page that ends it fails."""
        try:
            return ready.result()
        except BrokenExecutor:
            pass
        with ProcessPoolExecutor(1, initializer=_rendering_only) as alone:
            return alone.submit(
                _prepare, page, self.c.scratch, self.c.password, self.steps
            ).result()

    def next(self) -> tuple[_Page, Future[_Ready]] | None:
        """The next page, being rendered or ready; None when there are no more."""
        with self.lock:
            self._fill()
            if not self.ahead:
                return None
            taken = self.ahead.popleft()
            self._fill()
            return taken

    def _fill(self) -> None:
        while len(self.ahead) < self.depth and self.waiting:
            page = self.waiting.popleft()
            self.ahead.append((page, self._submit(page)))

    def close(self) -> int:
        """Stop rendering; how many pages weren't handed out."""
        with self.lock:
            left = len(self.waiting) + len(self.ahead)
            self.waiting.clear()
        self.renderer.shutdown(wait=True, cancel_futures=True)
        return left


def _rendering_only() -> None:
    """In a process rendering pages: Ctrl+C is for the run, which stops this one itself."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)


def _prepare(page: _Page, scratch: Path, password: str | None, steps: tuple[str, ...]) -> _Ready:
    """The page rendered, turned and through `steps` (by name), as a PNG in `scratch`."""
    target, plan = page.target, page.page
    image = raster.render(target.path, plan, password=password)
    size = image.size
    upright = raster.turn(image, page.clockwise)
    ready, back_steps = preprocess.apply(upright, preprocess.steps(steps))
    # Named after the page and its file (pages of several files are ready at once), for
    # whoever reads the log.
    path = scratch / f"page-{plan.page}-{target.sha256[:12]}.png"
    # With its resolution: some engines (Tesseract) size the text by it.
    dpi = image.width / (plan.width / 72)
    ready.save(path, compress_level=1, dpi=(dpi, dpi))
    return _Ready(path, size, _PageBack(back_steps, page.clockwise, size))


@dataclass(frozen=True)
class _PageBack:
    """The way back from a box on the image given to the worker to one on the page as
    rendered (`size`): through the preprocessing steps, then turned back."""

    steps: preprocess.Back
    clockwise: int
    size: tuple[int, int]

    def __call__(self, box: preprocess.Box) -> preprocess.Box:
        return raster.turn_back(self.steps(box), self.clockwise, self.size)


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
