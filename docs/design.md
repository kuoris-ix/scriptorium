# OCR design notes

English | [日本語](design.ja.md)

> The English version is in preparation; the Japanese version is complete.

How scriptorium is built, and why: how pages become images (resolution, rotation, which pages
are read, cutting apart several documents scanned at once), preprocessing (telling how a page
is turned, evening out lighting, straightening slant), running each engine as a worker in an
environment of its own, the common shape of what's read, the store and resuming a run, the
commands, how accuracy is checked (scoring against fictional documents' truth, comparing
engines on real documents), the engines and their dependencies, the settings (`OcrConfig`,
`ProgramNames`), the tests, and what comes next.
