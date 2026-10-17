# OCR engine comparison results

English | [日本語](benchmark-results.ja.md)

> The English version is in preparation; the Japanese version is complete. Its tables are
> mostly numbers, readable as they are.

What the engines read from fictional documents, following the method in `benchmark.md`: Tesseract
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
Measured again on `master` before v0.3.0 (Windows, Ryzen 9 5950X, RTX 2080 SUPER): the 256
pages are read exactly as before, and Tesseract and Windows' OCR keep their speed-up.
On a Mac (Apple M4 Pro, 14 cores) the same pages are read as before too, except that Tesseract
differs by a line or two as it did earlier; 40 pages take 4.4 s with Tesseract (11 workers, 25.0 s
with one) and 64 pages 8.5 s with Apple Vision (26.4 s with one), and Apple Vision reads the same
words with one worker and with eight.
On a Linux virtual machine (VMware Workstation Pro 26H1, Ubuntu 24.04, CPU only, on the same Ryzen 9
5950X) all five Linux engines work, PaddleOCR included. Its 64 documents are drawn in another
font than the Windows and Mac ones, so their accuracy isn't compared directly (read there,
the Windows documents give NDLOCR-Lite the same numbers as on Windows and a Mac). Tesseract's speed-up follows the vCPUs
(40 pages: 34.0 s with one worker, and 6.9 s with 8 workers on 8 vCPUs, 4.7 s with 16 on 16,
12.2 s with 4 on 4), while NDLOCR-Lite reads fastest with one worker; `--jobs auto` chose 3, 6 and
12 workers for 4, 8 and 16 vCPUs.
