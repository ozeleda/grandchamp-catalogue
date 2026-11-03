# Grandchamp catalogue

A searchable database built from Pierre Grandchamp's *La France en Tunisie*:
volumes of summaries of the acts recorded in the French consulate's chancellery
in Tunis (debts, receipts, powers of attorney, ransoms of captives, ship
charters, protests…), together with the documents he published in full.

| Volume | Period | Acts (Part I) | Full-text documents (Parts II–III) |
|---|---|---|---|
| 1 — *La France en Tunisie à la fin du XVIe siècle* (Tunis, 1920) | 1582–1600 | 855 | 33 + 38 |

Volumes 2–10 can be added the same way: see [Adding a volume](#adding-a-volume).

## Files

| Path | What it is |
|---|---|
| `catalogue.sqlite` | The database (SQLite 3; open with [DB Browser for SQLite](https://sqlitebrowser.org/), Datasette, Python, R…) |
| `csv/transactions.csv` | **One row per act with the transaction columns** (see below); opens directly in Excel |
| `csv/` | Other CSVs (UTF-8 with BOM): `acts`, `act_amounts`, `documents`, `officials` |
| `annotations/vol1.json` | Hand corrections to the extracted transaction columns (all 855 acts reviewed) |
| `pdfs/` | Source scans (Internet Archive, with an OCR text layer) |
| `volumes.json` | Per-volume settings: page layout plus the manual OCR corrections |
| `scripts/build_db.py` | Rebuilds the database and CSVs from the PDFs |
| `scripts/extract_parties.py` | Rule-based first draft of the transaction columns from each summary |
| `scripts/annotate.py` | Merges corrections into `annotations/vol<N>.json` |

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

* **People**: build `persons` / `act_persons` tables from the party columns and merge
  spelling variants (*Borrilly / Borrilli / Borilly*), so individuals can be followed across
  acts and volumes.
* Refine `act_type` and capture amounts written out in words.
* Correct OCR in the Part II–III transcriptions where needed.
