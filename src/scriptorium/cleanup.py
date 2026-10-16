"""Finding what's kept of OCR readings and no longer needed, and removing it.

What OCR read holds the documents' text, so what's kept of a file that's
gone should go too. Each file kept knows the absolute paths it was read at
(`Store.locations`): it's gone when the folder of each of them is there but
none holds the file (the same contents) any more. A folder that isn't there
at all (a drive not connected) says nothing, so what was read there is kept
unless told otherwise; and so is what was read before the paths were kept,
unless it's looked for in folders given.
"""

import shutil
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from scriptorium.files import absolute_path, sha256_of
from scriptorium.store import Store

# Scratch files untouched this long are left of a run stopped.
SCRATCH_KEPT_SECONDS = 24 * 60 * 60


@dataclass
class Item:
    """Something to remove: what to call it, and its files and folders."""

    label: str
    paths: list[Path]

    @property
    def size(self) -> int:
        return sum(_size(p) for p in self.paths)


@dataclass
class Missing:
    """What's kept of files gone, and of files that can't be told to be."""

    going: list[Item] = field(default_factory=list)
    # Kept: read in folders that aren't there (each folder, with the files' names).
    folder_missing: dict[Path, list[str]] = field(default_factory=dict)
    # Kept: read before where they were read was kept, and not looked for in folders given.
    unrecorded: list[str] = field(default_factory=list)


def find_missing(
    store: Store, folders: Sequence[Path] = (), *, folder_missing_too: bool = False
) -> Missing:
    """What's kept of files gone (see the module's docstring).

    A file under `folders` isn't gone wherever it was read. One read before
    the paths were kept is gone when it's under none of `folders`; without
    `folders`, it's kept. With `folder_missing_too`, a file whose folders
    aren't there is taken to be gone too.
    """
    # Every file, not only those `ocr run` finds in a folder: it reads any file given.
    present = {sha256_of(f) for f in every_file(folders)} if folders else set[str]()
    found = Missing()
    for sha256 in store.files():
        shown = store.name_of(sha256) or sha256[:12]
        if sha256 in present:
            continue
        paths = store.locations(sha256)
        if not paths:
            if folders:
                found.going.append(
                    Item(f"{shown} (not under the folders given)", [store.root / sha256])
                )
            else:
                found.unrecorded.append(shown)
            continue
        if any(p.is_file() and sha256_of(p) == sha256 for p in paths):
            continue
        absent = sorted({p.parent for p in paths if not p.parent.is_dir()})
        if absent and not folder_missing_too:
            for folder in absent:
                found.folder_missing.setdefault(folder, []).append(shown)
            continue
        found.going.append(Item(f"{shown} (gone from {paths[-1]})", [store.root / sha256]))
    return found


def find_engine_runs(store: Store, engines: set[str]) -> list[Item]:
    """The readings of `engines` (with every setting) of each file."""
    going = []
    for sha256 in store.files():
        runs = [folder for folder, engine in store.runs(sha256) if engine in engines]
        if runs:
            shown = store.name_of(sha256) or sha256[:12]
            going.append(Item(f"{shown}: {', '.join(f.name for f in runs)}", runs))
    return going


def engines_kept(store: Store) -> set[str]:
    """The engines something is kept of."""
    return {engine for sha256 in store.files() for _, engine in store.runs(sha256)}


def find_logs(store: Store) -> list[Item]:
    """The workers' logs, and the scratch files of runs stopped (not of a run going on)."""
    # A run going on keeps using its folder of scratch files, so it's touched within the day.
    scratch = store.root / "tmp"
    stale = time.time() - SCRATCH_KEPT_SECONDS
    left = [f for f in scratch.iterdir() if f.stat().st_mtime < stale] if scratch.is_dir() else []
    kept = [f for f in [store.root / "logs", *left] if f.exists()]
    return [Item("logs and scratch files", kept)] if kept else []


def every_file(folders: Sequence[Path]) -> Iterator[Path]:
    """Every file under `folders`, and under the links to folders in them (each folder once)."""
    seen = set()
    for folder in folders:
        for root, dirs, files in absolute_path(folder).walk(follow_symlinks=True):
            stat = root.stat()
            if (stat.st_dev, stat.st_ino) in seen:
                dirs.clear()
                continue
            seen.add((stat.st_dev, stat.st_ino))
            yield from (root / name for name in files if (root / name).is_file())


def remove(path: Path) -> list[str]:
    """Remove the file or folder `path`; what couldn't be removed (e.g. a file in use), and why."""
    failures = []

    def failed(_: object, name: str, error: BaseException) -> None:
        failures.append(f"{name}: {error}")

    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, onexc=failed)
    else:
        try:
            path.unlink(missing_ok=True)
        except OSError as e:
            failures.append(f"{path}: {e}")
    return failures


def _size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
