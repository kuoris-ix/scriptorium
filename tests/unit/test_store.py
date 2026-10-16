import os
import shutil
import stat
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from scriptorium import engines, store, worker
from scriptorium.engines import Engine
from scriptorium.models import ORIENT_MIN_CONFIDENCE as OCR_ORIENT_MIN_CONFIDENCE
from scriptorium.models import EngineInfo, OcrPage, OcrWord, Orientation, StoredPage
from scriptorium.store import RunKey, Store, UnreadableFile

ENGINE = EngineInfo(name="fake", version="1.0", model="m", device="cpu")


def stored(page: int, text: str = "合計") -> StoredPage:
    return StoredPage(
        page=OcrPage(
            page=page,
            width=595.28,
            height=841.89,
            dpi=300,
            words=[OcrWord(text, 72, 144, 144, 180, 0.9), OcrWord("¥1,234", 150, 200, 144, 180)],
        ),
        engine=ENGINE,
        seconds=1.5,
        extra={"raw": [1, 2]},
    )


def test_a_page_kept_is_loaded_as_it_was(tmp_path: Path) -> None:
    store, key = Store(tmp_path), RunKey("fake")

    store.save("abc", key, stored(2))

    assert store.load("abc", key, 2) == stored(2)
    assert store.load("abc", key, 1) is None
    assert store.done("abc", key) == {2}
    assert not list(tmp_path.rglob("*.part"))
    assert (store.folder("abc", key) / "run.json").exists()


def test_a_failure_is_kept_until_the_page_is_read(tmp_path: Path) -> None:
    store, key = Store(tmp_path), RunKey("fake")

    store.save_failure("abc", key, 3, "RuntimeError: no", ENGINE)

    assert store.failed("abc", key) == {3: "RuntimeError: no"}
    assert store.done("abc", key) == set()

    store.save("abc", key, stored(3))

    assert store.failed("abc", key) == {}
    assert store.done("abc", key) == {3}


def test_longest_path_is_that_of_a_failure_being_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, key = Store(tmp_path), RunKey.of(engines.find("yomitoku-lite"), min_dpi=1, max_dpi=2)
    written = []
    monkeypatch.setattr(Path, "replace", lambda part, path: written.append(part))

    store.save_failure("0" * 64, key, 1, "RuntimeError: no", ENGINE)

    assert [len(str(part)) for part in written][-1] == store.longest_path(key)


def test_each_write_goes_through_a_temporary_file_of_its_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kept, written = Store(tmp_path), []
    monkeypatch.setattr(Path, "replace", lambda part, path: written.append(part))

    kept.note_source("abc", "a.pdf", [])
    kept.note_source("abc", "a.pdf", [])

    first, second = written
    assert first != second
    assert first.name.startswith("source.json.") and first.name.endswith(".part")


def busy(monkeypatch: pytest.MonkeyPatch, times: int) -> list[Path]:
    """Have Path.replace refused `times` times, as while another process has the file; the
    files it was tried with, each time."""
    tries: list[Path] = []
    replace = Path.replace

    def refused(part: Path, path: Path) -> Path:
        tries.append(path)
        if len(tries) <= times:
            raise PermissionError(13, "in use", str(path))
        return replace(part, path)

    monkeypatch.setattr(Path, "replace", refused)
    return tries


def test_a_file_another_process_has_is_written_once_it_s_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store, "_BUSY_CAN_PASS", True)  # as on Windows
    tries = busy(monkeypatch, times=3)

    Store(tmp_path).note_source("abc", "a.pdf", [])

    assert len(tries) == 4
    assert Store(tmp_path).name_of("abc") == "a.pdf"


def test_a_file_held_too_long_isn_t_written_and_leaves_nothing_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store, "_BUSY_CAN_PASS", True)
    monkeypatch.setattr(store, "_BUSY_SECONDS", 0.05)
    busy(monkeypatch, times=1_000)

    with pytest.raises(PermissionError):
        Store(tmp_path).note_source("abc", "a.pdf", [])

    assert list(tmp_path.rglob("*.part")) == []


def test_a_permission_refused_is_tried_once_where_files_in_use_can_be_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store, "_BUSY_CAN_PASS", False)  # as on Linux and macOS
    tries = busy(monkeypatch, times=1)

    with pytest.raises(PermissionError):
        Store(tmp_path).note_source("abc", "a.pdf", [])

    assert len(tries) == 1


def test_an_unreadable_orientations_file_is_told_and_written_anew(tmp_path: Path) -> None:
    kept = Store(tmp_path)
    kept.save_orientation("abc", "fake", 1, Orientation(90, 5.0))
    path = tmp_path / "abc" / "orient-fake.json"
    path.write_text('{"1": {"rotate": 90, "confidence": 5.0}, "2": {"rot', encoding="utf-8")

    with pytest.raises(UnreadableFile, match=r"orient-fake\.json can't be read"):
        kept.orientations("abc", "fake")

    kept.save_orientation("abc", "fake", 2, Orientation(0, 7.0))

    assert kept.orientations("abc", "fake") == {2: Orientation(0, 7.0)}


