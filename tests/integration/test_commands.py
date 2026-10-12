"""The `ocr` commands, as a program adds them (tests/ocr_app.py), with a fake engine
(tests/ocr_fake_worker.py)."""

import csv
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest
import sample_documents
import typer
from ocr_app import app
from ocr_files import a4_scan, flatbed, form, scan, write_pdf
from PIL import Image, ImageFont
from typer.testing import CliRunner

from scriptorium import engines, samples, worker
from scriptorium.engines import Engine
from scriptorium.models import EngineInfo, OcrPage, OcrWord, StoredPage
from scriptorium.store import RunKey, Store

FAKE_WORKER = Path(__file__).resolve().parents[1] / "ocr_fake_worker.py"
FAKE = Engine("fake", "fake", "a fake engine for the tests")


@pytest.fixture(autouse=True)
def fake_engine(isolated_settings: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The fake engine in the list, its worker in the workers' folder, run with this Python."""
    (isolated_settings / "workers" / "fake").mkdir(parents=True)
    (isolated_settings / "workers" / "fake" / "worker.py").write_text("")
    monkeypatch.setattr(engines, "ENGINES", (*engines.ENGINES, FAKE))
    monkeypatch.setattr(
        worker, "uv_command", lambda engine, workers_dir: [sys.executable, str(FAKE_WORKER)]
    )
    monkeypatch.setenv("FAKE_OCR", json.dumps({}))


@pytest.fixture
def scans(isolated_settings: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # Read at a low resolution, so the pages are rendered quickly.
    monkeypatch.setenv("TEST_OCR_MIN_DPI", "100")
    monkeypatch.setenv("TEST_OCR_MAX_DPI", "200")
    folder = isolated_settings / "data" / "scans"
    write_pdf(folder / "bill.pdf", [a4_scan(100), None], texts=[None, "本文"])
    scan((600, 300)).save(folder / "receipt.png", dpi=(300, 300))
    return folder


def invoke(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(app, ["ocr", *args])
    # Without the colours Typer gives errors where it takes it to be a terminal (as on CI).
    return result.exit_code, re.sub(r"\x1b\[[0-9;]*m", "", result.output)


def test_run_reads_the_scanned_pages_and_keeps_them(isolated_settings: Path, scans: Path) -> None:
    code, output = invoke("run", str(scans), "-e", "fake")

    assert code == 0, output
    assert "2 file(s), 2 page(s) to read by OCR" in output
    assert "fake 1.0 (fake-model) on cpu" in output
    assert "2 page(s) read, 0 failed" in output
    store = Store(isolated_settings / "data" / "ocr")
    assert len(list(store.root.glob("*/fake-*/page-0001.json"))) == 2

    code, output = invoke("run", str(scans), "-e", "fake")

    assert (code, output.splitlines()[-1]) == (0, "nothing left to read")


def test_run_takes_the_engines_of_the_settings(
    scans: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TEST_OCR_ENGINES", "fake")

    code, output = invoke("run", str(scans))

    assert code == 0, output
    assert "== fake" in output


def test_run_needs_an_engine(scans: Path) -> None:
    code, output = invoke("run", str(scans))

    assert code == 2
    assert "TEST_OCR_ENGINES" in output


def test_the_help_names_the_programs_settings() -> None:
    group = typer.main.get_command(app)
    assert isinstance(group, typer.core.TyperGroup)
    ocr = group.commands["ocr"]
    assert isinstance(ocr, typer.core.TyperGroup)
    run = ocr.commands["run"]
    options = {p.name: p for p in run.params}

    assert "TEST_OCR_DIR" in (run.help or "")
    assert "TEST_OCR_ENGINES" in (getattr(options["engine_names"], "help", None) or "")
    assert options["password"].envvar == "TEST_PDF_PASSWORD"
    for command in ocr.commands.values():
        helps = [command.help or "", *(getattr(p, "help", None) or "" for p in command.params)]
        assert not [h for h in helps if "{settings}" in h or "{command}" in h], command.name


def test_run_tells_the_engines_there_are(scans: Path) -> None:
    code, output = invoke("run", str(scans), "-e", "nope")

    assert code == 2
    assert "yomitoku" in output


def test_run_exits_with_1_when_a_page_fails(scans: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_OCR", json.dumps({"fail": [1]}))

    code, output = invoke("run", str(scans), "-e", "fake")

    assert code == 1
    assert "FAILED: RuntimeError: can't read page 1" in output


def test_run_skips_an_engine_that_can_t_run_here(scans: Path) -> None:
    other = "windows-ocr" if sys.platform != "win32" else "apple-vision"

    code, output = invoke("run", str(scans), "-e", other, "-e", "fake")

    assert code == 1
    assert "skipped: runs on" in output
    assert "2 page(s) read" in output


def test_status_shows_what_s_read_and_what_failed(
    scans: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_OCR", json.dumps({"fail": [1]}))
    invoke("run", str(scans / "bill.pdf"), "-e", "fake")
    monkeypatch.setenv("FAKE_OCR", json.dumps({}))
    invoke("run", str(scans / "receipt.png"), "-e", "fake")

    code, output = invoke("status", str(scans))

    assert code == 0, output
    assert output.splitlines() == [
        "Using: engines every engine (default); orient none (default); preprocess none (default)",
        "bill.pdf: 2 page(s), 1 to read by OCR",
        "    fake: 0/1 read",
        "        p1 FAILED: RuntimeError: can't read page 1",
        "receipt.png: 1 page(s), 1 to read by OCR",
        "    fake: 1/1 read",
    ]


def test_dump_writes_what_was_read(isolated_settings: Path, scans: Path) -> None:
    invoke("run", str(scans), "-e", "fake", "--force")
    output_file = isolated_settings / "data" / "ocr.csv"

    code, output = invoke("dump-text", str(scans), "-e", "fake", "-o", str(output_file))

    assert code == 0, output
    assert "3 piece(s) of text from 3 page(s) of 2 file(s)" in output
    with output_file.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert [(r["file"], r["page"], r["text"]) for r in rows] == [
        ("bill.pdf", "1", "合計 ¥1,234"),
        ("bill.pdf", "2", "合計 ¥1,234"),
        ("receipt.png", "1", "合計 ¥1,234"),
    ]
    assert float(rows[0]["x0"]) == pytest.approx(10 * 72 / 100, abs=0.01)
    assert rows[0]["confidence"] == "0.9"


def test_dump_fails_when_nothing_was_read(isolated_settings: Path, scans: Path) -> None:
    output_file = isolated_settings / "data" / "ocr.json"

    code, _ = invoke("dump-text", str(scans), "-e", "fake", "-o", str(output_file))

    assert code == 1
    assert not output_file.exists()


def test_engines_lists_every_engine() -> None:
    code, output = invoke("list-engines")

    assert code == 0
    for engine in engines.ENGINES:
        assert engine.name in output
    assert "fake           not installed yet" in output


def test_a_changed_resolution_range_reads_the_pages_again(
    isolated_settings: Path, scans: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    invoke("run", str(scans), "-e", "fake")
    monkeypatch.setenv("TEST_OCR_MAX_DPI", "300")

    code, output = invoke("run", str(scans), "-e", "fake")

    assert code == 0, output
    assert "2 page(s) read" in output
    store = Store(isolated_settings / "data" / "ocr")
    assert {p.parent.name for p in store.root.glob("*/fake-*/page-0001.json")} == {
        RunKey("fake", min_dpi=100, max_dpi=200).slug,
        RunKey("fake", min_dpi=100, max_dpi=300).slug,
    }


def test_samples_writes_documents_with_their_truth(
    isolated_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(samples, "font_loader", lambda path: ImageFont.load_default)
    monkeypatch.setattr(samples, "VARIANTS", (samples.Variant("150", dpi=150),))
    folder = isolated_settings / "data" / "bench"

    code, output = invoke("make-samples", str(folder), "--count", "2", "--font", __file__)

    assert code == 0, output
    count = len(sample_documents.DOCUMENTS) * 2
    assert f"{count} sample(s) written" in output
    assert len(list(folder.glob("*.truth.json"))) == count


def test_compare_scores_what_was_read_against_the_truth(
    isolated_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = isolated_settings / "data" / "bench"
    variants = [samples.Variant("300"), samples.Variant("150", dpi=150)]
    paths = list(
        samples.write(
            folder,
            ImageFont.load_default,
            count=1,
            documents={"invoice": sample_documents.DOCUMENTS["invoice"]},
            variants=variants,
        )
    )
    store = Store(isolated_settings / "data" / "ocr")
    key = RunKey("fake")
    # An engine that reads the first exactly, and one amount of the second wrong.
    for path in paths:
        truth = samples.load_truth(path)
        assert truth is not None
        words = [
            OcrWord(line.text, line.x0, line.x1, line.top, line.bottom, 0.9) for line in truth.lines
        ]
        if "150" in path.name:
            first = next(i for i, line in enumerate(truth.lines) if line.kind == "amount")
            words[first] = replace(words[first], text="¥0")
        stored = StoredPage(
            page=OcrPage(1, truth.width, truth.height, 300, words),
            engine=EngineInfo("fake", "1.0"),
        )
        store.save(hashlib.sha256(path.read_bytes()).hexdigest(), key, stored)

    code, output = invoke("score-engines", str(folder), "-e", "fake")

    assert code == 0, output
    lines = output.splitlines()
    assert (
        lines[0]
        == "Using: engines fake (--engine); orient none (default); preprocess none (default)"
    )
    assert lines[1] == "== fake: 2 sample(s) scored, 0 not read yet"
    assert "dates 2/2 (100.0%) read exactly" in lines[2]
    assert any(line.split()[:2] == ["300", "100.0%"] for line in lines)
    assert "    amounts read wrong (1; truth -> read):" in lines
    assert lines[-1].endswith("-> ¥0")


def test_compare_needs_samples_with_a_truth(scans: Path) -> None:
    code, output = invoke("score-engines", str(scans), "-e", "fake")

    assert code == 1
    assert "No samples with a truth" in output


def test_compare_tells_when_nothing_was_read(isolated_settings: Path) -> None:
    folder = isolated_settings / "data" / "bench"
    list(
        samples.write(
            folder,
            ImageFont.load_default,
            count=1,
            documents={"receipt": sample_documents.DOCUMENTS["receipt"]},
            variants=[samples.Variant("150", dpi=150)],
        )
    )

    code, output = invoke("score-engines", str(folder), "-e", "fake")

    assert code == 1
    assert "No sample read yet with fake" in output


def test_run_turns_pages_upright_with_orient_and_keeps_them_apart(
    scans: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_OCR", json.dumps({"turns": {"1": [180, 9.0]}}))

    code, output = invoke("run", str(scans), "-e", "fake", "--orient", "fake")

    assert code == 0, output
    assert "telling how 2 page(s) are turned, with fake" in output
    assert "turned 180 degrees clockwise to be upright (confidence 9)" in output
    _, upright = invoke("status", str(scans), "-e", "fake", "--orient", "fake")
    _, as_they_are = invoke("status", str(scans), "-e", "fake")
    assert "    fake: 1/1 read" in upright.splitlines()
    assert "    fake: 1/1 read" not in as_they_are.splitlines()
    assert "    fake: read with other settings only (orient fake)" in as_they_are.splitlines()


def test_what_was_read_with_other_settings_is_told_with_where_the_settings_are_from(
    scans: Path, monkeypatch: pytest.MonkeyPatch, isolated_settings: Path
) -> None:
    invoke("run", str(scans), "-e", "fake", "--preprocess", "flatten")
    monkeypatch.setenv("TEST_OCR_ORIENT", "fake")
    output_file = isolated_settings / "data" / "words.csv"

    code, output = invoke("dump-text", str(scans), "-e", "fake", "-o", str(output_file))

    assert code == 1
    assert output.splitlines()[0] == (
        "Using: engine fake (--engine); orient fake (TEST_OCR_ORIENT); preprocess none (default)"
    )
    assert (
        "fake: 2 file(s) read with other settings only (orient none, preprocess flatten)"
        in output.splitlines()
    )


def test_orient_needs_an_engine_there_is(scans: Path) -> None:
    code, output = invoke("run", str(scans), "-e", "fake", "--orient", "nope")

    assert code == 2
    assert "no OCR engine 'nope'" in output


def test_preprocess_needs_steps_there_are(scans: Path) -> None:
    code, output = invoke("run", str(scans), "-e", "fake", "--preprocess", "flatten,sharpen")

    assert code == 2
    assert "no preprocessing step 'sharpen'" in output


def test_run_with_all_reads_with_every_engine_installed_here(
    isolated_settings: Path, scans: Path
) -> None:
    (isolated_settings / "workers" / "fake" / ".venv").mkdir()

    code, output = invoke("run", str(scans), "-e", "all")

    assert code == 0, output
    assert "leaving out yomitoku: no worker at" in output
    assert "== fake" in output
    assert "2 page(s) read" in output


def test_run_with_all_needs_an_engine_installed(scans: Path) -> None:
    code, output = invoke("run", str(scans), "-e", "all")

    assert code == 2
    assert "no engine is installed here" in output


def test_compare_writes_markdown_tables(isolated_settings: Path) -> None:
    folder = isolated_settings / "data" / "bench"
    (path,) = samples.write(
        folder,
        ImageFont.load_default,
        count=1,
        documents={"receipt": sample_documents.DOCUMENTS["receipt"]},
        variants=[samples.Variant("150", dpi=150)],
    )
    truth = samples.load_truth(path)
    assert truth is not None
    words = [OcrWord(t.text, t.x0, t.x1, t.top, t.bottom) for t in truth.lines]
    Store(isolated_settings / "data" / "ocr").save(
        hashlib.sha256(path.read_bytes()).hexdigest(),
        RunKey("fake"),
        StoredPage(OcrPage(1, truth.width, truth.height, 150, words), EngineInfo("fake", "1.0")),
    )

    code, output = invoke("score-engines", str(folder), "-e", "fake", "--markdown")

    assert code == 0, output
    lines = output.splitlines()
    assert lines[1] == "- 設定: なし"
    assert lines[5].startswith("| fake | 1.0 | - | 100.0% | 100.0% | 100.0% | 0.0% | 0/")
    assert "| `150` | 100.0% / 100.0% |" in lines


def test_compare_agreement_counts_what_cross_checking_would_find(
    isolated_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(engines, "ENGINES", (*engines.ENGINES, Engine("fake2", "fake", "")))
    folder = isolated_settings / "data" / "bench"
    (path,) = samples.write(
        folder,
        ImageFont.load_default,
        count=1,
        documents={"receipt": sample_documents.DOCUMENTS["receipt"]},
        variants=[samples.Variant("150", dpi=150)],
    )
    truth = samples.load_truth(path)
    assert truth is not None
    amounts = [i for i, line in enumerate(truth.lines) if line.kind == "amount"]
    store = Store(isolated_settings / "data" / "ocr")
    for name, wrong in (
        ("fake", {amounts[0]: "¥0", amounts[1]: "¥0"}),
        ("fake2", {amounts[0]: "¥9"}),
    ):
        words = [
            OcrWord(wrong.get(i, t.text), t.x0, t.x1, t.top, t.bottom)
            for i, t in enumerate(truth.lines)
        ]
        store.save(
            hashlib.sha256(path.read_bytes()).hexdigest(),
            RunKey(name),
            StoredPage(OcrPage(1, truth.width, truth.height, 150, words), EngineInfo(name, "1.0")),
        )

    code, output = invoke("score-engines", str(folder), "-e", "fake", "-e", "fake2", "--agreement")

    assert code == 0, output
    lines = output.splitlines()
    assert lines[1] == "== fake and fake2: 1 sample(s) read by both"
    assert lines[2] == (
        f"amounts {len(amounts)}: both right {len(amounts) - 2}; wrong by fake only 1, "
        "by fake2 only 0; both wrong 1 differently, 0 alike (not shown by diff-engines)"
    )

    code, output = invoke(
        "score-engines", str(folder), "-e", "fake", "-e", "fake2", "--agreement", "--markdown"
    )

    assert code == 0, output
    assert output.splitlines()[2].startswith(f"| fake | fake2 | 金額 | {len(amounts)} | ")


def test_compare_agreement_needs_two_engines_that_read_a_sample(isolated_settings: Path) -> None:
    folder = isolated_settings / "data" / "bench"
    list(
        samples.write(
            folder,
            ImageFont.load_default,
            count=1,
            documents={"receipt": sample_documents.DOCUMENTS["receipt"]},
            variants=[samples.Variant("150", dpi=150)],
        )
    )

    code, output = invoke("score-engines", str(folder), "-e", "fake", "--agreement")

    assert code == 1
    assert "No sample read yet by two of fake" in output


def test_diff_lists_where_engines_read_numbers_differently(
    scans: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = Engine(
        "fake2",
        "fake",
        "another fake engine",
        options={
            "boxes": [
                {"text": "合計 ¥1,284", "x0": 12, "y0": 20, "x1": 110, "y1": 40},
                {"text": "印", "x0": 300, "y0": 300, "x1": 330, "y1": 330},
            ]
        },
    )
    monkeypatch.setattr(engines, "ENGINES", (*engines.ENGINES, other))
    invoke("run", str(scans), "-e", "fake", "-e", "fake2")

    code, output = invoke("diff-engines", str(scans), "-e", "fake", "-e", "fake2")
    _, everything = invoke("diff-engines", str(scans), "-e", "fake", "-e", "fake2", "--all")

    assert code == 0, output
    lines = output.splitlines()
    assert lines[1:3] == ["bill.pdf p1", "    (7, 14) fake: 合計 ¥1,234 | fake2: 合計 ¥1,284"]
    assert lines[-1] == (
        "2 page(s) read by fake, fake2: 0 of 2 number(s) read alike, 2 differ "
        "(0 read by some only); 0 of 2 other piece(s) of text read alike"
    )
    assert "fake: (nothing) | fake2: 印" in everything


def test_diff_by_digits_leaves_out_numbers_read_alike_whatever_is_around_them(
    scans: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = Engine(
        "fake2",
        "fake",
        "another fake engine",
        options={
            "boxes": [
                {"text": "合討 ¥1,234", "x0": 12, "y0": 20, "x1": 110, "y1": 40},  # a kanji
                {"text": "〒000-0000", "x0": 300, "y0": 300, "x1": 400, "y1": 320},
            ]
        },
    )
    monkeypatch.setattr(engines, "ENGINES", (*engines.ENGINES, other))
    invoke("run", str(scans), "-e", "fake", "-e", "fake2")

    _, by_text = invoke("diff-engines", str(scans), "-e", "fake", "-e", "fake2")
    code, by_digits = invoke("diff-engines", str(scans), "-e", "fake", "-e", "fake2", "--digits")

    assert code == 0, by_digits
    assert "fake: 合計 ¥1,234 | fake2: 合討 ¥1,234" in by_text
    assert "合討" not in by_digits
    assert "fake: (nothing) | fake2: 〒000-0000" in by_digits  # read by one engine only
    assert by_digits.splitlines()[-1].startswith(
        "2 page(s) read by fake, fake2: 2 of 4 number(s) read alike by their digits, 2 differ "
    )


def test_diff_escapes_what_the_console_has_no_character_for(
    scans: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = Engine(
        "fake2",
        "fake",
        "another fake engine",
        options={"boxes": [{"text": "小さじ1½", "x0": 12, "y0": 20, "x1": 110, "y1": 40}]},
    )
    monkeypatch.setattr(engines, "ENGINES", (*engines.ENGINES, other))
    invoke("run", str(scans), "-e", "fake", "-e", "fake2")

    # As the Windows console: cp932 has no ½.
    result = CliRunner(charset="cp932").invoke(
        app, ["ocr", "diff-engines", str(scans), "-e", "fake", "-e", "fake2"]
    )

    assert result.exit_code == 0, result.output
    assert "fake2: 小さじ1\\xbd" in result.output


def test_diff_needs_pages_read_by_two_engines(scans: Path) -> None:
    invoke("run", str(scans), "-e", "fake")

    code, output = invoke("diff-engines", str(scans), "-e", "fake")

    assert code == 1
    assert "No page read by two engines or more" in output


def test_remove_missing_lists_and_removes_what_s_kept_of_files_gone(
    isolated_settings: Path, scans: Path
) -> None:
    invoke("run", str(scans), "-e", "fake")
    store = Store(isolated_settings / "data" / "ocr")
    assert len(store.files()) == 2
    (scans / "receipt.png").unlink()

    code, listed = invoke("remove-missing")

    assert code == 0, listed
    assert listed.splitlines()[0].startswith(f"  receipt.png (gone from {scans / 'receipt.png'}), ")
    assert listed.splitlines()[-1].endswith("add --apply to remove them")
    assert len(store.files()) == 2  # only listed

    code, removed = invoke("remove-missing", "--apply")

    assert code == 0, removed
    assert removed.splitlines()[-1].startswith("Removed 1 item(s)")
    assert [store.name_of(sha) for sha in store.files()] == ["bill.pdf"]


def test_run_keeps_where_files_were_read_as_absolute_paths(
    isolated_settings: Path, scans: Path
) -> None:
    # Given relative to the working directory, and then with "..".
    invoke("run", "data/scans/bill.pdf", "-e", "fake")
    invoke("run", "data/../data/scans", "-e", "fake")
    store = Store(isolated_settings / "data" / "ocr")

    kept = {store.name_of(sha): store.locations(sha) for sha in store.files()}

    assert kept == {"bill.pdf": [scans / "bill.pdf"], "receipt.png": [scans / "receipt.png"]}


def test_remove_missing_keeps_what_was_read_in_a_folder_not_there(
    isolated_settings: Path, scans: Path, tmp_path: Path
) -> None:
    drive = tmp_path / "drive"
    drive.mkdir()
    scan((40, 20)).save(drive / "photo.png")
    invoke("run", str(drive), "-e", "fake")
    drive.rename(tmp_path / "unplugged")

    code, kept = invoke("remove-missing", "--apply")
    _, removed = invoke("remove-missing", "--missing-dirs-too", "--apply")

    assert code == 0, kept
    assert kept.splitlines() == [
        f"kept 1 file(s) read in {drive}: the folder isn't there "
        "(a drive not connected? if it's gone, add --missing-dirs-too)",
        "Nothing to remove",
    ]
    assert removed.splitlines()[-1].startswith("Removed 1 item(s)")


def test_remove_missing_finds_what_was_read_before_paths_were_kept_in_folders_given(
    isolated_settings: Path, scans: Path
) -> None:
    invoke("run", str(scans), "-e", "fake")
    store = Store(isolated_settings / "data" / "ocr")
    for sha in store.files():  # as kept before
        source = store.root / sha / "source.json"
        data = json.loads(source.read_text(encoding="utf-8"))
        del data["paths"]
        source.write_text(json.dumps(data), encoding="utf-8")
    (scans / "receipt.png").unlink()

    _, unknown = invoke("remove-missing")
    code, removed = invoke("remove-missing", str(scans), "--apply")

    assert unknown.splitlines() == [
        "kept 2 file(s) read before where they were read was kept: run `test ocr run` on "
        "their folders again to keep it, or give the folders all scans are in",
        "Nothing to remove",
    ]
    assert code == 0, removed
    assert removed.splitlines()[0].startswith("  receipt.png (not under the folders given), ")
    assert [store.name_of(sha) for sha in store.files()] == ["bill.pdf"]


def test_remove_engine_and_remove_logs(isolated_settings: Path, scans: Path) -> None:
    invoke("run", str(scans), "-e", "fake")
    store = Store(isolated_settings / "data" / "ocr")

    code, engine = invoke("remove-engine", "fake", "--apply")
    _, logs = invoke("remove-logs", "--apply")
    bad, unknown = invoke("remove-engine", "nope")

    assert code == 0, engine
    assert all(store.runs(sha) == [] for sha in store.files())
    assert not (store.root / "logs").exists()
    assert "  logs and scratch files, " in logs
    assert bad == 2
    assert "no engine nope (use one of: " in unknown


def test_engines_check_starts_each_engine_installed_and_reads_a_page(
    isolated_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (isolated_settings / "workers" / "fake" / ".venv").mkdir()

    code, output = invoke("check-engines")

    assert code == 0, output
    (line,) = [line for line in output.splitlines() if line.startswith("fake ")]
    assert line.startswith("fake           works: fake 1.0 (fake-model) on cpu; started in ")
    assert line.endswith("amounts right); tells how pages are turned")


def test_engines_check_tells_an_engine_that_fails(
    isolated_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (isolated_settings / "workers" / "fake" / ".venv").mkdir()
    monkeypatch.setenv("FAKE_OCR", json.dumps({"fatal": "no model"}))

    code, output = invoke("check-engines")

    assert code == 1
    assert "fake           FAILED: couldn't start: StartError: no model (log: " in output


def test_engines_check_tells_an_engine_that_stops_after_starting(
    isolated_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (isolated_settings / "workers" / "fake" / ".venv").mkdir()
    monkeypatch.setenv("FAKE_OCR", json.dumps({"crash": [1]}))

    _, reading = invoke("check-engines")
    monkeypatch.setenv("FAKE_OCR", json.dumps({"crash_orient": True}))
    _, orienting = invoke("check-engines")

    assert "fake           FAILED: stopped reading the page: " in reading
    assert "fake           FAILED: stopped telling how the page is turned: " in orienting


def test_engines_lists_without_writing_and_takes_a_device_there_is(isolated_settings: Path) -> None:
    code, _ = invoke("list-engines")
    bad, output = invoke("check-engines", "--device", "gpu")

    assert code == 0
    assert not (isolated_settings / "data").exists()
    assert bad == 2
    assert "'gpu' is not one of 'auto'" in output


def test_diff_compares_every_engine_whatever_the_settings_name(
    scans: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = Engine("fake2", "fake", "another fake engine", options={"boxes": []})
    monkeypatch.setattr(engines, "ENGINES", (*engines.ENGINES, other))
    invoke("run", str(scans), "-e", "fake", "-e", "fake2")
    monkeypatch.setenv("TEST_OCR_ENGINES", "fake")  # as usual, one engine

    code, output = invoke("diff-engines", str(scans))

    assert code == 0, output
    assert output.splitlines()[-1].startswith("2 page(s) read by fake, fake2: ")
    assert "not read by every engine given" not in output


def test_remove_missing_keeps_what_s_kept_of_any_file_still_under_the_folders(
    isolated_settings: Path, scans: Path, tmp_path: Path
) -> None:
    # Read as given, though `ocr run` wouldn't find it in a folder.
    scan((40, 20)).save(scans / "photo.gif")
    invoke("run", str(scans / "photo.gif"), "-e", "fake")
    # And under a link to a folder.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    scan((40, 30)).save(elsewhere / "linked.png")
    try:
        (scans / "linked").symlink_to(elsewhere, target_is_directory=True)
    except OSError:
        pytest.skip("no symbolic links here")
    invoke("run", str(scans / "linked"), "-e", "fake")
    assert len(Store(isolated_settings / "data" / "ocr").files()) == 2

    recorded = invoke("remove-missing", "--apply")
    under_folders = invoke("remove-missing", str(scans), "--apply")

    assert (recorded[0], recorded[1].splitlines()) == (0, ["Nothing to remove"])
    assert (under_folders[0], under_folders[1].splitlines()) == (0, ["Nothing to remove"])


def test_remove_logs_leaves_the_scratch_files_of_a_run_going_on(isolated_settings: Path) -> None:
    scratch = isolated_settings / "data" / "ocr" / "tmp"
    (scratch / "going-on").mkdir(parents=True)
    (scratch / "stopped").mkdir()
    day_ago = time.time() - 25 * 60 * 60
    os.utime(scratch / "stopped", (day_ago, day_ago))

    code, output = invoke("remove-logs", "--apply")
    _, again = invoke("remove-logs")

    assert code == 0, output
    assert [p.name for p in scratch.iterdir()] == ["going-on"]
    assert again.splitlines() == ["Nothing to remove"]


def test_split_scans_cuts_out_each_sheet_of_a_page_scanned_with_several(
    isolated_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TEST_OCR_MIN_DPI", "150")
    monkeypatch.setenv("TEST_OCR_MAX_DPI", "150")
    sheet = form((400, 560), paper=12)  # about 68x95 mm at 150 dpi
    two = flatbed((1240, 1754), [(sheet, 30, 30, 0), (sheet, 500, 40, -1.5)])
    one = flatbed((1240, 1754), [(sheet, 30, 30, 0)])
    folder = isolated_settings / "data" / "scans"
    write_pdf(folder / "slips.pdf", [two, one, None], texts=[None, None, "本文"])
    out = isolated_settings / "data" / "cut"

    code, output = invoke("split-scans", str(folder), "-o", str(out))

    assert code == 0, output
    assert "slips.pdf p1: 2 document(s)" in output
    assert re.search(
        r"2: \d+x\d+ mm at \(\d+, \d+\) mm, turned \+1\.\d\d degrees -> slips_p1-2.png", output
    )
    assert "slips.pdf p2: 1 document(s)" in output  # cut out of the page's empty bed
    assert "p3" not in output  # a page with text isn't a scan
    names = ["slips_p1-1.png", "slips_p1-2.png", "slips_p2-1.png"]
    assert sorted(p.name for p in out.iterdir()) == names
    with Image.open(out / "slips_p1-1.png") as image:
        assert image.info["dpi"] == pytest.approx((150, 150), abs=0.1)

    code, output = invoke("split-scans", str(folder), "-o", str(out))

    assert code == 0, output
    assert "slips_p1-1.png exists, kept" in output
    assert "0 image(s) written" in output


def test_split_scans_exits_with_1_when_a_page_has_no_sheet(isolated_settings: Path) -> None:
    folder = isolated_settings / "data" / "scans"
    write_pdf(folder / "blank.pdf", [a4_scan(100)])

    code, output = invoke("split-scans", str(folder), "-o", str(isolated_settings / "out"))

    assert code == 1
    assert "blank.pdf p1: 0 document(s)" in output
