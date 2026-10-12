from pathlib import Path

import pytest

from scriptorium import engines, worker
from scriptorium.engines import Engine
from scriptorium.models import ORIENT_MIN_CONFIDENCE as OCR_ORIENT_MIN_CONFIDENCE
from scriptorium.models import EngineInfo, OcrPage, OcrWord, StoredPage
from scriptorium.store import RunKey, Store

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


def test_load_result_rejects_what_isn_t_a_result(tmp_path: Path) -> None:
    path = tmp_path / "result.json"
    path.write_text('{"protocol": 1, "boxes": [{"text": "a", "x0": 1}]}')

    with pytest.raises(ValueError, match="not a box"):
        worker.load_result(path)
    with pytest.raises(ValueError, match="wrote no result"):
        worker.load_result(tmp_path / "missing.json")