# Each of two processes keeps how every other page is turned, at the same time.
TELLING = """
import sys
from pathlib import Path
from scriptorium.models import Orientation
from scriptorium.store import Store

kept, first = Store(Path(sys.argv[1])), int(sys.argv[2])
for page in range(first, 61, 2):
    kept.save_orientation("abc", "fake", page, Orientation(0, 1.0))
"""


def test_two_runs_telling_turns_at_once_keep_every_page(tmp_path: Path) -> None:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONPATH"] = os.pathsep.join(sys.path)
    runs = [
        subprocess.Popen([sys.executable, "-c", TELLING, str(tmp_path), str(first)], env=env)
        for first in (1, 2)
    ]

    assert [r.wait(timeout=120) for r in runs] == [0, 0]
    assert sorted(Store(tmp_path).orientations("abc", "fake")) == list(range(1, 61))
    assert list(tmp_path.rglob("*.part")) == []


def test_a_log_s_name_tells_runs_started_in_the_same_second_apart(tmp_path: Path) -> None:
    log = Store(tmp_path).new_log("fake")

    assert log.name.endswith(f"-{os.getpid()}-fake.log")


_NOT_ON_WINDOWS = pytest.mark.skipif(sys.platform == "win32", reason="Windows has no modes")


@_NOT_ON_WINDOWS
@pytest.mark.parametrize(
    "write",
    [
        lambda s: s.save("abc", RunKey("fake"), stored(1)),
        lambda s: s.save_failure("abc", RunKey("fake"), 1, "RuntimeError: no", ENGINE),
        lambda s: s.note_source("abc", "sample.pdf", []),
        lambda s: s.save_orientation("abc", "fake", 1, Orientation(0, 0.9)),
        lambda s: s.scratch(),
        lambda s: s.new_log("fake"),
    ],
    ids=["save", "save_failure", "note_source", "save_orientation", "scratch", "new_log"],
)
def test_a_store_s_folder_made_by_any_write_is_its_user_s_only(
    tmp_path: Path, write: Callable[[Store], object]
) -> None:
    root = tmp_path / "ocr"

    write(Store(root))

    assert stat.S_IMODE(root.stat().st_mode) == 0o700


@_NOT_ON_WINDOWS
def test_a_store_s_folder_there_already_is_left_as_it_is(tmp_path: Path) -> None:
    root = tmp_path / "ocr"
    root.mkdir()
    root.chmod(0o755)

    Store(root).save("abc", RunKey("fake"), stored(1))

    assert stat.S_IMODE(root.stat().st_mode) == 0o755


@pytest.mark.parametrize(
    "other",
    [
        RunKey("other"),
        RunKey("fake", options={"lite": True}),
        RunKey("fake", revision=2),
        RunKey("fake", min_dpi=200),
        RunKey("fake", max_dpi=400),
    ],
)
def test_every_part_of_a_run_key_tells_runs_apart(other: RunKey) -> None:
    assert RunKey("fake").slug != other.slug
    assert RunKey("fake").slug == RunKey("fake").slug
    assert RunKey("fake").slug.startswith("fake-")


# The folders the engines' pages are kept in, with the default resolutions: as read, turned
# upright first (the recommended setting), and turned and preprocessed. If one changes, every
# page read so is read again (and what was read before is no longer found): change an engine's
# options or revision, PROTOCOL, PREPROCESS_REVISION or how a key is written only on purpose,
# and then write the new folders here.
KEPT_IN = [
    ("yomitoku", "yomitoku-422577fa6990", "yomitoku-64e74d1d1232", "yomitoku-8556ddf411b4"),
    (
        "yomitoku-lite",
        "yomitoku-lite-c087468b0d92",
        "yomitoku-lite-8567bc9cdbf3",
        "yomitoku-lite-1b566d3bf6f5",
    ),
    ("paddleocr", "paddleocr-81f3ba172c12", "paddleocr-f3751fbf275c", "paddleocr-3a81b769cf9c"),
    (
        "ndlocr-lite",
        "ndlocr-lite-39a415486add",
        "ndlocr-lite-2dfe33c836d2",
        "ndlocr-lite-222998dfebcb",
    ),
    (
        "apple-vision",
        "apple-vision-cbd6a75f7cf7",
        "apple-vision-eedb212eb64a",
        "apple-vision-48f6a0051eac",
    ),
    (
        "windows-ocr",
        "windows-ocr-6326d9e8f71f",
        "windows-ocr-94ac48174d7f",
        "windows-ocr-edcb403b85ad",
    ),
    ("tesseract", "tesseract-beeaaa9ada3d", "tesseract-cd22baea564e", "tesseract-d67873e4563e"),
]


