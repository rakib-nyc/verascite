# P4 — external validation on judicially-labelled citations

**2026-09-09.** Corpus documented in `CORPUS.md`; read its limitations first.
Harness: `measure.py`. This file reports what was measured and, as prominently,
what was not run.

## What was run

`--offline`: extraction, resolution, reporter validity, and metadata. **Existence
resolution did not run**, because it requires a CourtListener credential and
none was present in this environment.

That is a real gap and it is the reason the headline below is low. Existence
resolution is the check that catches a well-formed citation to a case that does
not exist, and it is doing none of the work here. **The number below is not this
tool's recall on this corpus.** It is the recall of the subset of checks that
need no credential.

## Result

| | |
|---:|---|
| Citations in corpus | 633 |
| Did not parse | 32 |
| Vendor-only identifiers, excluded from the headline | 149 |
| **Scored** | **484** |
| **Flagged** | **43** |
| **Detection rate, credential-free checks only** | **8.9%** |

Every flag came from one dimension: `reporter_valid` — a citation naming a
reporter series that has never been published. Nothing else can fire without a
lookup.

Distribution over the 484 scored: `REVIEW` 407, `FLAGGED` 43, unparsed 32,
`UNVERIFIED` 2.

## The result that matters more than the headline

**All 149 vendor-only citations were reported `UNVERIFIED`. None was flagged.**

These are citations a court adjudicated to be fabricated. The tool declined to
say so, because a `2019 WL 1396975` string that was invented is indistinguishable
from one naming a real unreported decision without consulting a source that
holds it — and no free source does.

That is the governing rule holding under the hardest possible pressure: **on
citations that really were fabricated, with a judicial finding to prove it, the
tool still refused to call absence fabrication.** The benchmark measured this on
101 sound citations absent from all sources and found zero false accusations.
This measures the same rule from the opposite direction and it holds.

It also sets a ceiling that should be stated plainly: **23.5% of the defective
citations in this corpus are ones this tool will never flag, by design.** A
checker that flagged them would score better on this corpus and would be a
worse tool.

## What this does not establish

- **Recall with existence resolution.** Not measured. Needs a credential.
- **Performance on real briefs.** The corpus is drawn from orders, not filings.
- **Precision.** The 27 negative controls are a court's own correctly formatted
  citations — an easier negative class than a real document presents. No
  precision figure is quoted from them.

## To complete this

Set `COURTLISTENER_API_TOKEN` and re-run without `--offline`. 484 scored
citations batch into roughly 8 lookup requests at 60 citations per request,
which is inside a single day's budget. The result will be the first measurement
of this tool against defects that were filed in court rather than injected into
a benchmark, and the honest expectation recorded in the roadmap stands:
**expect the number to drop.**
