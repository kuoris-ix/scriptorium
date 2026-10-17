# Installing and checking the OCR engines

English | [日本語](engine-setup.ja.md)

How to install each engine's worker (`workers/`) on a Mac, Windows or Linux machine, have it
read fictional documents with `scriptorium run`, and see whether it installs and how it reads.

On Windows (6 engines) and a Mac (the same but Windows OCR), these steps were run and what
didn't work was fixed (2026-10; the results are in `benchmark-results.md`). Linux
(Ubuntu, CPU only) was checked the same way. If something doesn't work on a new machine or a new version, the error
and the log, pasted as they are into an issue, are what's needed to fix it.

The commands are run in the clone, with the `scriptorium` command (`uv run scriptorium`, the
README's "Trying it out"). A program that has added scriptorium's `ocr` commands has the same
ones (`myapp ocr run` and so on), with its own settings.

## 0. Before you start

### Rules

- **No real documents.** Have the engines read only the fictional documents made below.
- What's read is kept in `data/ocr/` of the clone (`SCRIPTORIUM_DIR`; `data/` is kept out of
  Git).

### Getting scriptorium

The workers run in `workers/` of a clone of this repository, one per machine (each program
using scriptorium points its `OcrConfig.workers_dir` there). Run the commands below in the clone.
On Windows, unless long paths are enabled, clone it into a folder of at most 130 characters or
so: what's read is kept at paths some 120 characters longer than `data/ocr/` (`run` tells when
they would be too long, before reading).

```bash
git clone https://github.com/kuoris-ix/scriptorium
cd scriptorium
uv sync
uv run scriptorium list-engines
```

`scriptorium list-engines` tells, for each engine, whether it can be used here (`runs on macOS only`
and the like means it can't on this system).

### Fictional documents

**Start with generated ones** (they have their truth, so `scriptorium score-engines` gives numbers):

```bash
uv run scriptorium make-samples data/ocr-trial --count 1
```

This makes scriptorium's fictional documents (receipts, card statements, invoices and passbook
pages), degraded 16 ways
(low resolution, slant, turned, JPEG, noise, shadow, PDF and so on): 64 pages. A heavy engine
on the CPU takes a while, so try a folder of a few first (say, only `*-300.png` and
`*-turned90.png` copied to another folder), then read them all.

**If you have time, try printed ones too, scanned or photographed** (closer to the real thing
than generated ones). Keep every value fictional (no amounts, dates, shops or names copied from
real ones or from your own documents).

