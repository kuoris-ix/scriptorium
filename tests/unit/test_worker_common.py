"""The workers' shared module (workers/ocr_worker.py), as far as it runs without an engine."""

import os
import shutil
import sys
from pathlib import Path

import ocr_worker
import pytest


def test_a_box_around_slanted_text_holds_all_its_corners() -> None:
    box = ocr_worker.Box.around("合計", [[10, 22], [110, 20], [112, 40], [12, 42]], 0.5)

    assert (box.x0, box.y0, box.x1, box.y1, box.confidence) == (10, 20, 112, 42, 0.5)


@pytest.mark.parametrize(
    ("requested", "available", "picked"),
    [
        ("auto", ["cuda", "cpu"], "cuda"),
        ("auto", ["cpu"], "cpu"),
        ("cpu", ["cuda", "cpu"], "cpu"),
        ("mps", ["mps", "cpu"], "mps"),
    ],
)
def test_pick_device(requested: str, available: list[str], picked: str) -> None:
    assert ocr_worker.pick_device(requested, available) == picked


@pytest.mark.parametrize(
    ("requested", "message"),
    [("cuda", "isn't available here"), ("gpu", "unknown device")],
)
def test_pick_device_fails_rather_than_use_another(requested: str, message: str) -> None:
    with pytest.raises(ocr_worker.StartError, match=message):
        ocr_worker.pick_device(requested, ["cpu"])


def test_find_command_doesn_t_look_in_the_working_folder_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    here, on_path = tmp_path / "here", tmp_path / "on_path"
    here.mkdir()
    on_path.mkdir()
    (here / "tesseract.EXE").write_text("")
    monkeypatch.chdir(here)
    monkeypatch.setenv("PATHEXT", ".EXE")
    monkeypatch.setenv("PATH", os.pathsep.join([".", "", str(on_path)]))

    assert ocr_worker.find_command("tesseract", platform="win32") is None

    (on_path / "tesseract.EXE").write_text("")

    assert ocr_worker.find_command("tesseract", platform="win32") == str(on_path / "tesseract.EXE")


def test_find_command_is_which_where_it_looks_in_the_path_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    program = tmp_path / ("zzprogram.exe" if sys.platform == "win32" else "zzprogram")
    program.write_text("")
    program.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("PATHEXT", ".EXE")

    found = ocr_worker.find_command("zzprogram", platform="linux")

    assert found is not None
    assert found == shutil.which("zzprogram")
    assert ocr_worker.find_command("zznothing", platform="linux") is None
