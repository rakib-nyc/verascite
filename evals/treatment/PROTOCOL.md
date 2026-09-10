# P3 — a bounded negative-treatment signal, measured again and still rejected

**2026-09-09.** Re-measured once a CourtListener credential existed, which is
what had blocked it. The answer is better understood than before and it is
still no.

## What was already known

`verify_treatment.py` records the measurement that killed the document-level
version: of the opinions citing *Twombly*, roughly a sixth contain "overruled",
"abrogated" or "no longer good law" somewhere in their text, and almost none of
them are about *Twombly*. Distinguishing real treatment was priced at two
requests per citing opinion — one authority per day against a 125-request
ceiling.

## What is new

The search index supports **proximity queries**, which asks a much narrower
question — does the overruling language sit next to the case's own name — at
**one request per authority** rather than two per citing opinion. That removes
the cost objection entirely. So the remaining question is whether the signal
discriminates.

## Method

Ten authorities whose status is not in dispute, five each way. For each:
citing count, count containing negative-treatment language anywhere
(`doc-level`), and count with that language within ten words of the case's
distinctive party name (`proximity`).

## Result

| case | status | citing | doc-level | | proximity | |
|---|---|---:|---:|---:|---:|---:|
| Bowers | overruled | 564 | 302 | 53.5% | 73 | **12.94%** |
| Austin | overruled | 248 | 118 | 47.6% | 32 | **12.90%** |
| Plessy | overruled | 569 | 311 | 54.7% | 45 | **7.91%** |
| Roe | overruled | 3,228 | 1,048 | 32.5% | 46 | **1.43%** |
| Chevron | overruled | 14,277 | 4,053 | 28.4% | 131 | **0.92%** |
| Twombly | good law | 14,623 | 3,887 | 26.6% | 45 | **0.31%** |
| Iqbal | good law | 12,095 | 3,243 | 26.8% | 14 | **0.12%** |
| Matsushita | good law | 24,372 | 5,128 | 21.0% | 2 | **0.01%** |
| Celotex | good law | 48,912 | 9,357 | 19.1% | 6 | **0.01%** |

*Daubert timed out and is excluded.*

| | overruled (median) | good law (median) | separation |
|---|---:|---:|---:|
| document level | 47.6% | 26.6% | **1.8×** |
| proximity | 7.91% | 0.12% | **66×** |

**Proximity is dramatically better than the rejected document-level version.**
That is a real finding and it is why this was worth re-measuring.

## Why it still does not ship

**The distributions overlap where it matters.** *Roe* (overruled) sits at 1.43%
and 46 hits. *Twombly* (good law) sits at 0.31% and **45 hits**. On raw counts
they are indistinguishable. No single threshold on either rate or count
separates the two groups.

Three mechanisms, each verified rather than supposed:

**1. Directional conflation.** The query cannot tell "X overruled Y" from "Y
overruled X". Measured: of the 45 proximity hits for *Twombly*, **5 match
"Twombly overruled Conley"** — *Twombly* doing the overruling. A signal that
fires on a case for overruling something else is worse than silence: it would
flag the most important cases in a field precisely because they are important.

**2. Sense conflation.** Trial courts overrule *objections* and *motions*. The
word carries a procedural sense that has nothing to do with precedential
validity, and it is far more common in the corpus than the precedential sense.
This accounts for most of the remaining *Twombly* hits.

**3. Rate dilution runs the wrong way.** An authority overruled after decades of
good standing has decades of citations that predate the overruling, so its rate
is *lowest* exactly when the case is oldest and most consequential — *Roe* at
1.43%, *Chevron* at 0.92%, against *Bowers* at 12.94%. The metric is weakest on
the cases a lawyer most needs it for.

## The trap this avoids, restated

A partial citator that reports "no negative treatment found" is the governing
rule's failure mode wearing a different hat: absence of a signal presented as
assurance. With the overlap measured above, a shipped version would report
"nothing found" for *Roe v. Wade* and "3 signals found" for *Twombly*. That is
not a bounded signal. It is a confident wrong answer about whether a case is
good law, which is the one thing this tool must never give.

## What would change the answer

Named so the next attempt does not start over:

- **Direction.** Query the language that only applies to the *overruled* case:
  `"overruled by"`, `"abrogated by"`, `"superseded by"` immediately following
  the name, rather than any co-occurrence within a window.
- **Sense.** Exclude the procedural sense — `overruled` adjacent to
  `objection`, `motion`, `demurrer`.
- **Time.** Compute the rate over citations *after* the candidate overruling
  date rather than over all citations, which removes the dilution.
- **Anchoring.** Restrict to opinions from a court that could bind the cited
  authority. A district court cannot overrule the Supreme Court.

Each is cheap to test with the proximity mechanism now established. None was
tested here, and none is claimed to work.

## Status

**Not shipped.** `verify_treatment.py` is unchanged and continues to report
`NOT_CHECKABLE` with the citing-opinion count and a link, saying plainly that
whether the authority is good law was not determined.

Cost of this protocol: 31 requests.
