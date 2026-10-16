"""Running an engine's worker and talking with it.

The other side, and the messages, are in workers/ocr_worker.py. A
worker is started once and reads page after page (loading an engine's
models takes long); what it prints besides its answers, and its stderr,
go to a log file. It runs at a low priority, with what it starts (uv, the
engine, Tesseract's commands): what else runs on the machine comes first.
"""

import contextlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import IO, Any, Self

from scriptorium.engines import Engine
from scriptorium.models import EngineInfo, Orientation

PROTOCOL = 1
MARK = "@@scriptorium@@ "
# Variables that would make the worker's uv or Python use the program's environment.
_NOT_PASSED = ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "PYTHONHOME", "PYTHONPATH")
_STOP_SECONDS = 30
_NICENESS = 10  # added to the worker's niceness on Linux and macOS (19 is the lowest priority)


class WorkerFailed(Exception):
    """The worker couldn't start, or stopped; the message tells why."""


class PageFailed(Exception):
    """The worker couldn't read a page (and goes on with the next)."""


def find_command(name: str, platform: str = sys.platform) -> str | None:
    """Where the program `name` is on the PATH, if it is. On Windows, never in the working
    folder, which `shutil.which` looks in first: a uv.exe left in a folder of scans, say,
    would be run."""
    if platform != "win32":
        return shutil.which(name)
    suffixes = [s for s in os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";") if s]
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        folder = Path(entry.strip().strip('"'))
        if not folder.is_absolute():  # "" and "." too: the working folder
            continue
        for suffix in suffixes:
            if (found := folder / f"{name}{suffix}").is_file():
                return str(found)
    return None


def uv_command(engine: Engine, workers_dir: Path) -> list[str]:
    """How to start the worker of `engine`: with uv, in its own environment."""
    uv = os.environ.get("UV") or find_command("uv")
    if uv is None:
        raise WorkerFailed("uv isn't found (it runs the OCR workers)")
    folder = engine.folder(workers_dir).resolve()
    return [uv, "run", "--project", str(folder), "python", str(folder / "worker.py")]


@dataclass(frozen=True)
class Box:
    """A piece of text a worker read, in pixels from the image's top left."""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    confidence: float | None


@dataclass(frozen=True)
class Result:
    boxes: list[Box]
    extra: dict[str, Any]


