# OCR specification (talking with workers, the store's format, the store's keys)

English | [日本語](spec.ja.md)

The parts of scriptorium (`src/scriptorium`, `workers/`) that, **if changed, have what was read
before read again, or stop older workers from being talked with**. The thinking behind the
design is in `design.md` (in Japanese for now), how to use it in the README. What's written here is
how the code behaves now; where they differ, the code is right (and this document is fixed).

A change to anything described here follows the rules of §5.

## 1. Talking with a worker (PROTOCOL 1)

An engine runs in an environment and a process of its own, apart from the program using
scriptorium (a worker). scriptorium's side is `worker.py`; what every worker shares is
`workers/ocr_worker.py`.

### Starting

```text
uv run --project workers/<worker> python workers/<worker>/worker.py
```

- `uv` is the environment variable `UV`, else found on the PATH.
- The variables pointing at the using program's environment (`VIRTUAL_ENV`,
  `UV_PROJECT_ENVIRONMENT`, `PYTHONHOME`, `PYTHONPATH`) aren't passed on. `PYTHONIOENCODING=utf-8`
  is set (so that Japanese can be logged on Windows too).
- The worker's stderr goes as it is to the log (`<ocr_dir>/logs/<date and time>-<process ID>-<engine>.log`).

### scriptorium → worker (stdin, one JSON object a line)

| Type | Form | When |
|---|---|---|
| `start` | `{"type": "start", "protocol": 1, "device": "auto", "options": {...}}` | Once, first. `device` is `auto`, `cpu`, `cuda` or `mps`; `options` are the engine's in the list of engines (`engines.py`) |
| `page` | `{"type": "page", "id": "...", "image": "<PNG>", "output": "<JSON>"}` | For each page |
| `orient` | `{"type": "orient", "id": "...", "image": "<PNG>"}` | To an engine telling how pages are turned (`--orient`) |

The worker ends when stdin is closed. Images are PNGs drawn by scriptorium (with their
resolution, named `page-<page>.png`).

### worker → scriptorium (stdout)