1. Print a generated document from `data/ocr-trial` (e.g. `invoice-1-300.png`).
2. Put these in `data/ocr-trial/printed/`:
   - PDFs of it scanned at 300 dpi and 150 dpi (with a scanner, or a phone's scanning app)
   - a photo of it taken a little from the side (jpg)
3. The generated truth (`invoice-1-300.png.truth.json`) doesn't match the printed one's
   positions, so compare by eye.

## 1. Installing each engine and reading

### All at once

Once the engines' environments are made (`uv sync` in each engine's steps below), first see
that they work. This starts each installed engine in turn, has it read one fictional document,
and shows its version, device, times, how many amounts it read right, and whether it can tell
how pages are turned (an engine that doesn't work shows `FAILED` and where its log is):

```bash
uv run scriptorium check-engines
```

If they work, read with every installed engine and make a table comparing them. It takes a
while, so leave it running (see "5. Reading in the background" below).

```bash
uv run scriptorium run data/ocr-trial -e all                 # read with every installed engine in turn
uv run scriptorium score-engines data/ocr-trial --markdown   # the table comparing them (Markdown)
uv run scriptorium run data/ocr-trial -e all --orient tesseract --preprocess flatten,deskew
uv run scriptorium score-engines data/ocr-trial --orient tesseract --preprocess flatten,deskew --markdown
```

`-e all` uses only the engines that run on this system and whose environment is made (the
others are skipped, with why). The `--markdown` table can be pasted as it is
into the results (`docs/benchmark-results.ja.md`, and the English one once it's translated).

### One at a time

Every engine takes these three steps. `<worker>` is the folder's name under `workers/`,
`<engine>` the name `scriptorium list-engines` shows.

```bash
uv sync --project workers/<worker>                            # make the worker's environment (slow the first time)
uv run scriptorium run data/ocr-trial -e <engine>             # read (slow the first time: models are fetched)
uv run scriptorium score-engines data/ocr-trial -e <engine>   # score against the truth
```

- `scriptorium run` first shows the engine's version, model and device (cpu, cuda or mps), then for
  each page how many pieces of text it read and how long it took, and finally where the log
  file is (the details of any error are there).
- To see the text read and where: `uv run scriptorium dump-text data/ocr-trial -e <engine> -o data/ocr-trial/<engine>.csv`
- **To compare devices**, add `--reread` to `--device cpu` (or `cuda`, `mps`). The device isn't
  in the store's key, so without it nothing is read again (and `run` tells how many pages were
  read on another device, and that `--reread` reads them again).
- **Turned pages**: the generated documents include pages turned 90 and 180 degrees
  (`turned90`, `turned180`). Once Tesseract is installed, also read with `--orient tesseract`
  (each page's turn is told, and the page put upright before it's read), and score with
  `--orient tesseract` too (it's kept apart from what was read without it).
- **Preprocessing**: on documents with uneven lighting (`shadow`, `photo`) or slanted (`slant`),
  also try `--preprocess flatten,deskew`, and score with the same.
- A page that failed is read again by running `scriptorium run` again. `uv run scriptorium status data/ocr-trial`
  shows which pages each engine has read.
- If `uv sync` changes `workers/<worker>/uv.lock`, commit that change too.

### Tesseract (Mac, Windows, Linux)

Install Tesseract itself first:

- Mac: `brew install tesseract tesseract-lang`
- Linux (Ubuntu and the like): `sudo apt install tesseract-ocr tesseract-ocr-jpn`
- Windows: install with UB Mannheim's installer, choosing the Japanese language data on the
  way. Installed in `C:\Program Files\Tesseract-OCR\`, it's found without being on the PATH
  (elsewhere, set the environment variable `TESSERACT_CMD` to where tesseract.exe is).

```bash
uv sync --project workers/tesseract
uv run scriptorium run data/ocr-trial -e tesseract
```

### YomiToku (Mac, Windows, Linux)

```bash
uv sync --project workers/yomitoku
uv run scriptorium run data/ocr-trial -e yomitoku
uv run scriptorium run data/ocr-trial -e yomitoku-lite
```

- The first time, the models are fetched from Hugging Face.
- **Windows**: PyTorch built for CUDA 13.0 is installed: it needs an NVIDIA GPU that CUDA 13
  supports and a driver for it (580 or later). Then check that the GPU can be used:

  ```powershell
  uv run --project workers/yomitoku python -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))"
  ```

  `True` and the GPU's name mean it can. If it says `False`, or `uv sync` says PyTorch isn't
  found, change `cu130` in `workers/yomitoku/pyproject.toml` to a CUDA version your GPU and
  driver support that PyTorch's site offers (https://pytorch.org/get-started/locally/; `cu128`,
  `cu126` and so on), and `uv sync` again. An older GPU (CUDA 13 leaves out the Maxwell, Pascal
  and Volta ones) needs CUDA 12; its builds stop at PyTorch 2.11.
- **Mac**: `auto` uses MPS. If that fails, read with `--device cpu --reread` too, and paste both
  results.
- Check that the device on `scriptorium run`'s first line is `cuda` (Windows) or `mps` (Mac).

### PaddleOCR (Mac, Windows, Linux)

```bash
uv sync --project workers/paddleocr
uv run scriptorium run data/ocr-trial -e paddleocr
```

- The first time, the models are fetched from PaddlePaddle's servers.
- **Windows**: PaddlePaddle's GPU build (`paddlepaddle-gpu`) is installed from PaddlePaddle's
  own index (`cu126`). If `uv sync` says it isn't found, look up the URL for Windows, pip and
  CUDA on PaddlePaddle's installation page, and fix the `url` in
  `workers/paddleocr/pyproject.toml`.
  - The Windows GPU build brings no CUDA libraries (cuDNN and so on), so the ones NVIDIA
    publishes on PyPI (`nvidia-cudnn-cu12` and others) are installed in the worker's
    environment too, and the worker has them found first when it starts. CUDA needn't be
    installed apart (if it is, these are used instead). If `cudnn64_9.dll` or the like isn't
    found, run `uv sync` again.
- On a Mac and Linux, the CPU build.

### NDLOCR-Lite (Mac, Windows, Linux)

Git is needed (uv fetches it from its GitHub repository). The version is pinned to 1.2.3 (from
1.3.0 on, `pdf_output_utils` and others are missing from the package, and the installed command
stops with `ModuleNotFoundError`). NDLOCR-Lite pins its own dependencies to exact versions; the
worker raises those with known vulnerabilities to fixed versions (`override-dependencies` in its
`pyproject.toml`).

```bash
uv sync --project workers/ndlocr_lite
uv run scriptorium run data/ocr-trial -e ndlocr-lite
```

- If it can't start with "No module named 'ocr'", NDLOCR-Lite's module has another name (the
  worker imports the `ocr` module behind the `ndlocr-lite` command). Paste the contents of
  `ndlocr_lite-*.dist-info/entry_points.txt` in `workers/ndlocr_lite/.venv/lib/python3.*/site-packages/`
  (on Windows, `.venv\Lib\site-packages\`).

### Apple Vision (Mac only)

```bash
uv sync --project workers/apple_vision
uv run scriptorium run data/ocr-trial -e apple-vision
```

- If it says Japanese (`ja-JP`) can't be used, paste the list of languages in the error.
- Check that the boxes aren't off the text (not upside down) by comparing the dump's
  coordinates with the document.

### Windows OCR (Windows only)

Check that the Japanese OCR component is installed (it usually is on a Japanese Windows):

```powershell
Get-WindowsCapability -Online | Where-Object Name -like 'Language.OCR*ja-JP*'
```

`State` should be `Installed`.

```powershell
uv sync --project workers/windows_ocr
uv run scriptorium run data/ocr-trial -e windows-ocr
```

- An A4 page at 600 dpi may be too large to read (Windows OCR has a limit on pixels a side). If
  it says so, try with a lower maximum resolution (`SCRIPTORIUM_MAX_DPI`), 400 or so (the store's
  key changes, so the pages are read again).

## 2. What to look at

For each engine and device:

- **Did it install and start**: errors from `uv sync` or `scriptorium run` (and the log file's contents)
- **Version and device**: `scriptorium run`'s first line
- **Time**: about how long a page takes (leaving out the first fetch and load of models)
- **How well it read**: `scriptorium score-engines`' results (the share of amounts and dates read right,
  the table by degradation, the amounts and dates read wrong). For printed ones, compare the
  dump's CSV with the document.
- **Pieces of text**: whole lines, or words (1 or 2 characters) (see the dump's CSV)

## 3. Reporting

For each engine and machine, open an issue with the "An engine on your machine" form, and paste
these into it. They're results of fictional documents, so the text read may be in them as it
is; replace your user name in the paths they show (say, with `<user>`).

- `scriptorium run`'s first line (version, model, device) and last line
- `scriptorium check-engines`' output
- `scriptorium score-engines --markdown`'s output (without and with preprocessing)
- Where you got stuck (errors from `uv sync` or `scriptorium run`, the log file's contents)
- If you tried printed ones, what you noticed

## 4. Cleaning up

- What was read stays in `data/ocr/`; delete it if you don't need it.
- The models fetched the first time are kept elsewhere (Hugging Face's cache for YomiToku,
  `.paddlex` in your home folder for PaddleOCR, and so on); delete them if they take too much
  space.
- A worker's environment is `workers/<worker>/.venv` (not in Git); deleted, the next `uv sync`
  makes it again.

## 5. Reading in the background

When it takes long, leave it to the system. Stopped, it goes on where it was when run again.
Make the folder the progress is written to (`data/ocr/` below) first.

- macOS and Linux:

  ```bash
  nohup uv run scriptorium run data/scan -e yomitoku > data/ocr/run.txt 2>&1 &
  ```

- Windows (PowerShell):

  ```powershell
  Start-Process uv -ArgumentList 'run','scriptorium','run','data/scan','-e','yomitoku' -WindowStyle Hidden -RedirectStandardOutput data/ocr/run.txt
  ```

The progress is written to `data/ocr/run.txt`.
