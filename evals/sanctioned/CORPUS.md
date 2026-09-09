# A corpus of citations courts have adjudicated to be fabricated

**Snapshot 2026-09-09.** This file documents the corpus only. No performance
figures appear here; those are in `PROTOCOL.md`.

## Why this corpus exists

Every performance number in this project comes from one benchmark of
*injected* defects. External validity is unproven. This corpus is different in
kind: each citation in it was named in a court order as fabricated or
unsupported, so the labels are judicial findings rather than an annotator's
judgment.

## Provenance

| | |
|---|---|
| Source | AI Hallucination Cases Database |
| URL | `https://www.damiencharlotin.com/hallucinations/hallucinations/download.csv` |
| Snapshot | `raw/hallucinations-2026-09-09.csv` |
| SHA-256 | `f4c94d4e80ca5182404c200286aff46e6fa4809be9b466661a1c7a1f1a0f9168` |
| Bytes | 1,931,833 |
| Records on that date | 2,035 |
| Fields | 18 |
| License | CC BY 4.0 |

**Attribution, required by the licence and reproduced verbatim:**

> AI Hallucination Cases Database, Damien Charlotin,
> https://www.damiencharlotin.com/hallucinations/

The snapshot is pinned because the database grows daily. Any figure computed
from it is meaningless without the date and hash above.

**Fetching notes.** The site is behind a service that rejects a default
programmatic user agent with HTTP 403; a browser user agent is required. The
export path contains a doubled `hallucinations/` segment, which is correct.
The CSV is fetched once and cached. The operator's `robots.txt` signals
`use=reference, ai-train=no`; a bounded validation fetch is reference use, and
this corpus is not training data.

## How citations were extracted

The `Hallucination Items` field is semi-structured: segments joined by `||`,
each segment being `Type: Category | prose narrative`. Only segments tagged
**`Fabricated: Case Law`** were considered. Records marked `Alleged = Yes` were
dropped, so every retained label is an adjudicated finding rather than an
accusation.

Within a retained segment, full `Case Name, Volume Reporter Page` strings were
matched. Curator prose prefixes — "Appellant cited", "The brief cited",
"Counsel cited" — and surrounding quotation marks are stripped, because they
leak into a naive match. Results are deduplicated on the normalised citation.

Each row keeps the untruncated narrative it came from, so any extraction can be
audited against its source without returning to the database.

## Measured yield

| | |
|---:|---|
| Records in snapshot | 2,035 |
| Dropped as merely alleged | 10 |
| Records containing a `Fabricated: Case Law` item | 1,586 |
| `Fabricated: Case Law` segments | 3,077 |
| Segments containing a parseable citation | 619 |
| Records yielding at least one citation | 345 |
| Raw citations extracted | 672 |
| Duplicates removed | 12 |
| **Unique citations** | **633** |
| Negative controls | 27 |

**Only 345 of 2,035 records (17%) yield citation-level ground truth.** The rest
describe the defect without naming the citation, and no amount of parsing
recovers what the curator did not transcribe.

Jurisdictions: USA 565, Canada 60, India 4, Australia 2, South Africa 1.

Reporter distribution of the extracted citations (top): `WL` 149, `F.3d` 72,
`F. Supp. 2d` 26, `F.2d` 17, `B.R.` 15, `F.4th` 14.

## Limitations, stated before any result

**This is a corpus of citations quoted in court *orders*, not of the *filings*
that contained them.** The database links to the order imposing sanctions, not
to the offending brief. It therefore answers "given a citation a court
adjudicated to be fabricated, does the tool flag it" — and **not** "does the
tool catch bad citations in a real brief." Those are different questions and
only the second is what a lawyer buys a checker for.

**Vendor-only identifiers are 149 of 633 (23.5%).** Citations of the form
`2019 WL 1396975` name real decisions by a commercial database's own number,
and no free archive holds them. The governing rule requires `UNVERIFIED` for
these, so the tool will never flag them, by design. They must be reported apart
from any headline in both directions — counting them as misses understates the
tool, counting them as catches rewards the behaviour the project forbids.

**The population skews to unrepresented litigants.** Across the database, 1,170
of 2,035 records involve a pro se litigant against 801 involving a lawyer.
Citation errors from that population may not resemble those in a
counsel-drafted brief.

**Any negative-control set drawn from these orders is biased easy.** A sanctions
order is dense with the court's *own* correctly-formatted authority. Those make
convenient true negatives and are a far easier negative class than the
malformed-but-real citations a checker meets in practice. The 27 controls here
carry that bias and no precision figure should be quoted from them alone.

**32 of 633 citations do not parse** into a recognised citation shape. They are
reported as unparsed rather than silently dropped.

## Files

| File | What it is |
|---|---|
| `fetch_corpus.py` | Downloads and pins the snapshot |
| `extract_citations.py` | Produces the citation-level ground truth |
| `measure.py` | Runs the checker over the corpus |
| `raw/manifest.json` | URL, date, hash, row count, attribution |
| `fabricated_citations.jsonl` | 633 citations with provenance |
| `negative_controls.jsonl` | 27 real citations from the same orders |