@pytest.mark.parametrize(("name", "as_read", "turned", "preprocessed"), KEPT_IN)
def test_the_folders_pages_are_kept_in_don_t_change(
    name: str, as_read: str, turned: str, preprocessed: str
) -> None:
    def key(orient: str | None = None, preprocess: tuple[str, ...] = ()) -> str:
        return RunKey.of(
            engines.find(name),
            min_dpi=300,
            max_dpi=600,
            orient=orient,
            orient_min_confidence=OCR_ORIENT_MIN_CONFIDENCE,
            preprocess=preprocess,
        ).slug

    assert key() == as_read
    assert key("tesseract") == turned
    assert key("tesseract", ("flatten", "deskew")) == preprocessed


def test_every_engine_s_folders_are_written_down() -> None:
    assert [name for name, *_ in KEPT_IN] == [e.name for e in engines.ENGINES]


def test_a_key_is_written_as_before_pages_could_be_turned_or_preprocessed() -> None:
    assert RunKey("fake").as_dict() == {
        "engine": "fake",
        "options": {},
        "revision": 1,
        "min_dpi": 300,
        "max_dpi": 600,
        "protocol": 1,
    }
    assert RunKey("fake").slug == "fake-965bfff1ede7"


def test_engines_have_unique_names_and_existing_workers() -> None:
    names = [e.name for e in engines.ENGINES]
    workers_dir = Path(__file__).resolve().parents[2] / "workers"

    assert len(names) == len(set(names))
    for engine in engines.ENGINES:
        assert (engine.folder(workers_dir) / "worker.py").is_file(), engine.name
        assert (engine.folder(workers_dir) / "pyproject.toml").is_file(), engine.name


def test_an_engine_tells_why_it_can_t_run(tmp_path: Path) -> None:
    mac_only = Engine("mac", "mac", "", platforms=("darwin",))
    anywhere = Engine("any", "any", "")
    (tmp_path / "mac").mkdir()
    (tmp_path / "mac" / "worker.py").write_text("")

    assert mac_only.unavailable(tmp_path, platform="win32") == "runs on macOS only"
    assert mac_only.unavailable(tmp_path, platform="darwin") is None
    assert anywhere.unavailable(tmp_path) == f"no worker at {tmp_path / 'any'}"
    assert not mac_only.installed(tmp_path)


def test_find_names_the_engines_there_are() -> None:
    assert engines.find("tesseract").worker == "tesseract"
    with pytest.raises(KeyError, match="use one of: yomitoku, yomitoku-lite"):
        engines.find("nope")


def test_uv_command_runs_the_worker_in_its_own_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UV", "/bin/uv")

    command = worker.uv_command(Engine("t", "tess", ""), tmp_path)

    folder = (tmp_path / "tess").resolve()
    assert command == [
        "/bin/uv",
        "run",
        "--project",
        str(folder),
        "python",
        str(folder / "worker.py"),
    ]


def test_a_worker_isn_t_given_the_variables_not_passed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FICTIONAL_PDF_PASSWORD", "not-a-real-one")
    monkeypatch.setenv("FICTIONAL_OTHER", "kept")
    script = (
        "import os, sys\n"
        "print([os.environ.get(n) for n in ('FICTIONAL_PDF_PASSWORD', 'FICTIONAL_OTHER')],"
        " file=sys.stderr)\n"
    )
    log_path = tmp_path / "worker.log"

    with log_path.open("w", encoding="utf-8") as log:
        started = worker.Worker(
            [sys.executable, "-c", script], log, not_passed=("FICTIONAL_PDF_PASSWORD",)
        )
        started.stop()

    assert log_path.read_text(encoding="utf-8").strip() == "[None, 'kept']"


def test_find_command_doesn_t_look_in_the_working_folder_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    here, on_path = tmp_path / "here", tmp_path / "on_path"
    here.mkdir()
    on_path.mkdir()
    (here / "uv.EXE").write_text("")
    monkeypatch.chdir(here)
    monkeypatch.setenv("PATHEXT", ".EXE")
    monkeypatch.setenv("PATH", os.pathsep.join([".", "", str(on_path)]))

    assert worker.find_command("uv", platform="win32") is None

    (on_path / "uv.EXE").write_text("")

    assert worker.find_command("uv", platform="win32") == str(on_path / "uv.EXE")


def test_find_command_is_which_where_it_looks_in_the_path_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    program = tmp_path / ("zzprogram.exe" if sys.platform == "win32" else "zzprogram")
    program.write_text("")
    program.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("PATHEXT", ".EXE")

    found = worker.find_command("zzprogram", platform="linux")

    assert found is not None
    assert found == shutil.which("zzprogram")
    assert worker.find_command("zznothing", platform="linux") is None


def test_load_result_rejects_what_isn_t_a_result(tmp_path: Path) -> None:
    path = tmp_path / "result.json"
    path.write_text('{"protocol": 1, "boxes": [{"text": "a", "x0": 1}]}')

    with pytest.raises(ValueError, match="not a box"):
        worker.load_result(path)
    with pytest.raises(ValueError, match="wrote no result"):
        worker.load_result(tmp_path / "missing.json")
