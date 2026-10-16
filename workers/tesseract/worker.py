"""scriptorium's OCR worker for Tesseract, run as the `tesseract` command.

Tesseract itself is installed apart, with its Japanese data (jpn):

- macOS: `brew install tesseract tesseract-lang`
- Ubuntu: `sudo apt install tesseract-ocr tesseract-ocr-jpn`
- Windows: UB Mannheim's installer, choosing Japanese among the languages

The command is found on the PATH (on Windows, not in the working
folder), at Windows' usual place, or at TESSERACT_CMD. Each word
Tesseract reads is a box (Japanese words are often a character or two);
its block, paragraph and line are kept in the extra output. Tesseract
reads the image's resolution from the PNG.

It also tells how a page is turned (`ocr run --orient tesseract`),
with Tesseract's orientation and script detection (OSD, its "osd" data);
a page with too little text for it is taken to be upright, with a
confidence of 0.

Options (scriptorium's list of engines): languages (default "jpn+eng"),
psm (page segmentation mode, default 3: automatic).

Tesseract is run with one OpenMP thread (OMP_THREAD_LIMIT=1, unless it's set
otherwise): more don't read a page faster, and two Tesseracts at once (two
runs) each with a thread for every core keep taking the cores from each
other, reading tens of times slower.
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ocr_worker

_WINDOWS_PLACE = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")


def _command() -> str:
    found = os.environ.get("TESSERACT_CMD") or ocr_worker.find_command("tesseract")
    if found:
        return found
    if sys.platform == "win32" and _WINDOWS_PLACE.exists():
        return str(_WINDOWS_PLACE)
    raise ocr_worker.StartError(
        "the tesseract command isn't found (install Tesseract, or set TESSERACT_CMD)"
    )


def _run(args: list[str]) -> str:
    done = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", check=False)
    if done.returncode != 0:
        raise RuntimeError(f"tesseract failed ({done.returncode}): {done.stderr.strip()}")
    return done.stdout


class _TooLittleText(Exception):
    pass


def _osd(command: str, image: Path) -> dict[str, str]:
    """What Tesseract's OSD tells of `image` ("Rotate", "Orientation confidence", ...)."""
    args = [command, str(image), "stdout", "--psm", "0", "-l", "osd"]
    done = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", check=False)
    if done.returncode != 0:
        if "Too few characters" in done.stdout + done.stderr:
            raise _TooLittleText
        raise RuntimeError(f"tesseract failed ({done.returncode}): {done.stderr.strip()}")
    pairs = (line.split(":", 1) for line in done.stdout.splitlines() if ":" in line)
    return {key.strip(): value.strip() for key, value in pairs}


class Tesseract:
    name = "tesseract"

    def __init__(self, device: str, options: dict[str, Any]) -> None:
        self.device = ocr_worker.pick_device(device, ["cpu"])
        os.environ.setdefault("OMP_THREAD_LIMIT", "1")  # for the tesseract commands; see above
        self.command = _command()
        self.languages = str(options.get("languages", "jpn+eng"))
        self.psm = int(options.get("psm", 3))
        self.version = _run([self.command, "--version"]).splitlines()[0].removeprefix("tesseract ")
        installed = set(_run([self.command, "--list-langs"]).split()[1:])
        missing = [lang for lang in self.languages.split("+") if lang not in installed]
        if missing:
            raise ocr_worker.StartError(
                f"Tesseract has no data for {', '.join(missing)} (installed: {sorted(installed)})"
            )
        self.model = f"{self.languages}, psm {self.psm}"
        self.has_osd = "osd" in installed

    def read(self, image: Path) -> ocr_worker.Result:
        tsv = _run(
            [
                self.command,
                str(image),
                "stdout",
                "-l",
                self.languages,
                "--psm",
                str(self.psm),
                "tsv",
            ]
        )
        boxes, lines = [], []
        rows = [line.split("\t") for line in tsv.splitlines()]
        header = rows[0]
        for values in rows[1:]:
            row = dict(zip(header, values, strict=False))  # the text may be missing
            text = row.get("text", "").strip()
            if row.get("level") != "5" or not text:
                continue
            left, top = float(row["left"]), float(row["top"])
            confidence = float(row["conf"])
            boxes.append(
                ocr_worker.Box(
                    text=text,
                    x0=left,
                    y0=top,
                    x1=left + float(row["width"]),
                    y1=top + float(row["height"]),
                    confidence=None if confidence < 0 else round(confidence / 100, 4),
                )
            )
            lines.append([int(row["block_num"]), int(row["par_num"]), int(row["line_num"])])
        # The block, paragraph and line of each box, in the same order.
        return ocr_worker.Result(boxes, {"lines": lines})

    def orient(self, image: Path) -> tuple[int, float]:
        if not self.has_osd:
            raise RuntimeError("Tesseract has no osd data (it tells how pages are turned)")
        try:
            found = _osd(self.command, image)
        except _TooLittleText:
            return 0, 0.0
        return int(found["Rotate"]), float(found["Orientation confidence"])


if __name__ == "__main__":
    ocr_worker.serve(Tesseract)