class Worker:
    """A running worker; use it as a context manager to have it stopped."""

    def __init__(
        self, command: list[str], log: IO[str], *, not_passed: Collection[str] = ()
    ) -> None:
        """Start the worker with `command`, its stderr going to `log`. It's given the
        environment but `_NOT_PASSED` and `not_passed` (e.g. the variable a PDF's password
        is in: a worker reads images only)."""
        self.log = log
        left_out = {*_NOT_PASSED, *not_passed}
        if sys.platform == "win32":  # where variables' names ignore case
            left_out = {name.upper() for name in left_out}
        env = {
            k: v
            for k, v in os.environ.items()
            if (k.upper() if sys.platform == "win32" else k) not in left_out
        }
        env["PYTHONIOENCODING"] = "utf-8"  # what it prints in Japanese, on Windows too
        try:
            self.process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=log,
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                # On Windows, what it starts gets the same priority.
                creationflags=getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0),
            )
        except OSError as e:
            raise WorkerFailed(f"couldn't run {command[0]}: {e}") from e
        if sys.platform != "win32":
            # Right after it starts, before uv starts the worker's Python (which gets the same
            # niceness); not in the child before it runs (preexec_fn), unsafe with threads.
            # If it has already ended, that's told when it's talked with.
            with contextlib.suppress(OSError):
                os.setpriority(os.PRIO_PROCESS, self.process.pid, _NICENESS)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.stop(kill=exc_type is not None)

    def start(self, device: str, options: Mapping[str, Any]) -> EngineInfo:
        """Have the worker load its engine; what it tells about the engine."""
        self._send({"type": "start", "protocol": PROTOCOL, "device": device, "options": options})
        message = self._receive()
        if message.get("type") == "fatal":
            raise WorkerFailed(str(message.get("error")))
        engine = message.get("engine")
        if message.get("type") != "ready" or not isinstance(engine, dict):
            raise WorkerFailed(f"unexpected answer to start: {message!r}")
        try:
            return EngineInfo(
                name=str(engine["name"]),
                version=str(engine["version"]),
                model=_optional_str(engine.get("model")),
                device=_optional_str(engine.get("device")),
            )
        except KeyError as e:
            raise WorkerFailed(f"the worker didn't tell its engine's {e}") from e

    def read(self, page_id: str, image: Path, output: Path) -> float | None:
        """Have the worker read `image` into `output`; how long it took, if told.

        Raises `PageFailed` if it couldn't read it, `WorkerFailed` if it stopped.
        """
        self._send({"type": "page", "id": page_id, "image": str(image), "output": str(output)})
        while True:
            message = self._receive()
            if message.get("id") != page_id:
                self._log(f"unexpected message: {message!r}")
                continue
            if message.get("type") == "failed":
                raise PageFailed(str(message.get("error")))
            if message.get("type") == "done":
                seconds = message.get("seconds")
                return float(seconds) if isinstance(seconds, int | float) else None
            self._log(f"unexpected message: {message!r}")

    def orient(self, page_id: str, image: Path) -> Orientation:
        """How the page in `image` is turned, as the worker's engine tells.

        Raises `PageFailed` if it can't tell, `WorkerFailed` if it stopped.
        """
        self._send({"type": "orient", "id": page_id, "image": str(image)})
        while True:
            message = self._receive()
            if message.get("id") != page_id:
                self._log(f"unexpected message: {message!r}")
                continue
            if message.get("type") == "failed":
                raise PageFailed(str(message.get("error")))
            if message.get("type") == "oriented":
                try:
                    rotate = int(message.get("rotate") or 0)
                    confidence = float(message.get("confidence") or 0.0)
                except (TypeError, ValueError) as e:
                    raise PageFailed(f"the worker told an unreadable turn: {message!r}") from e
                if rotate not in (0, 90, 180, 270):
                    raise PageFailed(f"the worker told a turn of {rotate} degrees")
                return Orientation(rotate, confidence)
            self._log(f"unexpected message: {message!r}")

    def stop(self, *, kill: bool = False) -> int | None:
        """Tell the worker there's no more to read and wait for it (or kill it); its exit code."""
        if kill:
            self.process.kill()
        try:
            # Closes its stdin, and logs what it printed last.
            rest, _ = self.process.communicate(timeout=_STOP_SECONDS)
        except subprocess.TimeoutExpired:
            self.process.kill()
            rest, _ = self.process.communicate()
        if rest:
            self.log.write(rest)
            self.log.flush()
        return self.process.returncode

    def _send(self, message: dict[str, Any]) -> None:
        stdin = self.process.stdin
        assert stdin is not None
        try:
            stdin.write(json.dumps(message) + "\n")
            stdin.flush()
        except OSError as e:  # e.g. a broken pipe: it has stopped
            raise WorkerFailed(f"the worker stopped ({self._exit_code()}); see the log") from e

    def _receive(self) -> dict[str, Any]:
        stdout = self.process.stdout
        assert stdout is not None
        while True:
            line = stdout.readline()
            if not line:
                raise WorkerFailed(f"the worker stopped ({self._exit_code()}); see the log")
            if not line.startswith(MARK):
                self.log.write(line)
                self.log.flush()
                continue
            try:
                message = json.loads(line[len(MARK) :])
            except json.JSONDecodeError:
                self._log(f"unreadable message: {line.rstrip()}")
                continue
            if isinstance(message, dict):
                return message
            self._log(f"unexpected message: {message!r}")

    def _exit_code(self) -> str:
        try:
            code = self.process.wait(timeout=_STOP_SECONDS)
        except subprocess.TimeoutExpired:
            return "not answering"
        return f"exit code {code}"

    def _log(self, text: str) -> None:
        self.log.write(f"[scriptorium] {text}\n")
        self.log.flush()


def _optional_str(value: object) -> str | None:
    return None if value is None else str(value)


def load_result(path: Path) -> Result:
    """The result a worker wrote; raises ValueError if it's not one."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise ValueError("the worker said it read the page but wrote no result") from e
    if not isinstance(data, dict) or data.get("protocol") != PROTOCOL:
        raise ValueError(f"not a result of protocol {PROTOCOL}")
    boxes = data.get("boxes")
    extra = data.get("extra") or {}
    if not isinstance(boxes, list) or not isinstance(extra, dict):
        raise ValueError("a result needs a list of boxes")
    return Result(boxes=[_box(b) for b in boxes], extra=extra)


def _box(data: object) -> Box:
    if not isinstance(data, dict):
        raise ValueError(f"not a box: {data!r}")
    try:
        confidence = data.get("confidence")
        return Box(
            text=str(data["text"]),
            x0=float(data["x0"]),
            y0=float(data["y0"]),
            x1=float(data["x1"]),
            y1=float(data["y1"]),
            confidence=None if confidence is None else float(confidence),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError(f"not a box: {data!r}") from e
