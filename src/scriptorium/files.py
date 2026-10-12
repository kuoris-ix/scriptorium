"""Finding the files to read in a folder, and writing their paths to keep."""

import os
from collections.abc import Sequence
from pathlib import Path


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
