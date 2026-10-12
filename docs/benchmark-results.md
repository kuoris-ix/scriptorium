# OCR engine comparison results

English | [日本語](benchmark-results.ja.md)

> The English version is in preparation; the Japanese version is complete. Its tables are
> mostly numbers, readable as they are.

What the engines read from fictional documents, following the plan in `benchmark.md`: Tesseract
as the baseline; every engine on Windows (on the GPU and the CPU) and on a Mac; telling how
pages are turned, and preprocessing; long runs (1,600 pages) and the workers' memory.

In short: YomiToku's lighter models (`yomitoku-lite`) and PaddleOCR read the most amounts and
dates right (with Tesseract telling how pages are turned, 99.7% of the amounts and nearly every
date of 256 pages). `yomitoku-lite` is chosen as the main engine, and PaddleOCR reads the same
pages to cross-check it.
