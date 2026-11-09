# Grandchamp catalogue

A searchable database built from Pierre Grandchamp's *La France en Tunisie*:
volumes of summaries of the acts recorded in the French consulate's chancellery
in Tunis (debts, receipts, powers of attorney, ransoms of captives, ship
charters, protests…), together with the documents he published in full.

| Volume | Period | Acts (Part I) | Full-text documents (Parts II–III) |
|---|---|---|---|
| 1 — *La France en Tunisie à la fin du XVIe siècle* (Tunis, 1920) | 1582–1600 | 855 | 33 + 38 |
| 2 — *La France en Tunisie au début du XVIIe siècle* (Tunis, 1921) | 1601–1610 | 1326 | annexes: text files only |
| 3 — *La France en Tunisie au début du XVIIe siècle* (Tunis, 1925) | 1611–1620 | 2452 | annexes: text files only |
| 4 — *La France en Tunisie au XVIIe siècle* (Tunis, 1926) | 1621–1630 | 2327 | documents on Thomas de Arcos: text files only |
| 5 — *La France en Tunisie au XVIIe siècle* (Tunis, 1927) | 1631–1650 | 2192 | annexes: text files only |
| 6 — *id.* (Tunis, 1928) | 1651–1660 | 1714 | — |
| 7 — *id.* (Tunis, 1929) | 1661–1680 | 1760 | correspondence: text files only |
| 8 — *id.* (Tunis, 1930) | 1681–1700 | 1655 | correspondence, sea reports, documents (pp. 337–614): text files only |

| 10 — *La France en Tunisie de 1701 à l'avènement de la dynastie husseinite (1705)* (Tunis, 1933) | 1701–1703 | 283 | consular reports, protests, letters (pp. 61–190) and Nicolas Béranger's correspondence (pp. 191–321): text files only |

Volume 9 (the letters of Nicolas Béranger) is not part of this catalogue.

**Volumes 4 and 10** are read from photocopies (two pages per sheet; one per sheet in volume 10,
Parts 5–6), which proved more legible than the HathiTrust photographs of the open book (Tesseract
confidence about 90 against 85). `scripts/ocr_two_up.py` splits each sheet at the gutter and OCRs
each half; `volumes.json` pins the page number of every half (`page_numbers`) and lists the sheets
photocopied twice (`skip_halves`). The photocopy's own Acrobat text layer is used as a second
reading of the margin dates. Volume 4, pp. 82–83, missing from the photocopy, were transcribed from
the photographs. Volume 10's act analysis ends on 28 May 1703: the rest of register XXIV is lost.
The date check against the page images is complete for volume 10 and covers volume 4 up to p. 136.

The transaction columns of volumes 1–6 were reviewed by hand, act by act (`extraction =
'reviewed'`). Those of volumes 7, 8 and 10 are still the automatic draft (`extraction = 'auto'`)
and are being reviewed in the same way. Where Grandchamp printed two or more acts in one
paragraph, the review splits them (`split_at` in the annotation file); the extra acts get the
parent's ref with a letter suffix (`4-X-1678b`) and their own date.

Every volume also has **plain-text files** in `text/vol<N>/`, one per printed page plus organised
files (acts with date and other markers, annexed documents, indexes, table of contents). The
database is built from them. See [`text/README.md`](text/README.md).

## Files

