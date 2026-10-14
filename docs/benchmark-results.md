# OCR engine comparison results

English | [日本語](benchmark-results.ja.md)

> The English version is in preparation; the Japanese version is complete. Its tables are
> mostly numbers, readable as they are.

What the engines read from fictional documents, following the plan in `benchmark.md`: Tesseract
as the baseline; every engine on Windows (on the GPU and the CPU) and on a Mac; telling how
pages are turned, and preprocessing; long runs (1,600 pages) and the workers' memory; and the
speed: where a page's time goes (rendering it against reading it) and several workers at once, on
a Linux container's CPU (4 cores), on Windows (16 cores, a GPU) and on a Mac.

In short: YomiToku's lighter models (`yomitoku-lite`) and PaddleOCR read the most amounts and
dates right (with Tesseract telling how pages are turned, 99.7% of the amounts and nearly every
date of 256 pages). `yomitoku-lite` is chosen as the main engine, and PaddleOCR reads the same
pages to cross-check it.

On speed: rendering a page and saving it as a PNG takes a tenth to a half of the time an engine
reads it, so rendering the next pages while it reads makes every engine 1.1-2 times as fast.
More workers help only some engines: Tesseract with one OpenMP thread each, nearly as many
times as physical cores (10.6 times with 16), Windows' OCR (5.6 times with 16) and Apple Vision
(5.9 times with 12, on a Mac); NDLOCR-Lite and YomiToku on the CPU read fastest with one worker
using every core, and an engine on the GPU with one. `ocr run` now does so (`--jobs auto`):
40 pages took 36.1 s before and 6.7 s after with Tesseract, 14.7 s and 5.2 s with Windows' OCR,
and 64 pages 25.8 s and 8.5 s with Apple Vision, and what's read is the same.
