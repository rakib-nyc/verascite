# P4 — external validation on judicially-labelled citations

**2026-09-09, completed.** Corpus documented in `CORPUS.md`; read its
limitations first. Harness: `measure.py`.

Every earlier figure in this project came from one benchmark of *injected*
defects. This is the first measurement against citations that a court
adjudicated to be fabricated — labels that are judicial findings rather than an
annotator's judgment.

## Method

Two passes, because CourtListener is a nonprofit serving this free under a
measured ceiling of five requests a minute and roughly 125 a day. Pass one
resolves every distinct citation in batches and fills the cache. Pass two scores
each citation against that cache and makes no request at all.

**The whole corpus cost four requests.** 597 distinct citations, batched at up
to 250 per request and paced under the 60-citations-per-minute limit. Re-running
is free.

## Result

| | |
|---:|---|
| Citations in corpus | 633 |
| Did not parse | 32 |
| Vendor-only identifiers, excluded from the headline | 149 |
| **Scored** | **484** |
| **Flagged** | **239** |
| **Share flagged** | **49.4%** |

Distribution over the 484 scored: `FLAGGED` 239, `UNVERIFIED` 114, `REVIEW` 53,
`VERIFIED` 46, unparsed 32.

By dimension: `case_name` 196, `reporter_valid` 43. Nothing else fired, which is
what a bare citation with no year and no court leaves available to check.

### This is a lower bound on recall, not recall

The corpus labels a **record**, not always the citation extracted from it. Three
kinds of label noise all push the same way:

1. A narrative of the form *"counsel cited X, which does not exist; the correct
   case was Y"* contains two citations, and the extractor can take Y.
   Hand-read examples: `Iko v. Shreve, 535 F.3d 225` is real — the fabrication
   in that record was `Iko v. Shreve, 122 F.3d 707`.
2. A real case to which a **fabricated quotation** was attributed, tagged under
   fabricated case law. The citation is sound; the quotation is the defect, and
   it is not checkable without the opinion text.
3. A real case cited for a proposition it does not support.

Every one of these can only *lower* the figure. **49.4% is a floor.**

Measured, rather than asserted: rows whose narrative discusses a correction are
19.6% of the `VERIFIED` group against a ~9-10% base rate elsewhere — a real
signal. But excluding all 51 of them moves the headline only from **49.4% to
49.7%**, so contamination of that kind is *not* the main explanation for the 46
`VERIFIED`. Kinds 2 and 3 are, and they are defects this tool does not claim to
catch from a citation string alone.

## The result that matters more than the headline

**All 149 vendor-only identifiers were reported `UNVERIFIED`. None was flagged.**

These are citations a court adjudicated to be fabricated. The tool declined to
say so, because a `2019 WL 1396975` string that was invented is
indistinguishable from one naming a real unreported decision without consulting
a source that holds it — and no free source does.

That is the governing rule holding under the hardest available pressure: on
citations that really were fabricated, with a judicial finding to prove it, the
tool still refused to treat absence as fabrication. The benchmark measured the
same rule from the other direction — zero false accusations on 101 sound
citations absent from every source.

It also sets a ceiling worth stating plainly: **23.5% of the defective citations
in this corpus are ones this tool will never flag, by design.** A checker that
flagged them would score better here and be a worse tool.

## What this run found that the benchmark never did

Asking why 49 citations came back `VERIFIED` exposed a real defect in the
case-name matcher: it scored `State v. Pune` against `State v. Ing` at 1.00, and
would confirm a fabricated case name against any unrelated decision printed at
the same page whenever the two shared a generic party. Criminal and government
captions are an enormous share of American case law.

The benchmark could not have found it: its citations carry years and courts,
which the metadata stage uses to disambiguate. Strip those, as a bare citation
in a sanctions order does, and the case name is doing all the work alone.

Fixed and locked under test; full analysis in `evals/names/V9_PROTOCOL.md`.
The fix moved this corpus from 46.7% to 49.4%.

**This is what external validation is for**, and it is the single most useful
thing this corpus has produced.

## What this does not establish

- **Performance on real briefs.** The corpus is drawn from orders, not filings.
- **Precision.** The 27 negative controls are a court's own correctly formatted
  citations — an easier negative class than a real document presents. No
  precision figure is quoted from them, and none should be.
- **A comparable recall number.** See the lower-bound discussion above.
