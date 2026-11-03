# Text files

Plain-text versions of Pierre Grandchamp's *La France en Tunisie*, one folder per volume,
made from the scans in `pdfs/`. They are the source of the database: `scripts/build_db.py`
reads `acts.txt`, so a correction made here (through `fixes.json`, see below) reaches the
database and the CSVs on the next build. All files are UTF-8.

## Per volume (`text/vol<N>/`)

| File | Contents |
|---|---|
| `pages/p<NNN>.txt` | The text of printed page NNN, laid out as on the page (margin dates at the left). `fm<NN>.txt` are front-matter pages (title, foreword), `plate-*.txt` the facsimile-signature plates. `pages/index.json` gives the PDF file and page of each. |
| `acts.txt` | The analysis of the acts (Part I of the book), one block per act, with markers (below). |
| `documents.txt` | Documents printed in full: annexes (volumes 2, 3, 5), correspondence (volume 7), correspondence, sea reports and other documents (volume 8), Parts II–III (volume 1). |
| `index_names.txt` | The book's alphabetical table of names. The two printed columns are un-interleaved: each page gives its left column, then its right column. |
| `index_slaves.txt`, `index_ships.txt`, `glossary.txt` | Tables of slaves redeemed by the Redemptions, of ship names, and (volume 3) of rare words. |
| `contents.txt` | Table of contents and table of signatures. |
| `corrections.txt` | The book's errata. Volume 2's was transcribed by hand (the OCR garbled the table); `[applied]` marks the corrections made in the act texts. |
| `front_matter.txt` | Title pages, foreword, bibliography. |
| `plates.txt` | OCR of the captions of the signature plates. |
| `fixes.json` | Hand corrections, checked against the page images: `pages` replaces text on a page before parsing, `acts` edits parsed acts (found by a snippet of their text): `replace`, `split`, `merge_with_previous`, `drop`, `date`. |
| `transcribed/p<NNN>.txt` | Pages transcribed by hand from the page image because both OCRs scrambled them. They replace the OCR of that page. |
| `tesseract/p<NNN>.txt` | Pages whose Google OCR layout was scrambled (curved pages), re-OCR'd with Tesseract from the page image (`tesseract_threshold` in `volumes.json`). |

Each `[page …]` heading in the section files gives the printed page and the scan file and
PDF page it comes from.

## Markers in `acts.txt`

```
[register IV] Quatrième registre (1607-09-04 – 1610-12-30)

[official] Martin, chancelier (heading on p. 139)

[act 2-IV-0974]
date: 1609-02-09
date printed: 9 fév.
date status: printed in the margin
register: IV
page: 139 (scan: La France en Tunisie v.2_Part2.pdf p. 14)
folio: 381
language: français
consul: Gaspard Anfossis
chancelier: Martin
amounts: 45 écus
dates mentioned: 1609-02
flags: damaged
ocr corrections: ½ restored
text: (381) Lanterme Vincentou, corse habitant à Marseille, a reçu d'Antoine Carravane …
```

| Marker | Meaning |
|---|---|
| `[act REF]` | Starts an act. REF = volume-register-sequence, the same ref as in the database. |
| `date` | ISO date: `YYYY-MM-DD`, `YYYY-MM` when the day is not known, `undated (YYYY)` for acts marked « s. d. ». |
| `date printed` | The date as printed in the margin (`—` = same month as above; `........` = effaced in the register). |
| `date status` | `printed in the margin`; `not printed or not legible; taken from the previous act`; `supplied by the editor (in brackets)`; `undated in the register (s. d.)`; `day reconstructed from a damaged reading`; `month not read (heading lost in the scan)…` (volumes 5–8); `corrected by hand` (then `date note` says why). |
| `register`, `page`, `folio` | Register number, printed page (with the scan file and PDF page), folio number of the register when printed « (269) ». |
| `language` | Language of the original act (`italien`, …; `français` when no language is given). |
| `consul`, `vice-consul`, `chancelier` | Officials in office according to the book's headings. |
| `amounts` | Sums of money found in the text, as written. |
| `dates mentioned` | Other dates cited in the text (letters, earlier acts, receipts…). |
| `flags` | struck out, illegible, unfinished, damaged, copied in full in the annexes. |
| `ocr corrections` | Automatic corrections: words taken from the second OCR, `½` restored (the stacked fraction is often dropped by the OCR), text taken from the second OCR when this page is badly read. |
| `text` | Grandchamp's summary of the act, on one line. |
| `[official]` | A heading naming a new consul, vice-consul or chancellor. |
| `[footnote p. N (k)]` | The book's footnotes, at the end of the file. |

## How the text was made

* **Volume 1**: the Internet Archive scan's text layer, as is.
* **Volumes 2–3 and 5–8**: the HathiTrust scans (Google OCR). `scripts/extract_pages.py` reads the
  running headers to number the pages, normalises the OCR's spacing (« Natale , » → « Natale, »,
  « En italien . » → « En italien. — »), joins margin dates the OCR put on their own line,
  and moves dates the OCR placed on the wrong line.
* **Volume 2** has a second OCR (Tesseract on an older photocopy, `ocr/vol2/`).
  `scripts/organize_text.py` aligns each act with it and replaces words that occur only once in
  the volume by the second reading when that one is common and similar. Numbers are never changed
  automatically. Where the two disagreed, the page images were checked, and the HathiTrust
  reading was right in every case checked.
* Pages scrambled in every OCR were transcribed by hand from the page image (`transcribed/`).
* **Volumes 5–8** print the month as a heading and the year at the right edge of the page. In the
  page files these are rewritten as in volumes 2–3: the year on a line of its own at the left and
  the month after the day of the first act under each heading (« 3 juil. »). The month headings
  themselves are therefore not in the page files; the page images show them.

Regenerate with:

```
python3 scripts/extract_pages.py 3      # page files
python3 scripts/organize_text.py 3      # acts.txt and the other files
python3 scripts/remap_refs.py 3         # if acts were split or merged: keep annotations on the right acts
python3 scripts/build_db.py             # database and CSVs
```
