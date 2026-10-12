# Contributing

Thanks for your interest. scriptorium is a small project maintained in spare time: issues and
pull requests are welcome, but may take a while to get an answer.

## Getting started

```sh
uv sync
uv run ruff check && uv run ruff format --check && uv run mypy && uv run pytest
```

All four must pass before a change is merged; CI runs them on Linux, Windows and macOS. The
tests need no OCR engine: they use a fake worker (`tests/ocr_fake_worker.py`) and fictional
documents. To try real engines, see `docs/engine-setup.md`.

## Language

Write the README, code, comments, commit messages, issues and pull requests in English. The
commands' help is in Japanese for now, and translated bit by bit, when a part is changed
anyway.

The README and every document in `docs/` come in two languages, `<name>.md` in English and
`<name>.ja.md` in Japanese, each starting with links to switch between them, and the two are kept saying the same: a
change changes both, in the same pull request (reviewers check it). Where the English one isn't
written yet (most of `docs/`), `<name>.md` says it's in preparation and what the document
covers; links in English documents point to it all the same.

## Pull requests

- `master` is protected: every change comes through a pull request, with CI passing.
- Name the branch `<kind>/<topic>`, kind one of `feat`, `fix`, `docs`, `refactor`, `test`, `ci`,
  `chore`.
- A commit message starts with a summary line in the imperative ("Add ...", "Fix ..."), then a
  blank line and what changed and why. A pull request's title reads like a summary line, and
  its description says what changed and how it was checked.

## Releases

- Versions are `0.MINOR.PATCH` for now. A release sets `version` in `pyproject.toml`, is tagged
  `vX.Y.Z` on `master`, and has its notes in a GitHub Release, in English only.
- The projects using scriptorium depend on a tag. Breaking changes (a command or option renamed
  or removed, `OcrConfig` or `ProgramNames` changed, an import moved) raise MINOR and are listed in the
  release notes.

## Keeping what's been read

What the users of scriptorium have read can take hours to read again. A change to any of these
needs a good reason, and says in its pull request that pages will be read again:

- the store's format and `RunKey` (`store.py`): a change has every page read again.
  `tests/unit/test_store.py` pins the keys;
- `PROTOCOL` (`worker.py`, `workers/ocr_worker.py`): raised only with every worker changed
  alike, and it has every page read again too. `MARK` may change without it.

`docs/spec.md` has the details, and the rules for changing them.

## Test data and privacy

scriptorium reads personal documents (payslips, tax forms, receipts), so:

- Tests and sample documents use fictional values only: made-up names, shops and companies
  (no real banks, employers or people), random amounts and dates.
- Never put scans, text read from real documents, personal information, paths of your machine
  or its specs in the repository, an issue or a pull request. When reporting a misreading,
  reproduce it with a fictional document (`ocr make-samples`, or one you draw) if you can.

## Workers and dependencies

- The workers (`workers/`) run in their engines' environments on Python 3.10 or later: keep them
  to what 3.10 understands (ruff checks them with `target-version = "py310"`), and
  `workers/ocr_worker.py` to the standard library. mypy doesn't check the workers (it would
  need their engines' libraries).
- A new engine is a worker's folder (`pyproject.toml`, `uv.lock`, `worker.py`) and a line in
  `engines.py`; it's never a dependency of the package itself.
- Keep the package's own dependencies few and permissively licensed (scriptorium is MIT): no
  copyleft libraries (GPL, AGPL, such as PyMuPDF).
