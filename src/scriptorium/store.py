"""Where what the OCR engines read is kept, page by page.

    <ocr_dir>/<SHA-256 of the file>/
        source.json                  the file's name when last read, its pages, and the
                                     absolute paths it was read at (for `ocr remove-missing`)
        <engine>-<hash of RunKey>/
            run.json                 the RunKey
            page-0001.json           a page read (see `StoredPage`)
            page-0002.failed.json    a page that couldn't be read, and why
        orient-<engine>.json         how each page is turned, as that engine told (`--orient`)
        orient-<engine>.lock         held while that file is rewritten

A file is known by its contents, not its name, so renaming or moving it
doesn't have it read again. A page is read again only when its RunKey
changes; the engine's version isn't in it (it's only known once the worker
has started), but is kept with each page. Every file is written whole or
not at all, so a run stopped at any point is taken up where it stopped.
Several runs may keep pages in the same store at once (two programs, or the
same one run twice): each file is written through a temporary file of its
own, and what's rewritten from what was there (`orient-<engine>.json`) is
under a lock.
The pages hold the documents' text: keep `ocr_dir` out of version control,
as the scans.
"""

import hashlib
import json
import os
import secrets
import sys
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

from scriptorium.engines import Engine
from scriptorium.files import absolute_path
from scriptorium.models import EngineInfo, OcrPage, OcrWord, Orientation, StoredPage
from scriptorium.preprocess import REVISION as PREPROCESS_REVISION
from scriptorium.raster import PagePlan
from scriptorium.worker import PROTOCOL

# Windows doesn't let a file be replaced while another process has it open, nor opened while
# it's being replaced: with two runs on the same store, either is tried again for this long.
_BUSY_SECONDS = 10.0
_BUSY_CAN_PASS = sys.platform == "win32"


class UnreadableFile(ValueError):
    """A file of the store that's there but can't be read (e.g. left broken by two runs that
    wrote it at once, before each wrote through a temporary file of its own)."""


@dataclass(frozen=True)
class RunKey:
    """What a page read depends on; a change has it read again."""

    engine: str
    options: Mapping[str, Any] = field(default_factory=dict)
    revision: int = 1
    min_dpi: float = 300
    max_dpi: float = 600
    # The engine telling how pages are turned, which have them turned upright first, if any;
    # and how sure it has to be.
    orient: str | None = None
    orient_min_confidence: float = 0
    # The preprocessing steps done (in order) before an engine reads a page; see `preprocess`.
    preprocess: tuple[str, ...] = ()

    @classmethod
    def of(
        cls,
        engine: Engine,
        *,
        min_dpi: float,
        max_dpi: float,
        orient: str | None = None,
        orient_min_confidence: float = 0,
        preprocess: tuple[str, ...] = (),
    ) -> RunKey:
        return cls(
            engine.name,
            dict(engine.options),
            engine.revision,
            min_dpi,
            max_dpi,
            orient,
            orient_min_confidence if orient else 0,
            preprocess,
        )

    def as_dict(self) -> dict[str, Any]:
        data = {**asdict(self), "options": dict(self.options), "protocol": PROTOCOL}
        # As keys were before pages could be turned or preprocessed.
        if self.orient is None:
            del data["orient"], data["orient_min_confidence"]
        if self.preprocess:
            data["preprocess"] = list(self.preprocess)
            data["preprocess_revision"] = PREPROCESS_REVISION
        else:
            del data["preprocess"]
        return data

    @property
    def slug(self) -> str:
        """The folder name: the engine's, and a hash of the rest."""
        text = json.dumps(self.as_dict(), sort_keys=True, ensure_ascii=True)
        return f"{self.engine}-{hashlib.sha256(text.encode()).hexdigest()[:12]}"


