"""Reading files by OCR with a fake worker (tests/ocr_fake_worker.py), over the real protocol."""

import json
import sys
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
import pytest
from ocr_files import A4, a4_scan, prepare_that_ends_on, scan, write_pdf
from PIL import Image

from scriptorium import raster, runner
from scriptorium.engines import Engine
from scriptorium.models import Orientation
from scriptorium.raster import PagePlan, Reason
from scriptorium.runner import Target
from scriptorium.store import RunKey, Store

FAKE_WORKER = Path(__file__).resolve().parents[1] / "ocr_fake_worker.py"
ENGINE = Engine("fake", "fake", "a fake engine for the tests", options={"model": "small"})
# Low, so the pages are rendered quickly (the resolution is tested in test_raster).
MIN_DPI, MAX_DPI = 100, 200


def launcher(engine: Engine, workers_dir: Path) -> list[str]:
    return [sys.executable, str(FAKE_WORKER)]


def fake(monkeypatch: pytest.MonkeyPatch, **behaviour: Any) -> None:
    monkeypatch.setenv("FAKE_OCR", json.dumps(behaviour))


def targets(*paths: Path, folder: Path) -> list[Target]:
    found, failed = runner.plan(
        runner.find([folder]) if not paths else [(p.name, p) for p in paths],
        password=None,
        min_dpi=MIN_DPI,
        max_dpi=MAX_DPI,
    )
    assert failed == {}
    return found


def run(
    found: list[Target], store: Store, engine: Engine = ENGINE, **options: Any
) -> runner.Outcome:
    return runner.run(
        found,
        engine,
        store=store,
        workers_dir=Path("unused"),
        launcher=launcher,
        device=options.pop("device", "auto"),
        min_dpi=MIN_DPI,
        max_dpi=MAX_DPI,
        **options,
    )


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "ocr")


@pytest.fixture
def scans(tmp_path: Path) -> Path:
    folder = tmp_path / "scans"
    write_pdf(folder / "2030" / "bill.pdf", [a4_scan(150), None], texts=[None, "本文のページ"])
    scan((600, 300)).save(folder / "receipt.png", dpi=(300, 300))
    return folder


def key(engine: Engine = ENGINE) -> RunKey:
    return RunKey.of(engine, min_dpi=MIN_DPI, max_dpi=MAX_DPI)


