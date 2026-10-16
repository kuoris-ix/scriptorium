"""Finding the files to read in a folder, and writing their paths to keep."""

import hashlib
import os
import sys
from collections.abc import Sequence
from pathlib import Path

# The longest path Windows takes unless long paths are enabled: MAX_PATH (260), less the
# character ending it.
WINDOWS_MAX_PATH = 259


def absolute_path(path: Path) -> Path:
    """`path` as an absolute path, to keep or show: wherever the program runs from next, it's found.

    Without "..", but with links and mapped drives as they are: `Path.absolute()` leaves
    "..", and `Path.resolve()` puts links' targets (and drives' network paths) in, which
    differ from the paths given.
    """
    return Path(os.path.abspath(path))  # noqa: PTH100 resolve() follows links


def find_files(directory: Path, patterns: Sequence[str]) -> list[Path]:
    """The files in `directory` matching any of `patterns`, sorted by relative path.

    Patterns ignore case on every system ("*.pdf" finds "A.PDF" too).
    """
    found = {
        p
        for pattern in patterns
        for p in directory.glob(pattern, case_sensitive=False)
        if p.is_file()
    }
    # By the plain path: Windows paths would sort ignoring case, others not.
    return sorted(found, key=lambda p: p.relative_to(directory).parts)


def longest_path_allowed() -> int | None:
    """The longest path a file can be written at here; None if there's no limit to mind (not
    Windows, or Windows with long paths enabled)."""
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem"
        ) as key:
            enabled = winreg.QueryValueEx(key, "LongPathsEnabled")[0]
    except OSError:
        enabled = 0
    return None if enabled == 1 else WINDOWS_MAX_PATH


def sha256_of(path: Path) -> str:
    """The SHA-256 of the file's contents, read a piece at a time (a file may be large)."""
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()