Only the lines starting with the mark `@@scriptorium@@ ` (ending in one space) are answers; any
other line (printed by an engine's library) is only logged.

| Type | Form |
|---|---|
| `ready` | `{"type": "ready", "engine": {"name", "version", "model", "device"}}` (the answer to `start`) |
| `done` | `{"type": "done", "id": "...", "seconds": ...}` (the result is written to `output`) |
| `oriented` | `{"type": "oriented", "id": "...", "rotate": 0/90/180/270, "confidence": ...}` (how far to turn the page clockwise, and how sure the engine is) |
| `failed` | `{"type": "failed", "id": "...", "error": "..."}` (the page couldn't be read, or the type is unknown) |
| `fatal` | `{"type": "fatal", "error": "..."}` (it can't start; the worker ends after this) |

If `start`'s `protocol` differs, the worker ends with `fatal`.

### The result file (`page`'s `output`)

```json
{"protocol": 1, "boxes": [{"text": "...", "x0": 0, "y0": 0, "x1": 0, "y1": 0, "confidence": 0.9}], "extra": {}}
```

- Positions are **pixels from the image's top left**. `confidence` is 0 to 1, or `null` if the
  engine gives none.
- `extra` is the engine's own (kept as it is).
- It's written whole or not at all (written to `<output>.part`, then renamed).

### Failures and restarts (scriptorium's side)

- A `failed` page is kept as a failure (`page-NNNN.failed.json` of §2), and read again by the
  next `ocr run`.
- If a worker ends midway, the page it was on is a failure, and it's started again for the
  rest. After 3 ends in a row, that engine is given up.
- It's started again every `OcrConfig.worker_pages` pages (1,000 by default).

## 2. How the store is laid out

```text
<ocr_dir>/                              `OcrConfig.ocr_dir`. Holds the text of the documents read
    <the file's SHA-256>/
        source.json                     the file's name, its pages, absolute paths it was read at
        <engine>-<RunKey's hash>/       one for each key (the slug of §3)
            run.json                    the key (RunKey.as_dict)
            page-0001.json              a page read
            page-0002.failed.json       a page that couldn't be read, and why
        orient-<engine>.json            how each page is turned (by each engine that told)
        orient-<engine>.lock            held while that file is rewritten
    logs/<date and time>-<process ID>-<engine>.log   the workers' logs
    tmp/                                images for the workers (a stopped run's may be left)
```

- A file is known by its contents (SHA-256): renamed or moved, it isn't read again.
- Every file is written whole (written to `<name>.<8 random hex digits>.part`, then renamed).
  UTF-8, no `ensure_ascii`, indent 1.
- Several runs may keep pages in the same store at once: each writes through a temporary file
  of its own, `orient-<engine>.json` is rewritten holding `orient-<engine>.lock` (locked with
  the system's file locks), and on Windows a file another process has open is tried again for
  up to 10 seconds. An `orient-<engine>.json` that can't be read is told again, page by page.

| File | Contents |
|---|---|
| `source.json` | `{"name": the name when last read, "pages": [{"page", "width", "height", "dpi", "reason"}], "paths": [absolute paths]}`. `pages` are `raster.PagePlan`s (`reason` is `scanned`, `image file`, `text layer` and so on) |
| `run.json` | `RunKey.as_dict()` of §3 |
| `page-NNNN.json` | `{"page", "width", "height", "dpi", "words": [{"text", "x0", "x1", "top", "bottom", "confidence"}], "engine": {"name", "version", "model", "device"}, "seconds", "extra"}`. Positions are **points from the page's top left** (as a PDF's words are; pixels times `page width / image width`); `dpi` is the resolution of the image the engine read |
| `page-NNNN.failed.json` | `{"page", "error", "engine"}` (`engine` as above if it started, else `null`) |
| `orient-<engine>.json` | `{"<page>": {"rotate": 0/90/180/270, "confidence": ...}}` |

## 3. The store's key (RunKey)

What a page read depends on. If any of it changes, the page is read again as another result
(the earlier one is kept too).

| Item | Contents |
|---|---|
| `engine` | The engine's name |
| `options` | The engine's options in the list of engines (e.g. `{"lite": true}` for yomitoku-lite) |
| `revision` | The engine's revision (`Engine.revision`) |
| `min_dpi`, `max_dpi` | The range of resolutions scanned pages are drawn again at |
| `protocol` | The protocol's version (`PROTOCOL`) |
| `orient`, `orient_min_confidence` | The engine that told how pages are turned, and how sure it had to be to turn one. **Left out when no engine tells** |
| `preprocess`, `preprocess_revision` | The preprocessing steps in order, and their revision (`preprocess.REVISION`). **Left out without preprocessing** |

```python
slug = f"{engine}-{sha256(json.dumps(as_dict, sort_keys=True, ensure_ascii=True)).hexdigest()[:12]}"
```

- An item not used is left out altogether, so that the key is the same as before the item was
  added (what was read before is still found).
- **Not in the key**: the engine's version (known only once the worker has started; kept with
  each page as `engine.version`), the device (results on the GPU and the CPU have the same key;
  to keep them apart, use another `ocr_dir`), and the worker's project name.
- The slugs of every engine, plain, with orientation, and with orientation and
  preprocessing, are pinned by a test (`KEPT_IN` in `tests/unit/test_store.py`).

## 4. Truth files (fictional documents)

`ocr make-samples` writes `<file name>.truth.json` next to each document's file:

```json
{"file": "receipt-1-300.png", "width": 226.77, "height": 405.35,
 "lines": [{"text": "¥1,234", "kind": "amount", "x0": 0, "x1": 0, "top": 0, "bottom": 0}]}
```

- Sizes and positions are in points (from the page's top left). `kind` is `amount`, `date`,
  `number` or `text`.
- The same `--count` and `--seed` make the same images and truths (byte for byte; a PDF differs
  each time). What was read from fictional documents is kept by their contents, so if they're
  made again alike, they aren't read again. When how documents are made changes, check that
  existing documents come out byte for byte the same (otherwise every fictional document's
  results are read again).

## 5. Rules for changing them

| What changes | What happens | What to do |
|---|---|---|
| How an engine reads (how a worker's output is converted, the engine's settings) | Mixed up with earlier results | Raise the engine's `revision` (every page of that engine is read again) |
| An engine's options | The key changes | Only when meant to. Rewrite `KEPT_IN` |
| How preprocessing works (what `flatten` and `deskew` do) | Mixed up with earlier results | Raise `preprocess.REVISION` (every preprocessed page is read again) |
| The messages (§1) | Older workers can't be talked with | Raise `PROTOCOL`, and change scriptorium's side and every worker together (every page of every engine is read again). If it can be avoided, only add items and still accept the old form |
| The mark (`@@scriptorium@@ `) | Older workers can't be talked with (the keys don't change) | Change scriptorium's side and every worker together. Don't raise `PROTOCOL` |
| How keys are written (`as_dict`, `slug`) | Every page of every engine is read again | Avoid it. An added item is left out when not used. `KEPT_IN` guards this |
| The store's files (`page-NNNN.json` and the others) | What was kept before can't be read | Have reading (`store._stored_page` and so on) read the old form too |
| A worker's project name (`name` in `pyproject.toml`) | Nothing | — |

A change of the keys rewrites `KEPT_IN` with the new values, and says in its pull request that
pages will be read again.
