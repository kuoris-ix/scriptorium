"""Keep every test in a folder of its own, with no setting of the test program given.

The test program (`ocr_app`) reads `TEST_OCR_*` variables, and its paths are
relative to the working directory: each test runs in an empty folder, so what
it doesn't point elsewhere itself lands there.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from ocr_app import PREFIX


@pytest.fixture(autouse=True)
def isolated_settings(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    for name in [n for n in os.environ if n.startswith(PREFIX)]:
        monkeypatch.delenv(name)
    cwd = tmp_path_factory.mktemp("cwd")
    monkeypatch.chdir(cwd)
    yield cwd
