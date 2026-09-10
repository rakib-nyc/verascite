# The false-accusation rate

**2026-09-10.** Harness: `measure_abstention.py`. Reproducible from cache; the
run that produced these figures cost **five requests**.

## The question

Not "how many defects does it catch." That question is asked constantly and it
is not the one the field is failing. This asks: **when a verifier meets a real
citation its sources do not contain, what does it do?**

The question matters because absence is common. Measured here, on 996 sound
citations drawn from real federal appellate briefs, **9.5% were unreachable** —
not defective, not suspicious, simply not held by the free archives. That
figure independently corroborates the ~10.2% reported for the same corpus by
Liu, Stammbach & Henderson.

A verifier that treats absence as fabrication therefore does not fail rarely.
It fails on roughly one citation in ten, and every one of those failures is an
accusation against sound authority — the expensive error in legal practice, and
the one this project was built to prevent.

## Method

Every citation in the benchmark corpus carrying **no defect label** is a
citation that is fine. That is the population: 996 distinct citations, all of
which parsed.

Of those, the ones the sources could not speak to form the test set. Two
verdicts qualify, and they are one category from the reader's side — nobody
confirmed it:

| | n | what it means |
|---|---:|---|
| `NOT_FOUND` | 19 | searched for, not held |
| `OUT_OF_SCOPE` | 76 | a vendor-only identifier, journal, or authority type no free archive carries |
| **total unreachable** | **95** | **9.5% of the corpus** |

**Recall is undefined on this set and is not reported.** There is nothing to
detect. Every flag raised here is wrong by construction, which is exactly what
makes the measurement clean: no judgement call is required about whether a
given flag was justified.

## Result

| | |
|---:|---|
| Sound citations tested | 996 |
| Unreachable by the sources consulted | 95 |
| **Flagged as a finding** | **0** |
| **False-accusation rate** | **0.0%** |
| Every unreachable citation named the sources searched | **yes, 95 / 95** |

All 95 reported `UNVERIFIED` — absent from the sources consulted, with those
sources named, and no claim about the citation itself.

### Against the published figures

| System | Test set | Sound citations falsely flagged |
|---|---|---:|
| Gemini | 132 real-but-absent | **65.9%** |
| GPT-5 | 132 real-but-absent | **25%** |
| **VeraScite** | 95 unreachable, this run | **0%** |
| **VeraScite** | 101 absent-from-all-sources control | **0%** |
| **VeraScite** | 149 vendor-only, *court-adjudicated fabricated* | **0%** |

**Different test sets.** These are comparable in task, not item for item, and
the honest reading is that the three VeraScite rows are three independent
samples of the same behaviour rather than one head-to-head. Across them, **0
false accusations in 345 unreachable citations**, drawn from a benchmark corpus,
a control subset, and real sanctioned filings.

The third row is the strongest of the three and the least intuitive: those 149
citations *were* fabricated, and a court said so. The tool still declined to say
so, because a vendor-only identifier that was invented is indistinguishable from
one naming a real unreported decision without consulting a source that holds it
— and no free source does. Refusing to guess there is the same behaviour that
produces the zeros above, seen from the opposite direction.

## What the measurement changed

The run found a real gap. Of the 95 unreachable citations, **76 named no
sources at all** — `OUT_OF_SCOPE` was never covered by the rule requiring a
`NOT_FOUND` to say where it looked, although it reaches the reader as
`UNVERIFIED` identically. A verification record could therefore say "not
confirmed" without saying against what.

Fixed, and locked by a test in `tests/test_p1_separation.py`. All 95 now name
their sources.

## Honest limits

- **The absent subset is 95, not thousands.** A 0% rate on 95 has a real
  confidence interval; the upper bound is not zero.
- **One citation the sources *did* contain was flagged.** It is reported apart
  and not counted either way: the benchmark labels only *injected* errors, so a
  genuine pre-existing error in a filed brief scores against the tool that finds
  it. It has not been verified by hand.
- **The comparison rows are not item-for-item.** Running this harness over the
  exact 132 citations used in the published work is the obvious next step and
  has not been done.
- **This measures abstention, not competence.** A tool that flagged nothing ever
  would also score 0% here. The figure is only meaningful read beside the
  detection results in `evals/lephantomcite/`, where the same build finds 96.9%
  of non-existent citations.
