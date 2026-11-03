# Grandchamp catalogue

A searchable database built from Pierre Grandchamp's *La France en Tunisie*:
volumes of summaries of the acts recorded in the French consulate's chancellery
in Tunis (debts, receipts, powers of attorney, ransoms of captives, ship
charters, protests…), together with the documents he published in full.

| Volume | Period | Acts (Part I) | Full-text documents (Parts II–III) |
|---|---|---|---|
| 1 — *La France en Tunisie à la fin du XVIe siècle* (Tunis, 1920) | 1582–1600 | 857 | 33 + 38 |

Volumes 2–10 can be added the same way: see [Adding a volume](#adding-a-volume).

## Files

| Path | What it is |
|---|---|
| `catalogue.sqlite` | The database (SQLite 3; open with [DB Browser for SQLite](https://sqlitebrowser.org/), Datasette, Python, R…) |
| `csv/` | The same data as CSV (UTF-8 with BOM, opens directly in Excel): `acts`, `act_amounts`, `documents`, `officials` |
| `pdfs/` | Source scans (Internet Archive, with an OCR text layer) |
| `volumes.json` | Per-volume settings: page layout plus the manual OCR corrections |
| `scripts/build_db.py` | Rebuilds the database and CSVs from the PDFs |

## How it was built

The PDFs already contain OCR text, so no new OCR was run. The script reads that
layer page by page (`pdftotext -layout`) and parses the book's layout:

* **Part I – *Analyse des actes*** is a calendar: a year line, then entries with
  the day and month in the left margin (`25 avril`, `26 —` = same month,
  `s. d.` = undated) followed by Grandchamp's French summary. Each entry becomes a row
  in `acts`. The bold headings naming the consul, vice-consul and chancellor are
  tracked, so every act records who was in office. Signature facsimiles,
  page numbers and OCR noise are skipped; footnotes go to `footnotes`.
* **Parts II and III** (acts concerning the *Nation française* and the
  *Compagnies du Corail*) are read from the table of contents. Each document's
  OCR text is cut out of the pages between its numeral heading and the next one.
* Acts whose summary says *« Voir copie in extenso p. N »* are linked to the
  matching document (`act_documents`). The match uses the date, then the page,
  then the title words, because some printed page references are wrong.

Every row keeps its **printed page** and **PDF page**, so any entry can be
checked against the scan.

### Fields derived by heuristics

These come from keyword rules on the French summaries. They help with filtering
but are not authoritative:

* `act_type` — first matching rule: *reconnaissance de dette*, *quittance*,
  *procuration*, *protestation / sommation*, *affrètement*, *caution*, *testament*…
  (`act_types` lists them; 59 acts remain `autre`).
* `concerns_ransom` — mentions *rachat / esclave*.
* `language` — set when the summary says *En italien* / *En latin*.
* `struck_out`, `illegible`, `unfinished`, `damaged` — from *biffé*, *illisible*,
  *inachevé*, *effacé / rongé / en partie*…
* `act_amounts` — sums of money found in the summary (`25 écus d'or d'Espagne`,
  `26 onces et 15 taris`…) with the currency normalised (`écu`, `once`, `tari`,
  `aspre`, `ducat`, `réal`…). Amounts written out in words are not captured.

### Accuracy notes

* Spelling is reproduced as OCR'd: names keep the period's spellings
  (*Borrilly / Borrilli / Borilly*), and some OCR errors remain in summaries
  and especially in the 16th-century transcriptions of Parts II–III.
* Dates were checked for impossible values. Where the OCR mangled a year or
  day, the correct value was read off the page image and recorded in
  `volumes.json` (`year_fixes`, `day_fixes`, `toc_page_fixes`). Two days cannot
  be read even in print (pp. 54 and 83) and are stored as NULL.
* The register is not strictly chronological (follow-up acts were written next
  to the original), so `seq` keeps the printed order and `date_iso` the act's own date.

## Schema (main tables)

```
volumes(id, title, author, published, period_start, period_end, source_url, file, page_offset)
registers(volume_id, number, label, date_start, date_end)
acts(id, ref, volume_id, register, seq, date_iso, year, month, day, undated, date_supplied,
     date_raw, summary, language, act_type, concerns_ransom, struck_out, illegible,
     unfinished, damaged, consul, vice_consul, chancelier, page, pdf_page)
act_amounts(act_id, amount, currency, qualifier, raw)
act_documents(act_id, document_id, method)
documents(id, volume_id, part, part_label, number, roman, title, date_iso, undated,
          page_start, page_end, pdf_page, text)
officials(volume_id, name, role, register, first_act_id, page, pdf_page)
footnotes(volume_id, page, pdf_page, marker, text)
acts_fts, documents_fts      -- full-text indexes (accent-insensitive)
v_acts                       -- readable view used for csv/acts.csv
```

`ref` is a stable identifier: `volume-register-sequence`, e.g. `1-II-0415`.

## Example queries

```sql
-- Full-text search (accent-insensitive)
SELECT a.ref, a.date_iso, a.summary
FROM acts_fts f JOIN acts a ON a.id = f.rowid
WHERE acts_fts MATCH 'corail AND vernet';

-- Ransoms per year
SELECT year, count(*) FROM acts WHERE concerns_ransom GROUP BY year;

-- Total écus acknowledged as debts, per year
SELECT a.year, round(sum(m.amount)) AS ecus
FROM acts a JOIN act_amounts m ON m.act_id = a.id
WHERE a.act_type = 'reconnaissance_dette' AND m.currency = 'écu'
GROUP BY a.year;

-- An act with the full text of its copy in Part II/III
SELECT a.ref, a.summary, d.title, d.text
FROM acts a JOIN act_documents ad ON ad.act_id = a.id JOIN documents d ON d.id = ad.document_id
WHERE a.ref = '1-II-0415';

-- Acts recorded under a given chancellor
SELECT ref, date_iso, summary FROM acts WHERE chancelier LIKE '%Roze%';
```

## Rebuilding

Requires Python 3.9+ and `pdftotext` (poppler-utils: `apt install poppler-utils`
or `brew install poppler`). No Python packages are needed.

```bash
python3 scripts/build_db.py
```

The script prints warnings for anything it could not parse confidently (lines
in the date column that are not dates, out-of-range years, unmatched
cross-references). Check each warning against the scan and add a fix to `volumes.json`.

## Adding a volume

1. Put the PDF in `pdfs/`.
2. Add an entry to `volumes.json`. Give the `page_offset` (PDF page minus printed
   page) and the printed page ranges of the analysis, the registers and each part's
   table of contents and text.
3. Run the build, read the warnings, and add `year_fixes` / `day_fixes` /
   `toc_page_fixes` after checking the page images.

Later volumes may be laid out differently. If so, the parser will need adjusting,
and the warnings will show where.

## Possible next steps

* **People and places**: extract the individuals (names, origin such as *corse,
  sicilien, de Marseille*, roles such as *patron, renégat, esclave*) into
  `persons` / `act_persons` tables, and normalise spelling variants.
* Refine `act_type` and capture amounts written out in words.
* Correct OCR in the Part II–III transcriptions where needed.
