"""Where what the OCR engines read is kept, page by page.

    <ocr_dir>/<SHA-256 of the file>/
        source.json                  the file's name when last read, its pages, and the
                                     absolute paths it was read at (for `ocr remove-missing`)
        <engine>-<hash of RunKey>/
            run.json                 the RunKey
            page-0001.json           a page read (see `StoredPage`)
            page-0002.failed.json    a page that couldn't be read, and why
        orient-<engine>.json         how each page is turned, as that engine told (`--orient`)

A file is known by its contents, not its name, so renaming or moving it
doesn't have it read again. A page is read again only when its RunKey
changes; the engine's version isn't in it (it's only known once the worker
has started), but is kept with each page. Every file is written whole or
not at all, so a run stopped at any point is taken up where it stopped.
The pages hold the documents' text: keep `ocr_dir` out of version control,
as the scans.
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from scriptorium.engines import Engine
from scriptorium.files import absolute_path
from scriptorium.models import EngineInfo, OcrPage, OcrWord, Orientation, StoredPage
from scriptorium.preprocess import REVISION as PREPROCESS_REVISION
from scriptorium.raster import PagePlan
from scriptorium.worker import PROTOCOL


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
        return len(str(path)) + len(".part")

    def note_source(
        self, sha256: str, name: str, pages: Sequence[PagePlan], path: Path | None = None
    ) -> None:
        """Keep the file's name and pages, for whoever looks into the folder, and where it is.

        `path` is added to the paths the file was read at before (as an absolute path), so
        that `ocr remove-missing` can tell the file is gone without being told where it was.
        """
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
        return [Path(p) for p in json.loads(path.read_text(encoding="utf-8")).get("paths", [])]

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
            failures[page] = str(json.loads(path.read_text(encoding="utf-8")).get("error"))
        return failures

    def load(self, sha256: str, key: RunKey, page: int) -> StoredPage | None:
        path = self._page_path(sha256, key, page)
        if not path.exists():
            return None
        return _stored_page(json.loads(path.read_text(encoding="utf-8")))

    def readings(self, sha256: str) -> dict[int, list[tuple[str, StoredPage]]]:
        """Every page of the file read so far, by page: with which engine, and what it read.

        With any engine and settings (a page may have been read several ways).
        """
        found: dict[int, list[tuple[str, StoredPage]]] = {}
        for path in sorted((self.root / sha256).glob("*/page-[0-9][0-9][0-9][0-9].json")):
            run = path.parent / "run.json"
            engine = path.parent.name
            if run.exists():
                engine = str(json.loads(run.read_text(encoding="utf-8")).get("engine", engine))
            stored = _stored_page(json.loads(path.read_text(encoding="utf-8")))
            found.setdefault(stored.page.page, []).append((engine, stored))
        return found

    def save(self, sha256: str, key: RunKey, stored: StoredPage) -> None:
        folder = self.folder(sha256, key)
        if not (folder / "run.json").exists():
            _write_json(folder / "run.json", key.as_dict())
        _write_json(self._page_path(sha256, key, stored.page.page), _page_data(stored))
        self._failure_path(sha256, key, stored.page.page).unlink(missing_ok=True)

    def save_failure(
        self, sha256: str, key: RunKey, page: int, error: str, engine: EngineInfo | None
    ) -> None:
        folder = self.folder(sha256, key)
        if not (folder / "run.json").exists():
            _write_json(folder / "run.json", key.as_dict())
        _write_json(
            self._failure_path(sha256, key, page),
            {"page": page, "error": error, "engine": asdict(engine) if engine else None},
        )

    def orientations(self, sha256: str, engine: str) -> dict[int, Orientation]:
        """How each page of the file is turned, as `engine` told."""
        path = self.root / sha256 / f"orient-{engine}.json"
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return {
            int(page): Orientation(int(value["rotate"]), float(value["confidence"]))
            for page, value in data.items()
        }

    def save_orientation(
        self, sha256: str, engine: str, page: int, orientation: Orientation
    ) -> None:
        found = self.orientations(sha256, engine)
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
        return str(json.loads(path.read_text(encoding="utf-8")).get("name"))

    def runs(self, sha256: str) -> list[tuple[Path, str]]:
        """The folders of the file's pages read with each RunKey, and the engine of each."""
        found = []
        for folder in sorted((self.root / sha256).iterdir()):
            run = folder / "run.json"
            if folder.is_dir() and run.exists():
                engine = json.loads(run.read_text(encoding="utf-8")).get("engine")
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
                found.append(json.loads((folder / "run.json").read_text(encoding="utf-8")))
        return found

    def scratch(self) -> Path:
        """A folder for the images handed to workers (of real documents, so not a system one)."""
        folder = self.root / "tmp"
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def new_log(self, engine: str) -> Path:
        """A log file for one run of `engine`'s worker."""
        folder = self.root / "logs"
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"{datetime.now():%Y%m%d-%H%M%S}-{engine}.log"


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


def _write_json(path: Path, data: Any) -> None:
    """Write `data` to `path` whole or not at all."""
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    part.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    part.replace(path)


def _is_sha256(name: str) -> bool:
    return len(name) == 64 and all(c in "0123456789abcdef" for c in name)