def test_reads_the_scanned_pages_and_keeps_them_in_points(
    scans: Path, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake(monkeypatch, boxes=[{"text": "合計", "x0": 150, "y0": 300, "x1": 300, "y1": 375}])
    found = targets(folder=scans)

    outcome = run(found, store)

    assert [t.name for t in found] == ["2030/bill.pdf", "receipt.png"]
    assert (outcome.read, outcome.failed, outcome.left) == (2, 0, 0)
    assert outcome.engine is not None
    assert (outcome.engine.name, outcome.engine.device) == ("fake", "cpu")
    bill, receipt = found
    assert store.done(bill.sha256, key()) == {1}  # page 2 has text
    page = store.load(bill.sha256, key(), 1)
    assert page is not None
    assert page.page.dpi == pytest.approx(150, abs=0.2)
    assert (page.page.width, page.page.height) == pytest.approx(A4)
    (word,) = page.page.words
    # 150 dpi: 150 pixels are 72 points.
    assert (word.text, word.x0, word.top, word.x1, word.bottom) == pytest.approx(
        ("合計", 72, 144, 144, 180), abs=0.2
    )
    assert page.extra["options"] == {"model": "small"}
    assert store.done(receipt.sha256, key()) == {1}


def test_a_run_again_reads_only_what_s_not_read_yet(
    scans: Path, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake(monkeypatch)
    found = targets(folder=scans)
    run(found, store)

    again = run(found, store)
    forced = run(found, store, force=True)
    reread = run(found, store, reread=True)
    both = run(found, store, reread=True, force=True)

    assert (again.read, again.log) == (0, None)  # no worker started
    assert forced.read == 1  # the page with text
    assert reread.read == 2
    assert both.read == 3


def test_a_page_that_can_t_be_read_is_kept_as_failed_and_read_again_next_time(
    tmp_path: Path, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_pdf(tmp_path / "three.pdf", [a4_scan(100)] * 3)
    found = targets(path, folder=tmp_path)
    fake(monkeypatch, fail=[2])

    outcome = run(found, store)

    sha = found[0].sha256
    assert (outcome.read, outcome.failed) == (2, 1)
    assert outcome.failures == ["three.pdf p2: RuntimeError: can't read page 2"]
    assert store.done(sha, key()) == {1, 3}
    assert store.failed(sha, key()) == {2: "RuntimeError: can't read page 2"}

    fake(monkeypatch)
    again = run(found, store)

    assert (again.read, again.failed) == (1, 0)
    assert store.failed(sha, key()) == {}


def test_a_worker_that_stops_is_started_again_for_the_pages_left(
    tmp_path: Path, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_pdf(tmp_path / "three.pdf", [a4_scan(100)] * 3)
    found = targets(path, folder=tmp_path)
    fake(monkeypatch, crash=[2])

    outcome = run(found, store)

    assert (outcome.read, outcome.failed, outcome.left) == (2, 1, 0)
    assert "the worker stopped" in store.failed(found[0].sha256, key())[2]
    assert outcome.log is not None and outcome.log.exists()


def test_a_worker_that_keeps_stopping_isn_t_started_again(
    tmp_path: Path, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_pdf(tmp_path / "five.pdf", [a4_scan(100)] * 5)
    found = targets(path, folder=tmp_path)
    fake(monkeypatch, crash=[1, 2, 3])

    outcome = run(found, store)

    assert (outcome.read, outcome.failed, outcome.left) == (0, 3, 2)
    assert outcome.error == "the worker stopped 3 times in a row"


def test_a_worker_is_started_again_after_so_many_pages(
    tmp_path: Path, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_pdf(tmp_path / "five.pdf", [a4_scan(100)] * 5)
    found = targets(path, folder=tmp_path)
    fake(monkeypatch)
    said: list[str] = []

    outcome = run(found, store, worker_pages=2, say=said.append)

    assert (outcome.read, outcome.failed, outcome.left) == (5, 0, 0)
    assert store.done(found[0].sha256, key()) == {1, 2, 3, 4, 5}
    assert said.count("starting the worker again (after 2 pages)") == 2
    assert sum(line.startswith("fake 1.0 ") for line in said) == 3  # each worker, as it starts


def test_a_worker_that_can_t_start_leaves_every_page(
    scans: Path, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake(monkeypatch, devices=["cpu"])
    found = targets(folder=scans)

    outcome = run(found, store, device="cuda")

    assert (outcome.read, outcome.failed, outcome.left) == (0, 0, 2)
    assert outcome.error is not None
    assert "couldn't start: StartError: the device 'cuda' isn't available" in outcome.error
    assert all(store.done(t.sha256, key()) == set() for t in found)


def test_pages_read_on_another_device_are_kept_and_counted(
    scans: Path, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake(monkeypatch, devices=["mps", "cpu"])
    found = targets(folder=scans)
    assert run(found, store, device="auto").read == 2  # on mps

    on_cpu = run(found, store, device="cpu")

    assert (on_cpu.read, on_cpu.log, on_cpu.read_elsewhere) == (0, None, {"mps": 2})
    assert run(found, store, device="mps").read_elsewhere == {}
    # auto is the best there is, known only once the worker has started.
    assert run(found, store, device="auto").read_elsewhere == {}

    reread = run(found, store, device="cpu", reread=True)

    assert (reread.read, reread.read_elsewhere) == (2, {})
    page = store.load(found[0].sha256, key(), 1)
    assert page is not None
    assert page.engine.device == "cpu"
    assert run(found, store, device="cpu").read_elsewhere == {}


def test_what_the_engine_prints_goes_to_the_log(
    scans: Path, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake(monkeypatch, chatter="loading weights... 100%")
    found = targets(folder=scans)

    outcome = run(found, store)

    assert outcome.read == 2
    assert outcome.log is not None
    assert "loading weights... 100%" in outcome.log.read_text(encoding="utf-8")


def test_an_engine_s_options_and_revision_keep_its_pages_apart(
    scans: Path, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake(monkeypatch)
    found = targets(folder=scans)
    run(found, store)

    other = Engine("fake", "fake", "", options={"model": "large"})
    newer = Engine("fake", "fake", "", options={"model": "small"}, revision=2)

    assert run(found, store, other).read == 2
    assert run(found, store, newer).read == 2
    assert len({key().slug, key(other).slug, key(newer).slug}) == 3


def test_files_that_can_t_be_opened_are_told_apart(tmp_path: Path) -> None:
    (tmp_path / "broken.pdf").write_bytes(b"%PDF-1.4 broken")
    (tmp_path / "broken.jpg").write_bytes(b"not a jpeg")
    scan((100, 100)).save(tmp_path / "fine.png")

    found, failed = runner.plan(
        runner.find([tmp_path]), password=None, min_dpi=MIN_DPI, max_dpi=MAX_DPI
    )

    assert [t.name for t in found] == ["fine.png"]
    assert sorted(failed) == ["broken.jpg", "broken.pdf"]
    assert found[0].pages[0].reason is Reason.IMAGE


def oriented_key(engine: Engine = ENGINE) -> RunKey:
    return RunKey.of(
        engine, min_dpi=MIN_DPI, max_dpi=MAX_DPI, orient="fake", orient_min_confidence=2.0
    )


def run_upright(found: list[Target], store: Store, engine: Engine = ENGINE) -> runner.Outcome:
    return run(found, store, engine, orient=ENGINE, orient_min_confidence=2.0)


def test_pages_are_turned_upright_as_told_and_what_s_read_is_put_back_on_the_page(
    tmp_path: Path, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_pdf(tmp_path / "turned.pdf", [a4_scan(100)])
    found = targets(path, folder=tmp_path)
    fake(monkeypatch, turns={"1": [90, 10.0]})

    outcome = run_upright(found, store)

    sha = found[0].sha256
    assert (outcome.read, outcome.error) == (1, None)
    assert store.orientations(sha, "fake") == {1: Orientation(90, 10.0)}
    page = store.load(sha, oriented_key(), 1)
    assert page is not None
    turned_width, turned_height = page.extra["size"]
    assert turned_width > turned_height  # the engine read the page turned a quarter
    # The box near the top left of the turned page is near the bottom left of the page
    # (whose height in pixels is the turned page's width).
    (word,) = page.page.words
    x_scale, y_scale = page.page.width / turned_height, page.page.height / turned_width
    assert (word.x0, word.x1) == pytest.approx((20 * x_scale, 40 * x_scale), abs=0.01)
    assert (word.top, word.bottom) == pytest.approx(
        ((turned_width - 110) * y_scale, (turned_width - 10) * y_scale), abs=0.01
    )
    # Kept apart from pages read as they are.
    assert store.done(sha, key()) == set()


def test_a_turn_told_unsure_is_left_alone(
    tmp_path: Path, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_pdf(tmp_path / "unsure.pdf", [a4_scan(100)])
    found = targets(path, folder=tmp_path)
    fake(monkeypatch, turns={"1": [90, 1.0]})

    run_upright(found, store)

    page = store.load(found[0].sha256, oriented_key(), 1)
    assert page is not None
    width, height = page.extra["size"]
    assert width < height


def test_how_pages_are_turned_is_told_once(
    scans: Path, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake(monkeypatch)
    found = targets(folder=scans)
    run_upright(found, store)
    fake(monkeypatch, crash_orient=True)  # it would stop if asked again

    outcome = run_upright(found, store, Engine("fake", "fake", "", options={"model": "large"}))

    assert (outcome.read, outcome.error) == (2, None)


def test_a_page_whose_turn_can_t_be_told_fails_and_is_told_again_next_time(
    scans: Path, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake(monkeypatch, no_orient=True)
    found = targets(folder=scans)

    outcome = run_upright(found, store)

    assert (outcome.read, outcome.failed) == (0, 2)
    assert outcome.failures[0].endswith(
        "couldn't tell how it's turned: fake doesn't tell how pages are turned"
    )
    assert store.orientations(found[0].sha256, "fake") == {}

    fake(monkeypatch)
    again = run_upright(found, store)

    assert (again.read, again.failed) == (2, 0)
    assert store.orientations(found[0].sha256, "fake") == {1: Orientation(0, 9.0)}


def test_turns_kept_unreadable_are_told_again(
    scans: Path, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake(monkeypatch)
    found = targets(folder=scans)
    broken = store.root / found[0].sha256 / "orient-fake.json"
    broken.parent.mkdir(parents=True)
    broken.write_text('{"1": {"rot', encoding="utf-8")
    said: list[str] = []

    outcome = run(found, store, orient=ENGINE, orient_min_confidence=2.0, say=said.append)

    assert (outcome.read, outcome.error) == (2, None)
    assert store.orientations(found[0].sha256, "fake") == {1: Orientation(0, 9.0)}
    assert any("orient-fake.json can't be read" in line for line in said)


def test_a_page_that_can_t_be_rendered_fails_alone(
    scans: Path, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    scan((300, 600)).save(scans / "broken.png", dpi=(300, 300))
    fake(monkeypatch)
    found = targets(folder=scans)
    render = raster.render

    def failing(path: Path, page: PagePlan, *, password: str | None = None) -> Image.Image:
        if path.name == "broken.png":
            raise OSError("image file is truncated")
        return render(path, page, password=password)

    monkeypatch.setattr(raster, "render", failing)

    upright = run_upright(found, store)
    as_it_is = run(found, store)

    assert (upright.read, upright.failed, upright.error) == (2, 1, None)
    assert "broken.png p1: couldn't tell how it's turned" in upright.failures[0]
    assert (as_it_is.read, as_it_is.failed) == (2, 1)


def test_a_page_too_large_to_render_fails_alone(
    scans: Path, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake(monkeypatch)
    found = targets(folder=scans)
    # Only the PDF's scanned page (A4 at 150 dpi, 1.9 million pixels) is over it.
    monkeypatch.setattr(raster, "MAX_PIXELS", 1_000_000)

    # One worker: the pages are rendered in this process, where the patch is.
    outcome = run(found, store, jobs=1)

    assert (outcome.read, outcome.failed, outcome.error) == (1, 1, None)
    assert "bill.pdf p1: " in outcome.failures[0]
    assert "pixels" in outcome.failures[0]


def test_a_page_pdfium_can_t_render_fails_alone(
    scans: Path, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake(monkeypatch)
    found = targets(folder=scans)
    pdfium_document = pdfium.PdfDocument

    def failing(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path.suffix == ".pdf":
            raise pdfium.PdfiumError("Failed to load document")
        return pdfium_document(path, *args, **kwargs)

    monkeypatch.setattr(pdfium, "PdfDocument", failing)

    outcome = run(found, store, jobs=1)

    assert (outcome.read, outcome.failed, outcome.error) == (1, 1, None)
    assert "bill.pdf p1: " in outcome.failures[0]
    assert "pdfium can't open it" in outcome.failures[0]


def test_an_engine_telling_turns_that_can_t_start_leaves_every_page(
    scans: Path, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake(monkeypatch, fatal="no osd data")
    found = targets(folder=scans)

    outcome = run_upright(found, store)

    assert (outcome.read, outcome.left) == (0, 2)
    assert outcome.error is not None
    assert outcome.error.startswith("couldn't tell how pages are turned with fake")


def test_a_key_without_turning_is_as_it_was() -> None:
    plain = RunKey("fake").as_dict()

    assert "orient" not in plain
    assert RunKey("fake").slug != RunKey("fake", orient="tesseract").slug


def test_pages_go_through_the_preprocessing_steps_and_are_kept_apart(
    tmp_path: Path, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    slanted = a4_scan(150, [(200, 300, 999, 339), (200, 500, 999, 539), (200, 700, 999, 739)])
    path = write_pdf(tmp_path / "slanted.pdf", [slanted.rotate(-3, fillcolor="white")])
    found = targets(path, folder=tmp_path)
    fake(monkeypatch)

    outcome = run(found, store, steps=["flatten", "deskew"])

    sha = found[0].sha256
    steps_key = RunKey.of(
        ENGINE, min_dpi=MIN_DPI, max_dpi=MAX_DPI, preprocess=("flatten", "deskew")
    )
    page = store.load(sha, steps_key, 1)
    assert (outcome.read, page is not None) == (1, True)
    assert page is not None
    # The engine read the page straightened, so a bit larger than the page.
    width, _ = page.extra["size"]
    assert width > page.page.width * page.page.dpi / 72 + 50
    assert store.done(sha, key()) == set()
    assert steps_key.as_dict()["preprocess"] == ["flatten", "deskew"]
    assert "preprocess" not in key().as_dict()


@pytest.mark.parametrize("jobs", [2, 3])
def test_several_workers_read_as_one_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, jobs: int
) -> None:
    # Two files whose pages have the same numbers, rendered at once (in processes), at
    # different sizes; each page turned, a turn told on some, and preprocessed.
    first = write_pdf(tmp_path / "first.pdf", [a4_scan(100, [(50, 60, 300, 120)])] * 3)
    second = write_pdf(tmp_path / "second.pdf", [a4_scan(150)] * 3)
    found = targets(first, second, folder=tmp_path)
    fake(monkeypatch, turns={"2": [90, 10.0]})
    alone, together = Store(tmp_path / "alone"), Store(tmp_path / "together")
    options: dict[str, Any] = {
        "orient": ENGINE,
        "orient_min_confidence": 2.0,
        "steps": ("deskew",),
    }

    one = run(found, alone, **options)
    several = run(found, together, jobs=jobs, orient_jobs=jobs, **options)

    assert (one.read, several.read, several.failed, several.left) == (6, 6, 0, 0)
    kept = RunKey.of(
        ENGINE,
        min_dpi=MIN_DPI,
        max_dpi=MAX_DPI,
        orient="fake",
        orient_min_confidence=2.0,
        preprocess=("deskew",),
    )
    sizes = set()
    for target in found:
        assert together.orientations(target.sha256, "fake") == alone.orientations(
            target.sha256, "fake"
        )
        for page in (1, 2, 3):
            expected, read = (
                alone.load(target.sha256, kept, page),
                together.load(target.sha256, kept, page),
            )
            assert expected is not None and read is not None
            assert (read.page, read.extra) == (expected.page, expected.extra)
            sizes.add(tuple(read.extra["size"]))
    assert len(sizes) == 4  # each file's pages, upright and turned a quarter


def test_a_page_one_of_the_workers_stops_on_fails_and_the_others_are_read(
    tmp_path: Path, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_pdf(tmp_path / "six.pdf", [a4_scan(100)] * 6)
    found = targets(path, folder=tmp_path)
    fake(monkeypatch, crash=[3])

    outcome = run(found, store, jobs=2)

    assert (outcome.read, outcome.failed, outcome.left) == (5, 1, 0)
    assert "the worker stopped" in store.failed(found[0].sha256, key())[3]


def test_workers_that_can_t_start_leave_every_page(
    scans: Path, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake(monkeypatch, devices=["cpu"])

    outcome = run(targets(folder=scans), store, device="cuda", jobs=2)

    assert (outcome.read, outcome.failed, outcome.left) == (0, 0, 2)
    assert outcome.error is not None and outcome.error.startswith("couldn't start: ")


def test_no_more_workers_start_than_there_are_pages(
    scans: Path, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake(monkeypatch)
    said: list[str] = []

    outcome = run(targets(folder=scans), store, jobs=8, say=said.append)

    assert (outcome.read, outcome.failed) == (2, 0)
    assert sum(line.startswith("fake 1.0 ") for line in said) == 2
    assert [line.split("]")[0] for line in said if line.startswith("  [")] == [
        "  [1/2",
        "  [2/2",
    ]


@pytest.mark.parametrize("jobs", [1, 2])
def test_a_run_stopped_midway_stops_its_workers_and_keeps_what_was_read(
    tmp_path: Path, store: Store, monkeypatch: pytest.MonkeyPatch, jobs: int
) -> None:
    path = write_pdf(tmp_path / "six.pdf", [a4_scan(100)] * 6)
    found = targets(path, folder=tmp_path)
    fake(monkeypatch)

    def stop(line: str) -> None:
        if line.startswith("  [1/"):
            raise KeyboardInterrupt  # as Ctrl+C would, once a page is kept

    with pytest.raises(KeyboardInterrupt):
        run(found, store, jobs=jobs, say=stop)

    assert 1 <= len(store.done(found[0].sha256, key())) < 6
    assert list((store.root / "tmp").iterdir()) == []  # the pages rendered ahead are gone


def test_a_page_lost_with_the_process_rendering_it_is_rendered_again(
    tmp_path: Path, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_pdf(tmp_path / "six.pdf", [a4_scan(100)] * 6)
    found = targets(path, folder=tmp_path)
    fake(monkeypatch)
    monkeypatch.setattr(runner, "_prepare", prepare_that_ends_on(3, once=True))

    outcome = run(found, store, jobs=2)

    assert (outcome.read, outcome.failed, outcome.left) == (6, 0, 0)


def test_only_the_page_that_ends_the_process_rendering_it_fails(
    tmp_path: Path, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_pdf(tmp_path / "six.pdf", [a4_scan(100)] * 6)
    found = targets(path, folder=tmp_path)
    fake(monkeypatch)
    monkeypatch.setattr(runner, "_prepare", prepare_that_ends_on(3, once=False))

    outcome = run(found, store, jobs=2)

    assert (outcome.read, outcome.failed, outcome.left) == (5, 1, 0)
    assert store.failed(found[0].sha256, key()) == {3: "the process rendering the page stopped"}
