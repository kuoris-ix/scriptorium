"""The `scriptorium` command's settings: from the environment, else from `.env`."""

import os
import re
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scriptorium import cli, fictional


@pytest.fixture(autouse=True)
def no_settings_given(isolated_settings: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [n for n in os.environ if n.startswith(cli.PREFIX)]:
        monkeypatch.delenv(name)


def test_the_env_file_s_lines_are_read_as_shells_and_dotenv_tools_read_them(
    tmp_path: Path,
) -> None:
    path = tmp_path / ".env"
    lines = ["# settings", "", "A=1", "export B = two words  # a comment"]
    lines += ["C='kept # as it is'", 'D=""', "E=#"]
    # With a BOM, as some Windows editors save it.
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8-sig")

    assert cli.read_env_file(path) == {
        "A": "1",
        "B": "two words",
        "C": "kept # as it is",
        "D": "",
        "E": "#",
    }
    assert cli.read_env_file(tmp_path / "none") == {}


@pytest.mark.parametrize("line", ["ENGINES", "=1", "TWO WORDS=1"])
def test_an_env_file_line_not_setting_a_variable_is_an_error(tmp_path: Path, line: str) -> None:
    path = tmp_path / ".env"
    path.write_text(f"A=1\n{line}\n", encoding="utf-8")

    with pytest.raises(cli.SettingError, match=r"\.env line 2: expected NAME=VALUE"):
        cli.read_env_file(path)


def test_the_settings_are_the_environment_s_else_the_env_file_s(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "SCRIPTORIUM_ENGINES=tesseract\nSCRIPTORIUM_DEVICE=cpu\nOTHER=1\n", encoding="utf-8"
    )

    config = cli.config({"SCRIPTORIUM_ENGINES": "yomitoku", "PATH": "x"}, env_file)

    assert (config.engines, config.device) == ("yomitoku", "cpu")
    assert config.origins == {
        "engines": "SCRIPTORIUM_ENGINES",
        "device": f"SCRIPTORIUM_DEVICE in {env_file}",
    }


def test_the_defaults_read_into_the_working_folder_with_the_clone_s_workers() -> None:
    config = cli.config({}, Path(".env"))

    assert config.ocr_dir == Path.cwd() / "data" / "ocr"
    assert (config.workers_dir / "ocr_worker.py").is_file()
    assert config.origins == {}
    assert config.documents == fictional.DOCUMENTS
    assert (config.engines, config.device, config.min_dpi, config.worker_pages) == (
        "",
        "auto",
        300,
        1000,
    )


@pytest.mark.parametrize(("value", "jobs"), [("4", 4), ("auto", None), ("", None)])
def test_the_workers_to_read_with_are_a_number_or_auto(value: str, jobs: int | None) -> None:
    config = cli.config({"SCRIPTORIUM_JOBS": value}, Path(".env"))

    assert config.jobs == jobs
    assert config.origin("jobs") == ("SCRIPTORIUM_JOBS" if value else "default")


def test_a_setting_given_empty_is_as_not_given() -> None:
    config = cli.config({"SCRIPTORIUM_DEVICE": "", "SCRIPTORIUM_MIN_DPI": ""}, Path(".env"))

    assert (config.device, config.min_dpi, config.origins) == ("auto", 300, {})


@pytest.mark.parametrize(
    ("variables", "message"),
    [
        ({"SCRIPTORIUM_ENGINS": "x"}, "no setting SCRIPTORIUM_ENGINS (there are: SCRIPTORIUM_DIR,"),
        ({"SCRIPTORIUM_DEVICE": "gpu"}, "SCRIPTORIUM_DEVICE: 'gpu' is not one of auto, cpu"),
        ({"SCRIPTORIUM_MIN_DPI": "high"}, "SCRIPTORIUM_MIN_DPI: not a number: 'high'"),
        ({"SCRIPTORIUM_JOBS": "0"}, "SCRIPTORIUM_JOBS: '0' is neither auto nor a number of"),
        ({"SCRIPTORIUM_JOBS": "many"}, "SCRIPTORIUM_JOBS: 'many' is neither auto nor a number"),
    ],
)
def test_a_setting_that_can_t_be_used_is_an_error(variables: dict[str, str], message: str) -> None:
    with pytest.raises(cli.SettingError, match=re.escape(message)):
        cli.config(variables, Path(".env"))


def invoke(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(cli.app, list(args))
    return result.exit_code, re.sub(r"\x1b\[[0-9;]*m", "", result.output)


def test_the_command_shows_settings_with_where_they_re_from(isolated_settings: Path) -> None:
    (isolated_settings / "workers").mkdir()
    Path(".env").write_text("SCRIPTORIUM_WORKERS_DIR=workers\n", encoding="utf-8")

    code, output = invoke("list-engines")

    assert code == 0, output
    expected = f"Using: workers {Path.cwd() / 'workers'} (SCRIPTORIUM_WORKERS_DIR in .env)"
    assert output.splitlines()[0] == expected


def test_the_command_tells_a_setting_that_can_t_be_used_and_exits_with_2() -> None:
    Path(".env").write_text("SCRIPTORIUM_DEVICE=gpu\n", encoding="utf-8")

    code, output = invoke("list-engines")

    assert code == 2
    assert "Error: SCRIPTORIUM_DEVICE in .env: 'gpu' is not one of" in output


def test_the_command_names_itself_and_its_settings_in_its_help() -> None:
    code, output = invoke("run", "--help")

    assert code == 0
    assert "SCRIPTORIUM_DIR" in output
    assert "scriptorium remove-missing" in output


def test_main_takes_the_password_from_the_env_file_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Path(".env").write_text("SCRIPTORIUM_PDF_PASSWORD=secret\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["scriptorium", "list-engines"])

    with pytest.raises(SystemExit) as exited:
        cli.main()

    assert exited.value.code == 0
    assert os.environ["SCRIPTORIUM_PDF_PASSWORD"] == "secret"


def test_main_shows_its_help_whatever_is_in_the_env_file(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    Path(".env").write_text("oops\n", encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["scriptorium", "--help"])
    with pytest.raises(SystemExit) as helped:
        cli.main()
    monkeypatch.setattr(sys, "argv", ["scriptorium", "list-engines"])
    with pytest.raises(SystemExit) as listed:
        cli.main()

    assert (helped.value.code, listed.value.code) == (0, 2)
    assert "Error: .env line 1: expected NAME=VALUE" in capsys.readouterr().err
