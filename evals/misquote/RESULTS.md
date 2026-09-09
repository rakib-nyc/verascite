# P5 — misquotation, measured on text that is not scanned

**2026-09-09.** Written after the runs it describes. Nothing below is projected;
where a number is an application of measured rates rather than a direct
measurement, it says so on the line.

## The question, and why it was open

Misquotation is **42 labels (13% of defects) at 0% recall**. V8h established the
cause and it was not a weak matcher. A *correct* quotation matched its source at
a median of **0.929**, not 1.00, because the brief and the archive were both
scanned text. The injected defects are one- and two-word substitutions, which
are **smaller than the disagreement scanning already produced**. Every threshold
was swept; precision peaked at **17.2%**. The check was built, measured, and
deliberately not shipped.

That rejection was always a statement about the corpus rather than about the
check. The standing hypothesis was that publisher-quality text would dissolve
the problem. It could not be tested there: only one misquote-labelled citation
in the benchmark resolved to a non-scanned opinion.

## Method

The cache holds **280 opinion texts** carrying the archive's own
`extracted_by_ocr` flag — **236 not scanned, 44 scanned** — so the subsets are
separated exactly rather than guessed at. Passages are drawn from those
opinions; the same one- and two-word substitutions the benchmark injects are
applied to produce the misquotes; the shipped matcher is used unmodified.

**The trap in the obvious design.** Drawing a passage out of an opinion and
matching it back against that same opinion returns 1.00. It is the same string.
A measurement built that way reports a beautiful separation and measures
nothing. Correct quotations are therefore generated in three grades and never
pooled:

| Grade | What it models |
|---|---|
| `verbatim` | A perfect paste. **Circular by construction; an upper bound, not a finding.** |
| `conventional` | Quoted as a careful lawyer quotes: bracketed alterations, a bracketed substitution. |
| `noisy` | Carrying transcription noise at a stated per-word rate. |

Harness: `build_and_measure.py`. Seed `20260909`, fixed.

## Result 1 — the archive's scanning flag is not the variable

This was the hypothesis, and it is wrong.

At every noise level the scanned and non-scanned subsets track each other:

| per-word noise | non-scanned correct (median / min) | scanned correct (median / min) |
|---:|---|---|
| 0.00 | 1.0000 / 0.9854 | 1.0000 / 0.9931 |
| 0.02 | 1.0000 / 0.9882 | 1.0000 / 0.9850 |
| 0.05 | 0.9964 / 0.9787 | 0.9965 / 0.9800 |
| 0.10 | 0.9933 / 0.9625 | 0.9929 / 0.9756 |
| 0.20 | 0.9857 / 0.9524 | 0.9837 / 0.9643 |

**Gating the check on `extracted_by_ocr == False` — the plan the roadmap
recorded — would not have worked.** What decides the outcome is not whether the
archive was scanned. It is how far the quoting document and the archive
disagree, and a fixed threshold cannot know that: it is applied with the same
number to a brief whose quotations agree at 0.999 and to one whose quotations
agree at 0.95, and it is wrong for one of them.

## Result 2 — the noise model reproduces the original finding

Precision at the true base rate (42 misquote labels against 233 sound
quotations), threshold 0.99, non-scanned subset:

| per-word noise | precision at true base rate |
|---:|---:|
| 0.00 | 80.3% |
| 0.02 | 88.9% |
| 0.05 | 62.0% |
| 0.10 | 34.3% |
| 0.20 | 19.0% |
| 0.35 | 15.8% |

At high disagreement this lands on **15.8–19.0%**, against the **17.2%** V8h
measured on real scanned text. The synthetic noise is random and real scanning
errors are systematic, so this is corroboration rather than proof — but a model
that independently reproduces the number it was not fitted to is worth more than
one that does not.

## Result 3 — calibrating to the document works

If the variable is disagreement between document and archive, the document
carries the evidence needed to measure it. A brief quotes many authorities. If
nine quotations match at 0.999 and the tenth matches at 0.977, the tenth is
anomalous *for this document* — and the comparison is between quotations that
passed through the same author, the same word processor, and the same
extraction. If all ten match at 0.95, nothing separates a misquote from the
background and the correct output is silence.

Harness: `calibrated.py`. Synthetic documents of 10 quotations with exactly one
alteration planted — a **10% positive rate, slightly more conservative than the
15% the benchmark carries**. Outlier margin swept; a document whose own median
agreement falls below 0.99 is declined outright.

At margin **0.02**:

| per-word noise | precision | recall | documents declined |
|---:|---:|---:|---:|
| 0.00 | 80.0% | 41.4% | 0 / 29 |
| 0.02 | 87.5% | 51.9% | 0 / 27 |
| 0.05 | 90.0% | 56.3% | 1 / 32 |
| 0.10 | 90.9% | 34.5% | 11 / 29 |
| 0.20 | 50.0% | 3.4% | **28 / 29** |

**80–91% precision against a 17.2% ceiling**, on a defect class currently at 0%
recall. And where the document is too noisy the gate switches the check off
rather than guessing — 28 of 29 documents declined at the highest disagreement
level, which is the behaviour that makes the rest of the table safe to rely on.

## What shipped, and what did not

Shipped as `verify_agreement.py`, and **only ever as `REVIEW`**. Two reasons, and
the second is binding:

1. The evidence is synthetic disagreement, not real scanned briefs. The
   precision figure is corroborated, not proven.
2. **An anomaly in agreement is not contradiction.** The honest statement is
   "this quotation matches its source less well than the rest of yours do —
   read it." That is a prompt to look, not a finding against the document.
   `FAIL` stays reserved for the quotation check proper, which requires
   retrieved text that actually contradicts.

Gates, all three enforced in code and tested: at least **6** located quotations,
document median agreement at least **0.99**, outlier margin **0.02**.

**The fixed-threshold check remains rejected.** Nothing here rehabilitates it.

## Honest limits

- Synthetic noise is random; real scanning error is systematic (`rn`→`m`,
  `l`→`1`). The reproduction of 17.2% is corroborating, not conclusive.
- Passages are drawn from the archive text itself, so the document side of the
  disagreement is modelled rather than observed. A corpus of real briefs with
  known-good quotations would settle it and does not exist here.
- Per-cell n is 27–32 documents. The confidence intervals are wide.
- The substitution vocabulary is fixed and hand-built. A misquote that alters
  meaning without touching one of those words is not represented.
- No threshold in `verascite/config.py` was changed by this work. The three
  gates are new constants in `verify_agreement.py`, declared here.
