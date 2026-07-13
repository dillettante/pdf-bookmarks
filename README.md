# pdf-bookmarks

Automatically add a table-of-contents **outline (bookmarks)** to scanned / OCR'd book PDFs — and, when needed, OCR the scan first. Built as an [Agent Skill](https://docs.claude.com/en/docs/claude-code/skills) but the core is a standalone Python CLI, so it works from a terminal too.

Optimized for the messy reality of scanned books (Korean-language books especially): multi-page/two-column contents, printed-page ≠ PDF-page offsets, rotated scans, and low-quality OCR.

## What it does

- Reads a book's **printed table of contents** and writes matching PDF bookmarks.
- Corrects the **printed-page → PDF-page offset** automatically (front matter shifts every page number).
- Sets **PDF page labels** so your viewer's page counter matches the book's printed numbers.
- Falls back gracefully when the contents page is hard to parse: font-size heading detection, or a human-readable list.
- **OCR built in** for raw scans, with a high-quality native backend per platform.

## Install

```bash
pip install pymupdf
git clone https://github.com/dillettante/pdf-bookmarks
```

As a Claude Code / agent skill, copy the folder into your skills directory (e.g. `~/.claude/skills/pdf-bookmarks`). As a plain CLI, call `scripts/pdf_toc.py` directly.

OCR (only for raw scans — skip if your PDF already has a text layer):
- **tesseract** (any OS): `brew install ocrmypdf tesseract-lang` / `apt install ocrmypdf tesseract-ocr-kor` / Windows installer.
- **macOS**: nothing to install — `--ocr vision` uses Apple Vision (needs Xcode command-line tools for `swiftc`).
- **Windows**: nothing to install — `--ocr winocr` uses the built-in OCR engine (add the **Korean OCR language pack**: Settings → Language → Korean → Optional features → Optical character recognition).

## Usage

```bash
# 1) Find which PDF page the contents is on
python3 scripts/pdf_toc.py extract book.pdf

# 2) Preview, then apply (offset auto-detected)
python3 scripts/pdf_toc.py apply book.pdf --toc-pages 5-8 --dry-run
python3 scripts/pdf_toc.py apply book.pdf --toc-pages 5-8 --page-labels
# -> writes book-marked.pdf (original untouched)
```

### Raw scan (no text layer)? OCR first
```bash
python3 scripts/pdf_toc.py extract book.pdf --ocr vision   # macOS  (or --ocr winocr on Windows, --ocr auto elsewhere)
# then bookmark book_ocr.pdf as above
```

### When the contents page won't parse
- **Two-column / dense academic TOC** → font-based heading detection:
  `apply book.pdf --by-font --font-levels "36-45:1,16-23:2" --body-start 17` (use `extract --fonts` to pick the size bands).
- **Low-quality OCR** → read the contents yourself and feed a list:
  `apply book.pdf --from-list toc.txt` where each line is `level | title | printed_page` (`=N` for an absolute PDF page).

## OCR backends

| `--ocr` | Engine | Platform | Install |
|---|---|---|---|
| `off` (default) | none | all | — (PDF already has text) |
| `auto` / `force` | tesseract (ocrmypdf) | all | ocrmypdf + tesseract-kor |
| `vision` | Apple Vision | macOS | built-in |
| `winocr` | Windows.Media.Ocr | Windows | built-in + Korean OCR pack |

`vision` and `winocr` rebuild each page as image + invisible text (embedded Korean font), producing a searchable PDF whose quality beats tesseract on faint scans. Wrong platform → a clear error pointing you to the right option, never a crash.

> **⚠️ `winocr` is beta / unverified.** It was written on macOS and has not been run on a real Windows machine yet. Please test on Windows 10/11 with the Korean OCR pack and open an issue with any error output. Until verified, Windows users can use `--ocr auto` (tesseract).

## The bookmark strategy, by book quality

| Book | Route |
|---|---|
| Native text layer, simple TOC | regex (`--toc-pages`) |
| Native text, dense/two-column TOC | `--by-font` |
| Raw scan | `--ocr vision`/`winocr`/`auto` → then one of the above, or `--from-list` |

## Notes

- Output is always a **new `-marked.pdf`** — the original is never modified. (In-place edits inside cloud-synced folders — Google Drive/Dropbox/iCloud/OneDrive — get reverted by the sync client, so a new file is used.)
- OCR (`--ocr auto`/`vision`/`winocr`) writes a searchable **`<name>_ocr.pdf`** next to the input as an intermediate (also a new file — safe in cloud folders; it's an extra artifact, not an overwrite). Bookmark that to get `<name>_ocr-marked.pdf`.
- `vision`/`winocr` re-render each page but **cap render DPI at the source scan's native DPI** (never upscale), so low-DPI scans don't balloon in size. OCR aborts (without writing output) if it finds zero text, rather than producing an empty text layer.
- The compiled `scripts/visionbox` binary is not committed; it is compiled on first `--ocr vision` use.

## License

MIT — see [LICENSE](LICENSE).
