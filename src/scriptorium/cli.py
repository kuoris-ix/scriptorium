"""The `scriptorium` command: the `ocr` commands on their own, to try scriptorium or compare
engines without a program of one's own.

Run from a clone of the repository (`uv run scriptorium ...`). Its settings are
`SCRIPTORIUM_*` variables, from the environment or else from `.env` in the
working folder, and are read again for each command run. Paths are relative to
the working folder; the workers' folder is the clone's `workers/` unless set.
It has the fictional documents of `scriptorium.fictional`.
"""

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import typer

from scriptorium import fictional
from scriptorium.commands import make_app
from scriptorium.config import OcrConfig, ProgramNames
from scriptorium.models import DEVICES, ORIENT_MIN_CONFIDENCE, Device

PREFIX = "SCRIPTORIUM_"
NAMES = ProgramNames(
    command="scriptorium", settings_prefix=PREFIX, password_variable=f"{PREFIX}PDF_PASSWORD"
)
ENV_FILE = Path(".env")
# The clone's `workers/`, next to `src/`: the package is installed from the clone as it is
# (`uv sync` installs it editable).
WORKERS = Path(__file__).parents[2] / "workers"
# The settings there are, besides the password (read by the commands' option, see `main`).
SETTINGS = (
    "dir",
    "workers_dir",
    "engines",
    "device",
    "orient",
    "orient_min_confidence",
    "preprocess",
    "min_dpi",
    "max_dpi",
    "worker_pages",
)
# Those shown with where they're from (see `OcrConfig.origins`).
SHOWN = ("engines", "device", "orient", "preprocess", "workers_dir")


class SettingError(Exception):
    """A setting, or the `.env` file, that can't be used: the message says which and why."""


@dataclass(frozen=True)
class Given:
    """The `SCRIPTORIUM_*` variables given: their values, and where each is from."""

    values: Mapping[str, str]
    origins: Mapping[str, str]

    def get(self, name: str) -> str | None:
        """The setting's value; None if not given, or given empty (as not given)."""
        return self.values.get(PREFIX + name.upper()) or None


def read_env_file(path: Path) -> dict[str, str]:
    """The variables `path` sets, as `NAME=VALUE` lines (`export ` before is allowed).

    Blank lines and lines starting with "#" are left out. A value may be put in
    single or double quotes, kept as it is inside them; one not in quotes ends
    before " #", a comment after it. No file sets none.
    """
    if not path.is_file():
        return {}
    found = {}
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, equals, value = line.removeprefix("export ").partition("=")
        name, value = name.strip(), value.strip()
        if not equals or not name or " " in name:
            raise SettingError(f"{path} line {number}: expected NAME=VALUE")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].rstrip()
        found[name] = value
    return found


def given(environ: Mapping[str, str], env_file: Path) -> Given:
    """The `SCRIPTORIUM_*` variables of the environment, and of `env_file` where the
    environment hasn't them; an unknown one (a misspelt name?) is an error."""
    values, origins = {}, {}
    for source, variables in (
        (f"in {env_file}", read_env_file(env_file)),
        ("", dict(environ)),
    ):
        for name, value in variables.items():
            if name.startswith(PREFIX):
                values[name] = value
                origins[name] = f"{name} {source}".rstrip()
    known = [*(PREFIX + s.upper() for s in SETTINGS), NAMES.password_variable]
    unknown = sorted(origins[n] for n in values if n not in known)
    if unknown:
        raise SettingError(f"no setting {', '.join(unknown)} (there are: {', '.join(known)})")
    return Given(values, origins)


def _number[T: (int, float)](given: Given, name: str, default: T, kind: Callable[[str], T]) -> T:
    text = given.get(name)
    if text is None:
        return default
    try:
        return kind(text)
    except ValueError:
        origin = given.origins[PREFIX + name.upper()]
        raise SettingError(f"{origin}: not a number: {text!r}") from None


def config(environ: Mapping[str, str] = os.environ, env_file: Path = ENV_FILE) -> OcrConfig:
    """The settings, from the environment and `env_file`; else the defaults."""
    found = given(environ, env_file)
    device = found.get("device") or "auto"
    if device not in DEVICES:
        origin = found.origins[f"{PREFIX}DEVICE"]
        raise SettingError(f"{origin}: {device!r} is not one of {', '.join(DEVICES)}")
    return OcrConfig(
        ocr_dir=Path(found.get("dir") or "data/ocr").absolute(),
        workers_dir=Path(found.get("workers_dir") or WORKERS).absolute(),
        documents=fictional.DOCUMENTS,
        check_document=fictional.receipt,
        engines=found.get("engines") or "",
        device=cast(Device, device),
        orient=found.get("orient") or "",
        orient_min_confidence=_number(found, "orient_min_confidence", ORIENT_MIN_CONFIDENCE, float),
        preprocess=found.get("preprocess") or "",
        min_dpi=_number(found, "min_dpi", 300, int),
        max_dpi=_number(found, "max_dpi", 600, int),
        worker_pages=_number(found, "worker_pages", 1000, int),
        origins={
            name: found.origins[PREFIX + name.upper()]
            for name in SHOWN
            if found.get(name) is not None
        },
    )


def _config_or_exit() -> OcrConfig:
    try:
        return config()
    except SettingError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(2) from e


app = make_app(_config_or_exit, NAMES)


def main() -> None:
    """Run the command; the password too may be in `.env` (Typer reads it from the
    environment only, so it's put there)."""
    try:
        password = given(os.environ, ENV_FILE).values.get(NAMES.password_variable)
    except SettingError:
        password = None  # told by the command, when it reads its settings (not for --help)
    if password is not None:
        os.environ[NAMES.password_variable] = password
    app()