class Store:
    def __init__(self, root: Path) -> None:
        self.root = root

    def folder(self, sha256: str, key: RunKey) -> Path:
        return self.root / sha256 / key.slug

    def _page_path(self, sha256: str, key: RunKey, page: int) -> Path:
        return self.folder(sha256, key) / f"page-{page:04d}.json"

    def _failure_path(self, sha256: str, key: RunKey, page: int) -> Path:
        return self.folder(sha256, key) / f"page-{page:04d}.failed.json"

    def longest_path(self, key: RunKey) -> int:
        """How long the longest path written for a page read with `key` is, as an absolute
        path: a failure's, while it's written (of pages up to 9999)."""
        path = absolute_path(self._failure_path("0" * 64, key, 1))
        return len(str(_part_of(path)))

    def _make_root(self) -> None:
        """Make the store's folder if it isn't there, for its user only (Linux, macOS; the
        mode is left out on Windows): what's kept holds the documents' text. One there
        already is left as it is."""
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)

    def note_source(
        self, sha256: str, name: str, pages: Sequence[PagePlan], path: Path | None = None
    ) -> None:
        """Keep the file's name and pages, for whoever looks into the folder, and where it is.

        `path` is added to the paths the file was read at before (as an absolute path), so
        that `ocr remove-missing` can tell the file is gone without being told where it was.
        """
        self._make_root()
        paths = self.locations(sha256)
        if path is not None and (where := absolute_path(path)) not in paths:
            paths.append(where)
        data: dict[str, Any] = {
            "name": name,
            "pages": [{**asdict(p), "reason": str(p.reason)} for p in pages],
        }
        if paths:
            data["paths"] = [str(p) for p in paths]
        _write_json(self.root / sha256 / "source.json", data)

    def has(self, sha256: str) -> bool:
        """Whether anything is kept of the file."""
        return (self.root / sha256).is_dir()

    def locations(self, sha256: str) -> list[Path]:
        """The absolute paths the file was read at (none if read before they were kept)."""
        path = self.root / sha256 / "source.json"
        if not path.exists():
            return []
        return [Path(p) for p in _read_json(path).get("paths", [])]

    def done(self, sha256: str, key: RunKey) -> set[int]:
        """The pages of the file read with `key`."""
        return {
            int(path.name.removeprefix("page-").removesuffix(".json"))
            for path in self.folder(sha256, key).glob("page-[0-9][0-9][0-9][0-9].json")
        }

    def failed(self, sha256: str, key: RunKey) -> dict[int, str]:
        """The pages of the file that couldn't be read with `key` (and not read since), and why."""
        failures = {}
        for path in self.folder(sha256, key).glob("page-*.failed.json"):
            page = int(path.name.removeprefix("page-").removesuffix(".failed.json"))
            failures[page] = str(_read_json(path).get("error"))
        return failures

    def load(self, sha256: str, key: RunKey, page: int) -> StoredPage | None:
        path = self._page_path(sha256, key, page)
        if not path.exists():
            return None
        return _stored_page(_read_json(path))

    def readings(self, sha256: str) -> dict[int, list[tuple[str, StoredPage]]]:
        """Every page of the file read so far, by page: with which engine, and what it read.

        With any engine and settings (a page may have been read several ways).
        """
        found: dict[int, list[tuple[str, StoredPage]]] = {}
        for path in sorted((self.root / sha256).glob("*/page-[0-9][0-9][0-9][0-9].json")):
            run = path.parent / "run.json"
            engine = path.parent.name
            if run.exists():
                engine = str(_read_json(run).get("engine", engine))
            stored = _stored_page(_read_json(path))
            found.setdefault(stored.page.page, []).append((engine, stored))
        return found

    def save(self, sha256: str, key: RunKey, stored: StoredPage) -> None:
        self._make_root()
        folder = self.folder(sha256, key)
        if not (folder / "run.json").exists():
            _write_json(folder / "run.json", key.as_dict())
        _write_json(self._page_path(sha256, key, stored.page.page), _page_data(stored))
        failure = self._failure_path(sha256, key, stored.page.page)
        _when_free(lambda: failure.unlink(missing_ok=True))

    def save_failure(
        self, sha256: str, key: RunKey, page: int, error: str, engine: EngineInfo | None
    ) -> None:
        self._make_root()
        folder = self.folder(sha256, key)
        if not (folder / "run.json").exists():
            _write_json(folder / "run.json", key.as_dict())
        _write_json(
            self._failure_path(sha256, key, page),
            {"page": page, "error": error, "engine": asdict(engine) if engine else None},
        )

    def orientations(self, sha256: str, engine: str) -> dict[int, Orientation]:
        """How each page of the file is turned, as `engine` told.

        Raises `UnreadableFile` if what's kept can't be read; it's rewritten as pages are told
        again (`save_orientation`).
        """
        path = self.root / sha256 / f"orient-{engine}.json"
        if not path.exists():
            return {}
        try:
            return {
                int(page): Orientation(int(value["rotate"]), float(value["confidence"]))
                for page, value in _read_json(path).items()
            }
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            raise UnreadableFile(f"{path} can't be read ({type(e).__name__}: {e})") from e

    def save_orientation(
        self, sha256: str, engine: str, page: int, orientation: Orientation
    ) -> None:
        self._make_root()
        # Under the lock, so that what another run keeps meanwhile isn't written over.
        with _locked(self.root / sha256 / f"orient-{engine}.lock"):
            try:
                found = self.orientations(sha256, engine)
            except UnreadableFile:
                found = {}  # the other pages are told again
            found[page] = orientation
            _write_json(
                self.root / sha256 / f"orient-{engine}.json",
                {str(p): asdict(o) for p, o in sorted(found.items())},
            )

    def files(self) -> list[str]:
        """The SHA-256 of every file something is kept of."""
        if not self.root.is_dir():
            return []
        return sorted(p.name for p in self.root.iterdir() if p.is_dir() and _is_sha256(p.name))

    def name_of(self, sha256: str) -> str | None:
        """The file's name when it was last read, if kept."""
        path = self.root / sha256 / "source.json"
        if not path.exists():
            return None
        return str(_read_json(path).get("name"))

    def runs(self, sha256: str) -> list[tuple[Path, str]]:
        """The folders of the file's pages read with each RunKey, and the engine of each."""
        found = []
        for folder in sorted((self.root / sha256).iterdir()):
            run = folder / "run.json"
            if folder.is_dir() and run.exists():
                engine = _read_json(run).get("engine")
                found.append((folder, str(engine)))
        return found

    def other_runs(self, sha256: str, key: RunKey) -> list[dict[str, Any]]:
        """The RunKeys (as kept) the file has pages read with by `key`'s engine, but `key`."""
        found: list[dict[str, Any]] = []
        if not self.has(sha256):
            return found
        for folder, engine in self.runs(sha256):
            if engine != key.engine or folder.name == key.slug:
                continue
            if any(folder.glob("page-[0-9][0-9][0-9][0-9].json")):
                found.append(_read_json(folder / "run.json"))
        return found

    def scratch(self) -> Path:
        """A folder for the images handed to workers (of real documents, so not a system one)."""
        self._make_root()
        folder = self.root / "tmp"
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def new_log(self, engine: str) -> Path:
        """A log file for one run of `engine`'s worker (with the process ID, as two runs may
        start in the same second)."""
        self._make_root()
        folder = self.root / "logs"
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"{datetime.now():%Y%m%d-%H%M%S}-{os.getpid()}-{engine}.log"