| Path | What it is |
|---|---|
| `catalogue.sqlite` | The database (SQLite 3; open with [DB Browser for SQLite](https://sqlitebrowser.org/), Datasette, Python, R…) |
| `csv/transactions.csv` | **One row per act with the transaction columns** (see below); opens directly in Excel |
| `csv/` | Other CSVs (UTF-8 with BOM): `acts`, `act_amounts`, `documents`, `officials` |
| `text/vol<N>/` | Plain-text edition of each volume: page files, `acts.txt` with markers, documents, indexes… ([details](text/README.md)) |
| `annotations/vol<N>.json` | Hand corrections to the extracted transaction columns, plus splits of merged acts (volumes 1–6 fully reviewed) |
| `pdfs/` | Source scans: volume 1 from the Internet Archive; volumes 2–8 and 10 from HathiTrust (Google OCR), split into parts under 25 MB; `v.2.pdf` is the older photocopy of volume 2 |
| `volumes.json` | Per-volume settings: page layout plus the manual OCR corrections |
| `ocr/vol2/` | Tesseract OCR of the older volume 2 photocopy, used as a second reading (see below) |
| `scripts/extract_pages.py` | Writes the page text files of a volume from its scans |
| `scripts/organize_text.py` | Writes `acts.txt` and the other organised text files |
| `scripts/margin_ocr.py` | Re-reads the margin (month headings, days) of volumes 5–8 from the page images |
| `scripts/remap_refs.py` | Keeps the hand annotations on the right acts when a fix splits or merges acts |
| `scripts/build_db.py` | Rebuilds the database and CSVs from the text files (volume 1 documents from the PDF) |
| `scripts/extract_parties.py` | Rule-based first draft of the transaction columns from each summary |
| `scripts/annotate.py` | Merges corrections into `annotations/vol<N>.json` |
| `scripts/ocr_volume.py` | Re-OCRs a two-up photocopy scan with Tesseract into `ocr/vol<N>/` |

## Transaction columns

Each act (table `acts`, view `v_transactions`, file `csv/transactions.csv`) has:

| Column | Content |
|---|---|
| `year`, `month`, `day`, `month_day` | Date of the act (`month_day` as printed, e.g. « 19 mai »; « s. d. » when undated) |
| `transaction_type` | Redemption, Debt, Payment / receipt, Sale / transfer, Power of attorney, Charter (freight), Protest / summons, Surety, Company / partnership, Agreement, Undertaking / promise, Consular decision, Declaration / attestation, Registration / copy, Deliberation of the Nation, Will, Marriage contract, Other |
| `party1_name`, `party1_origins`, `party1_role` | The party making the act: debtor, grantor of a power of attorney, recipient of a payment, seller, protester… |
| `party2_name`, `party2_origins`, `party2_role` | The counterparty: creditor, attorney, payer, buyer, party summoned… |
| `other_parties_names`, `…_origins`, `…_role` | Others named: the captive ransomed, the captor or slave-owner paid, guarantors, witnesses, principals… |
| `matter` | What the transaction is about (English) |
| `price`, `currency` | Principal sum and its currency as qualified in the text (« écus d'or d'Espagne », « onces de Sicile »…) |
| `other_equivalent` | Further sums, exchange rates (« at 15 taris to the écu »), goods given or owed, instalments |
| `keywords` | Subject keywords, `;`-separated (ransom, debt, loan, hides, wool, coral, ship, avania, janissaries / divan…) |
| `decision` | Outcome of consular decisions, deliberations of the Nation and disputes, when the summary gives it |
| `notes` | Language of the act, condition (struck out, effaced…), repayment terms, later annotations, editorial remarks |

Conventions:

* **Several people in one role** are separated by `;`, and their origins and roles follow in
  the same order. A single value (e.g. `de Marseille`) applies to all of them.
* **Origins** are given as printed (« sicilien », « de Trapani », « turc renié »). Some
  place names are modernised where the OCR garbled them.
* **Roles** start with the role in the transaction (debtor, creditor, attorney…), followed by
  status or occupation (ransomed captive, ship master (patron), renegade, janissary, consul…).
* **Redemption** is used whenever the money or the act serves to ransom a captive: a loan for a
  ransom, its repayment, a surety, or an undertaking to ransom someone. A power of attorney
  stays *Power of attorney*, with « ransom » in `keywords`.
* `price` is the principal sum of the act. Where the summary itemises several sums, the rest
  are in `other_equivalent`.

How they were produced: `scripts/extract_parties.py` drafts every column from the summary's
formula (« X, sicilien, reconnaît devoir à Y … N écus payés pour son rachat à Z »). Every one
of the 855 acts was then reviewed by hand against its summary, and corrections were recorded
in `annotations/vol1.json` (724 acts needed at least one). The build applies them on top of
the draft, and the `extraction` column says `reviewed`. Names keep the summary's spellings,
including period variants and OCR errors, so the same person can appear under several spellings.

To correct a value, add it to `annotations/vol1.json`, either by hand or with
`python3 scripts/annotate.py 1 < fixes.json`, then rebuild.

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

* `act_type` — (the original French classification, superseded by `transaction_type`) first matching rule: *reconnaissance de dette*, *quittance*,
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

## Volumes 2, 3 and 5–8: HathiTrust scans

Volumes 2, 3 and 5–8 are read from the HathiTrust scans (`pdfs/La France en Tunisie v.N_PartK.pdf`), whose
Google OCR is much better than the earlier photocopy of volume 2. The pipeline is:

1. `scripts/extract_pages.py N` numbers the pages from the running headers, drops library pages,
   normalises the OCR (spacing, margin dates the OCR put on their own line or on the wrong line),
   and writes `text/volN/pages/`. Pages whose Google OCR is scrambled (curved pages) are re-OCR'd
   with Tesseract (`text/volN/tesseract/`), keeping Google's margin dates. A few pages scrambled in
   both were transcribed by hand from the page image (`text/volN/transcribed/`).
2. Remaining errors were checked against the page images and fixed in `text/volN/fixes.json`
   (page text replacements, splits and merges of acts, dates).
3. `scripts/organize_text.py N` parses the acts and writes `acts.txt` (one block per act, with
   date, register, page, folio, language, officials, amounts, dates mentioned, flags) and the
   section files.
4. `scripts/build_db.py` reads `acts.txt` and applies the hand annotations.

Volume 2 notes:

* The new scan was checked against what had been parsed from the photocopy. The act texts now
  come from the new scan. Words were compared with the photocopy's Tesseract OCR (`ocr/vol2/`),
  and the page images were checked wherever they disagreed. The book's errata are applied
  (`text/vol2/corrections.txt`). Pages 8–9, missing from the photocopy, are now included.
  Volume 2 has 1,326 acts, against 1,299 before: missing pages, and acts the OCR had run together.
* All annotations were carried over to the new act refs (`annotations/vol2_ref_map.json` maps old
  refs to new ones) and re-checked against the new text. Prices and numbers were corrected
  where the photocopy's OCR had misread them.
* Where the margin date is not legible, the act takes the previous act's date, and `date status` in
  `acts.txt` (and `notes`) says so.

Volume 3 notes:

* 2,452 acts in four registers (IV–VII, 1611–1620, a few acts dated 1621), with the annexed
  documents, the indexes of names, of slaves and of ships, and the glossary as text files.
* The transaction columns have been reviewed by hand, act by act.
* From p. 168 to 22 September 1617, Grandchamp's own manuscript was destroyed in the 1923 fire
  at the Imprimerie Rapide, and he redid that part from the register. Some leaves were too damaged
  for a second reading. His note is kept in the text.

Volumes 5–8 notes:

* The layout differs from volumes 2–3: the month is printed as a heading (or at the top of the
  page) and only the day in the margin; the year is at the right edge of the page. The page step
  (`month_headings` in `volumes.json`) rewrites this into the volume 2–3 form (« 3 juil. »,
  year line at the left) before the acts are parsed.
* The Google OCR often drops the month headings, so `scripts/margin_ocr.py` reads the margin and
  the running head of every page image again with Tesseract (`text/volN/margin_ocr.json`). The two
  readings of the margin days are aligned, and each heading is put back before the act it heads.
  Where the two OCRs read a day differently, the one that fits the order of the days is kept.
* Where no heading could be read, the month is taken as the one after the previous act's when the
  day falls back (30 → 2); `date status` says so (8–17 acts per volume). Years misread at the page
  edge are checked against the following pages.
* Every flagged date (out of order, unreadable, month in doubt, or disagreeing with the image) was
  checked against the page image: about 110 acts were corrected by hand (`date status: corrected by
  hand`, with a `date note`), and the rest were found correct. The out-of-order dates that remain
  are the register's own order (copies and late entries).
* Volume 7: the register dates the acts from 27 January to 3 August « 1669 », which the editor
  thinks is a mistake for 1670 (note on p. 158). The dates are given as printed, with a `date note`.
* A few days printed in the book do not exist (« 31 juin (sic) »); these acts are dated to the month,
  with a `date note`.
* Amounts in « pièces de 8 réaux » (pieces of eight), the usual currency of these volumes, are read
  as such in the `price` and `currency` columns.
* Volume 8: the analysis ends on p. 335; p. 336 is a blank page.

## Schema (main tables)

```
volumes(id, title, author, published, period_start, period_end, source_url, file, page_offset)
registers(volume_id, number, label, date_start, date_end)
acts(id, ref, volume_id, register, seq, date_iso, year, month, day, undated, date_supplied,
     date_raw, summary, language, act_type, concerns_ransom, struck_out, illegible,
     unfinished, damaged, consul, vice_consul, chancelier, page, pdf_page,
     month_day, transaction_type, party1_name, party1_origins, party1_role,
     party2_name, party2_origins, party2_role, other_parties_names, other_parties_origins,
     other_parties_role, matter, price, currency, other_equivalent, keywords, decision,
     notes, extraction)
act_amounts(act_id, amount, currency, qualifier, raw)
act_documents(act_id, document_id, method)
documents(id, volume_id, part, part_label, number, roman, title, date_iso, undated,
          page_start, page_end, pdf_page, text)
officials(volume_id, name, role, register, first_act_id, page, pdf_page)
footnotes(volume_id, page, pdf_page, marker, text)
acts_fts, documents_fts      -- full-text indexes (accent-insensitive)
v_acts                       -- readable view used for csv/acts.csv
v_transactions               -- the transaction columns, used for csv/transactions.csv
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

-- Who lent money for ransoms, and how much (in écus)
SELECT party2_name, count(*) AS acts, sum(price) AS total
FROM acts WHERE transaction_type = 'Redemption' AND currency LIKE 'écus%'
GROUP BY party2_name ORDER BY total DESC LIMIT 20;

-- Captors / slave-owners paid ransoms
SELECT other_parties_names, other_parties_role, date_iso, price, currency
FROM acts WHERE other_parties_role LIKE '%recipient of ransom%';

-- Acts recorded under a given chancellor
SELECT ref, date_iso, summary FROM acts WHERE chancelier LIKE '%Roze%';
```

## Rebuilding

Requires Python 3.9+ and `pdftotext` (poppler-utils: `apt install poppler-utils`
or `brew install poppler`). Re-OCRing pages also needs Tesseract (`fra`, `ita`) and Pillow; the
Tesseract output is cached in the repository, so it is only needed for new pages.

```bash
python3 scripts/extract_pages.py 3      # page text files (per volume)
python3 scripts/organize_text.py 3      # acts.txt and the other text files
python3 scripts/build_db.py             # database and CSVs
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

* **People**: build `persons` / `act_persons` tables from the party columns and merge
  spelling variants (*Borrilly / Borrilli / Borilly*), so individuals can be followed across
  acts and volumes.
* Refine `act_type` and capture amounts written out in words.
* Correct OCR in the Part II–III transcriptions where needed.
