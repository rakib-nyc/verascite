# v1 run: stopping decision

**Written 2026-08-31, before any of the defects below were touched.** Committed
separately from the fixes so the git history shows the order. A stopping
decision written after the fix reads as reconstruction even when it is not.

## What was run

The protocol in `PREREGISTRATION.md` (commits `89ba9af`, `ea08225`),
unmodified, against the LePhantomCite eval split. Deterministic layer only.
Thresholds version `1`. Quote checking enabled, court lookups disabled, both
as pre-registered.

## Where it was stopped

**75 of 387 excerpts — 19% of the corpus.** Halted deliberately, not by
failure. The process was killed; it did not crash.

| | value |
|---|---|
| Excerpts scored | 75 of 387 |
| True positives | 9 |
| False positives | 34 |
| False negatives | 35 |
| Precision | **20.9%** |
| Recall | **20.5%** |
| F1 | **20.7%** |

Last full log line was at n=70 (tp=9, fp=30, fn=31); the final checkpoint at
n=75 is the figure above.

## Why it was stopped

Three parsing defects, identified partway through by a diagnostic that made 4
requests against an already-warm cache, account for the large majority of the
34 false positives. Continuing would have spent an estimated 10.5 further
hours and roughly 1,500 additional requests against Free Law Project's shared
tier — a small nonprofit's infrastructure — to extend a measurement of three
known, fixable bugs from 19% coverage to 100%.

That is not a defensible use of someone else's infrastructure, and no
methodological principle requires it. The purpose of pre-registration is to
prevent choosing the verdict mapping after seeing scores. The mapping was
committed before the data was downloaded, the run went against it unmodified,
and it is being stopped at a disclosed point for a stated reason. That purpose
is intact.

A second consideration: throughput was degrading badly. Cumulative cost per
excerpt ran 13s → 86s → 119s → 280s as cache hits thinned out. The last five
excerpts alone took 3.5 hours. Full-corpus completion was not 10.5 hours away;
it was considerably further.

## The three defects (identified before stopping, fixed after)

1. **Spelled-out journal names flagged as invented reporters.**
   `84 Oregon Law Review 227 (2005)` — eyecite's journal database holds the
   abbreviated form (`Or. L. Rev.`) and not the spelled-out one, so eyecite
   returns nothing, the shadow scanner claims the span, and `reporters-db`
   reports the reporter as non-existent. A real law review citation is
   reported as fabricated. Same class as the `42 U.S.C.` defect fixed in M1.

2. **Markdown emphasis corrupts case names.**
   The corpus is olmOCR-converted PDF, so case names arrive as
   `*Heartland Regional Med.Ctr. v. Sebelius*`. The case-name walk-back stops
   at the asterisk and yields `Regional Med.Ctr. v. Sebelius*` — the first
   party lost, the trailing asterisk kept — which scores 0.00 against the
   retrieved name and produces a `case_name: FAIL`.

3. **`id.` bound to the wrong antecedent**, inheriting a case-name comparison
   against an unrelated authority.

## How v1 is to be cited

> **v1 (bug-limited floor).** n=75 of 387, halted at 19% coverage. Precision
> 20.9%, recall 20.5%, F1 20.7%. Three parsing defects identified as the
> dominant precision cost. **Superseded by v2.** This is a floor produced by a
> partial run against known-broken parsing, not a measurement of the
> approach.

The number is not to be quoted without that qualification.

## v2 protocol

- Fix the three defects above and nothing else. No threshold in
  `verascite/config.py` changes.
- **Re-run the same 75 excerpts first**, so the delta is attributable purely
  to the three fixes. Changing the slice and the code together would confound
  them.
- Then extend to the full corpus.
- Report recall decomposed by hallucination category, always. A single recall
  figure averages a category the deterministic layer can detect against one it
  provably cannot, and that average means nothing.