def _page_data(stored: StoredPage) -> dict[str, Any]:
    return {**asdict(stored.page), **{k: v for k, v in asdict(stored).items() if k != "page"}}


def _stored_page(data: dict[str, Any]) -> StoredPage:
    return StoredPage(
        page=OcrPage(
            page=int(data["page"]),
            width=float(data["width"]),
            height=float(data["height"]),
            dpi=float(data["dpi"]),
            words=[OcrWord(**w) for w in data["words"]],
        ),
        engine=EngineInfo(**data["engine"]),
        seconds=data.get("seconds"),
        extra=data.get("extra") or {},
    )


def _read_json(path: Path) -> Any:
    return json.loads(_when_free(lambda: path.read_text(encoding="utf-8")))


def _write_json(path: Path, data: Any) -> None:
    """Write `data` to `path` whole or not at all."""
    path.parent.mkdir(parents=True, exist_ok=True)
    part = _part_of(path)
    try:
        part.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        _when_free(lambda: part.replace(path))
    except BaseException:
        part.unlink(missing_ok=True)
        raise


def _part_of(path: Path) -> Path:
    """The temporary file `path` is written to first: one of its own for each time, so that
    two runs writing the same file at once don't write into the same one."""
    return path.with_name(f"{path.name}.{secrets.token_hex(4)}.part")


def _when_free[T](action: Callable[[], T]) -> T:
    """`action`, tried again for a while if another process has the file (see `_BUSY_SECONDS`)."""
    waited, pause = 0.0, 0.01
    while True:
        try:
            return action()
        except PermissionError:
            if not _BUSY_CAN_PASS or waited >= _BUSY_SECONDS:
                raise
        time.sleep(pause)
        waited += pause
        pause = min(pause * 2, 0.2)


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    """Hold the lock file `path` while in the block, against other processes too. The system
    lets it go if the process ends, so a run stopped doesn't leave it held."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as file:
        if sys.platform == "win32":
            while True:
                file.seek(0)
                try:
                    msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:  # held by another process
                    time.sleep(0.01)
            try:
                yield
            finally:
                file.seek(0)
                msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(file, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(file, fcntl.LOCK_UN)


def _is_sha256(name: str) -> bool:
    return len(name) == 64 and all(c in "0123456789abcdef" for c in name)
