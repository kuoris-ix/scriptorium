from pathlib import Path

import pytest

from scriptorium.files import absolute_path, find_files


def test_find_files_matches_any_pattern_once_in_path_order(tmp_path: Path) -> None:
    for name in ["b.csv", "c.txt", "sub/d.csv"]:
        (tmp_path / name).parent.mkdir(exist_ok=True)
        (tmp_path / name).write_text("")

    found = find_files(tmp_path, ["*.csv", "b.*", "**/d.csv"])

    assert [p.relative_to(tmp_path).as_posix() for p in found] == ["b.csv", "sub/d.csv"]


def test_find_files_ignores_case_and_leaves_out_folders(tmp_path: Path) -> None:
    (tmp_path / "A.PDF").write_text("")
    (tmp_path / "b.pdf").write_text("")
    (tmp_path / "folder.pdf").mkdir()

    found = find_files(tmp_path, ["*.pdf"])

    assert [p.name for p in found] == ["A.PDF", "b.pdf"]


def test_absolute_path_leaves_out_dots_and_keeps_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "real").mkdir()
    try:
        (tmp_path / "link").symlink_to(tmp_path / "real", target_is_directory=True)
    except OSError:
        pytest.skip("no symbolic links here")
    monkeypatch.chdir(tmp_path / "real")

    assert absolute_path(Path("../link/a.pdf")) == tmp_path / "link" / "a.pdf"
