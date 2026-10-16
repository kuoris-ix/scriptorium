"""What every OCR worker shares: talking with scriptorium, which starts it.

scriptorium runs each engine in an environment of its own (the folder next to
this file), as

    uv run --project workers/<worker> python workers/<worker>/worker.py

and talks with it one JSON object per line. Over stdin, scriptorium sends

    {"type": "start", "protocol": 1, "device": "auto", "options": {...}}
    {"type": "page", "id": "...", "image": "<PNG file>", "output": "<JSON file>"}  (repeated)
    {"type": "orient", "id": "...", "image": "<PNG file>"}  (to an engine that tells pages' turns)

and closes stdin when it's done. Over stdout the worker answers, each line
starting with MARK so that anything else printed there (a library's own
messages) is told apart, and only logged:

    {"type": "ready", "engine": {"name", "version", "model", "device"}}
    {"type": "done", "id": "...", "seconds": ...}  once the result is in the output file
    {"type": "oriented", "id": "...", "rotate": 0, 90, 180 or 270, "confidence": ...}
        how far the page has to be turned clockwise to be upright, and how sure that is
    {"type": "failed", "id": "...", "error": "..."}  for a page it couldn't read (or orient,
        or a message it doesn't know)
    {"type": "fatal", "error": "..."}  if it can't start; then it exits

An output file holds {"protocol": 1, "boxes": [...], "extra": {...}}: each
piece of text read as {"text", "x0", "y0", "x1", "y1", "confidence"}, in
pixels from the image's top left (confidence 0 to 1, or null), and whatever
else the engine gives, kept as it is.

A worker's worker.py only starts its engine and reads a page with it (see
`serve`); an engine that can tell how a page is turned also has `orient`.
This module runs in every worker's environment (Python 3.10 or later), so
it uses the standard library only.
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
import sys
import time
import traceback
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol, TextIO

PROTOCOL = 1
MARK = "@@scriptorium@@ "
DEVICES = ("auto", "cpu", "cuda", "mps")


@dataclass
class Box:
    """A piece of text read, in pixels from the image's top left."""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    confidence: float | None = None  # 0 to 1

    @classmethod
    def around(
        cls, text: str, points: Iterable[Sequence[float]], confidence: float | None = None
    ) -> Box:
        """The box around `points` (e.g. the four corners of slanted text)."""
        xs, ys = zip(*((float(p[0]), float(p[1])) for p in points), strict=True)
        return cls(text, min(xs), min(ys), max(xs), max(ys), confidence)


@dataclass
class Result:
    boxes: list[Box]
    extra: dict[str, Any] = field(default_factory=dict)


class Engine(Protocol):
    """An engine started, ready to read pages."""

    @property
    def name(self) -> str: ...

    @property
    def version(self) -> str: ...

    @property
    def model(self) -> str | None: ...

    @property
    def device(self) -> str: ...

    def read(self, image: Path) -> Result: ...


class Orienting(Engine, Protocol):
    """An engine that also tells how far a page is turned."""

    def orient(self, image: Path) -> tuple[int, float]:
        """How far to turn the image clockwise (0, 90, 180, 270) to have it upright, and how
        sure that is (the engine's own measure; 0 for not at all)."""
        ...


class StartError(Exception):
    """The engine can't start as asked (e.g. no GPU for the device "cuda")."""


def serve(start: Callable[[str, dict[str, Any]], Engine]) -> None:
    """Answer scriptorium until it closes stdin.

    `start(device, options)` loads the engine; it gets the device asked for
    (see `DEVICES`) and the engine's options from scriptorium's list of engines.
    """
    channel = sys.stdout
    # What the engine's libraries print goes to the log, not the channel.
    sys.stdout = sys.stderr

    def send(**message: Any) -> None:
        channel.write(MARK + json.dumps(message) + "\n")
        channel.flush()

    first = _receive(sys.stdin)
    if first is None or first.get("type") != "start":
        send(type="fatal", error=f"expected a start message, got {first!r}")
        sys.exit(2)
    if first.get("protocol") != PROTOCOL:
        send(
            type="fatal",
            error=f"scriptorium speaks protocol {first.get('protocol')!r}; "
            f"this worker speaks {PROTOCOL}",
        )
        sys.exit(2)
    try:
        engine = start(str(first.get("device") or "auto"), dict(first.get("options") or {}))
    except Exception as e:
        traceback.print_exc()
        send(type="fatal", error=_describe(e))
        sys.exit(1)
    send(
        type="ready",
        engine={
            "name": engine.name,
            "version": engine.version,
            "model": engine.model,
            "device": engine.device,
        },
    )
    while (message := _receive(sys.stdin)) is not None:
        page_id = message.get("id")
        if message.get("type") == "orient":
            _orient(engine, message, send)
            continue
        if message.get("type") != "page":
            send(type="failed", id=page_id, error=f"unknown message {message.get('type')!r}")
            continue
        started = time.perf_counter()
        try:
            result = engine.read(Path(message["image"]))
            seconds = round(time.perf_counter() - started, 3)
            write_json(
                Path(message["output"]),
                {
                    "protocol": PROTOCOL,
                    "boxes": [asdict(box) for box in result.boxes],
                    "extra": result.extra,
                },
            )
        except Exception as e:
            traceback.print_exc()
            send(type="failed", id=page_id, error=_describe(e))
        else:
            send(type="done", id=page_id, seconds=seconds)


def _orient(engine: Engine, message: dict[str, Any], send: Callable[..., None]) -> None:
    page_id = message.get("id")
    orient = getattr(engine, "orient", None)
    if orient is None:
        send(type="failed", id=page_id, error=f"{engine.name} doesn't tell how pages are turned")
        return
    try:
        rotate, confidence = orient(Path(message["image"]))
    except Exception as e:
        traceback.print_exc()
        send(type="failed", id=page_id, error=_describe(e))
    else:
        send(type="oriented", id=page_id, rotate=int(rotate), confidence=float(confidence))


def _receive(stream: TextIO) -> dict[str, Any] | None:
    """The next message, or None once the stream is closed."""
    for line in stream:
        if line.strip():
            message = json.loads(line)
            if not isinstance(message, dict):
                raise ValueError(f"not a message: {line!r}")
            return message
    return None


def _describe(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"


def write_json(path: Path, data: Any) -> None:
    """Write `data` to `path` whole or not at all (scriptorium may read it any time after)."""
    part = path.with_name(path.name + ".part")
    part.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    part.replace(path)


def pick_device(requested: str, available: Sequence[str]) -> str:
    """The device to use: `requested`, or for "auto" the first of `available` (best first)."""
    if requested not in DEVICES:
        raise StartError(f"unknown device {requested!r} (use one of {', '.join(DEVICES)})")
    if requested == "auto":
        return available[0] if available else "cpu"
    if requested not in available:
        raise StartError(
            f"the device {requested!r} isn't available here (available: {', '.join(available)})"
        )
    return requested


def find_command(name: str, platform: str = sys.platform) -> str | None:
    """Where the program `name` is on the PATH, if it is. On Windows, never in the working
    folder, which `shutil.which` looks in first: a tesseract.bat left in a folder of scans,
    say, would be run."""
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


def torch_devices() -> list[str]:
    """The devices PyTorch can use here, best first."""
    torch: Any = importlib.import_module("torch")
    devices = []
    if torch.cuda.is_available():
        devices.append("cuda")
    if torch.backends.mps.is_available():
        devices.append("mps")
    return [*devices, "cpu"]
