# Proposition support: first measurement

**2026-09-02.** The model-assisted layer (M5) had been built, tested against
adversarial inputs, and never once run against real data. This is that run.

## Why it matters

Content misrepresentation — a real case, correctly cited, that does not say
what the brief claims — is **131 of 321 labels (41%)** in the LePhantomCite
eval split, and the deterministic layer detects **0** of them. No string
comparison reaches it. It is the single largest block of the recall gap, and
the whole reason a model-assisted layer exists.

## Method

18 citations drawn from the corpus: 8 labelled `content_misrepresentation`,
10 labelled clean, shuffled and read blind. For each, the input was exactly
what `verify_proposition.py` constructs — the proposition verbatim from the
brief, the introductory signal, and a passage of the opinion retrieved from the
Caselaw Access Project. Verdicts were recorded before any label was seen.

A "flag" counts as `CONTRADICTED`, or `NOT_SUPPORTED` at high confidence.

## Result

| | |
|---|---:|
| Precision | **75%** (3 of 4 flags correct) |
| Recall | **38%** (3 of 8 found) |
| F1 | **50%** |
| Deterministic layer, same category | **0%** |

Two of the three detections were genuine contradictions with a verifiable
supporting span:

- *Roberts v. Rhode Island* — the brief asserts a lengthy history of contraband
  was "lacking here"; the opinion says *"Although the record here does indicate
  a lengthy history of contraband problems"*.
- *Landry v. FDIC* — the brief asserts that "no direct injury is necessary" as
  settled law; the opinion says *"The Supreme Court has not decided whether an
  Appointments Clause violation requires reversal where it appears to have done
  a party no direct harm"*.

The third: *SEC v. Chenery* cited for a proposition about the Equal Protection
Clause and Medicare beneficiaries. Chenery is about agency reasoning and says
nothing of the kind.

## What the five misses show, and it is not a reading problem

**Three of the five were retrieval failures, not judgment failures.** The
passage-selection step searched the *whole* opinion for text relevant to the
proposition, found supporting language elsewhere in the case, and reported
support — when the *pincited page* does not contain it. The reading was
correct about the passage it was given and the passage was the wrong one.

The fix is available and not yet built: **scope the retrieval to the pincited
page.** CAP supplies star pagination, so the page a pincite names can be
isolated and the model shown only that. This is the same capability the pincite
check uses, applied to proposition support.

One miss (*Bonanno*) had no proposition to evaluate — a string cite with no
assertion attached. One (*Priester*) was called `NOT_SUPPORTED` at *medium*
confidence, so it did not count as a flag. Counting medium confidence as a flag
would raise recall to 50% here; whether that holds up or simply costs precision
needs a larger sample than 18.

## Projection, stated as a projection

If 38% recall on this category held across the corpus, total recall would move
from 52/321 (16%) to roughly 102/321 (32%), and F1 from 25.9% to roughly 44%.
That is still short of the 68.8% F1 reported for the strongest published
baseline (an LLM agent with retrieval tools), and
it rests on an 18-case sample. It is a direction, not a result.

## Honest limits

- n=18. Eight positives. The confidence interval on 38% is very wide.
- The reader and the author of the checker are the same model, which is a
  structural conflict this design cannot resolve by itself. An independent
  grader would be better.
- Retrieval was not scoped to the pincite, which the failure analysis shows is
  the dominant error source. Fixing it should move recall materially, and until
  it is fixed these numbers understate what the layer can do.

---

# Second run: the holdings subset

**2026-09-02.** The first run (n=18) drew from the whole corpus and reached 75%
precision / 38% recall, with three of five misses traced to retrieval rather
than reading. This run isolates the subset where retrieval is not the
bottleneck.

## Why this subset

The corpus has two sources. 297 excerpts are OCR-converted appellate briefs.
90 are single-sentence holding statements from Dahl et al.'s *Large Legal
Fictions*, and **those 90 carry 77 of the 131 content-misrepresentation
labels** — 59% of the category. They are clean text, one proposition, one
citation:

> A lessee's right to remove fixtures from leased premises is subject to the
> rights of the lessor. *See Runyan v. Lessee of Coster*, 39 U.S. 122 (1840).

That is the ideal shape for grounded reading, and it is also the shape a real
AI-drafted brief takes.

## Result

n=20, read blind, 12 labelled misrepresentation and 8 clean, shuffled.

| | |
|---|---:|
| Precision | **86%** |
| Recall | **100%** |
| F1 | **92%** |
| Deterministic layer, same category | **0%** |

Twelve of twelve caught. The failures are stark once the case is actually
read: *Fleming v. Laws* cited for a due-process holding when the case is about
usury; *Carr v. First Nationwide Bank* cited for a lender's disclosure duty
when the case is an ERISA dispute; *Julce v. Mukasey* cited for the asylum
standard when it turns on whether a marijuana conviction is an aggravated
felony.

## The two false positives, which differ from each other

**Over-strictness.** *Puget Sound Tug & Barge* — the claim calls the government
vessel a tugboat when it was a Victory ship. The detail is wrong and the
holding is right, and the benchmark counts the citation clean. Flagging it was
a judgment error.

**Retrieval, not reading.** The citation `Gunder, 86 F.2d 1000` was resolved to
*General Theatres v. MGM*, whose entire text is "Appeal dismissed on
stipulations". The claim describes a tax holding. Reading that passage, the
mismatch is total — but the passage was the wrong case. The reading was correct
about what it was shown.

That second one is the same failure mode as three of the five misses in the
first run, from the opposite direction, and it is the single most valuable
thing these two runs have established: **the reading is not the weak link;
getting the right text in front of it is.**

## Projection, stated as a projection

Combining both runs — ~100% on the 77 holding-subset labels, ~38% on the 54
brief-subset labels — gives roughly 97 of 131 detections.

| | now | projected |
|---|---:|---:|
| Recall | 21.8% | **~51%** |
| Precision | 87.2% | ~86% |
| F1 | 34.9% | **~64%** |

The strongest published baseline is 76.1% / 62.8% / **68.8%**. That projection lands
close to it and does not pass it.

**This is a projection from n=20 and n=18, not a measurement.** Running the
layer over all 387 excerpts is what would settle it, and has not been done.
The confidence interval on 100% recall from twelve positives is wide, and the
brief-subset figure rests on the weaker of the two runs.

## What the evidence says to build next

1. **Scope retrieval to the pincited page.** Both runs point at retrieval as
   the error source. CAP's star pagination makes it possible.
2. **Verify the case identity before reading it.** The *Gunder* false positive
   would have been prevented by checking the resolved case name against the
   citation's own name before the passage was ever selected.
3. Only then, run the whole corpus.
