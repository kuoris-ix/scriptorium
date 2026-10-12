"""Reading the text of scanned pages (images, and PDFs of them) with OCR engines.

The engines run in environments of their own, outside the program's (see
`engines` and the `workers` folder), and what they read is kept in a folder
the program gives (see `store`); whatever uses it later only reads what's
kept there. The program adds the `ocr` commands (`commands.make_app`) to its
own. See docs/design.ja.md.
"""
