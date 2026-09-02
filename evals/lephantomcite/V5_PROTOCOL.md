# v5 protocol — precision rebuild

**Written 2026-09-01.** The first change set driven by external research rather
than by reading our own failures alone. `THRESHOLDS_VERSION` moves from `1` to
`2`; the reason is below, and the regression corpus was re-run deliberately.

## Result

Full corpus, 387 excerpts, 1,334 citations, cache-served quotations:

| | v4 | v5 |
|---|---:|---:|
| Precision | 46.2% | **61.6%** |
| False positives | 70 | **33** |
| Recall | 19.2% | 17.0% |
| True positives | 60 | 53 |
| FPR on correct-but-absent | 0.0% | **0.0%** |

Precision up 15.4 points, false positives cut by 53%, at a cost of 2.2 points
of recall. The trust metric is unchanged.

## What the research changed

Two literature findings reframed the problem, and both are now reflected here.

**The state of the art is one agentic system.** On this benchmark the only
system above 50% precision is an LLM agent with retrieval tools, at 76.1% P / 62.8% R
(Liu, Stammbach & Henderson, arXiv:2606.21155). Every other system reported —
GPT-5 agentic at 40.8%, Gemini 2.5 Flash at 16.9% — sits below where our
deterministic layer now is. That does not make 61.6% good; it means the
remaining gap is a model-in-the-loop gap, not a matcher gap.

**Everyone shipping to lawyers uses three states.** BriefCatch RealityCheck
(Green/Yellow/Red), Clearbrief, and CiteTracer (Real/Potential/Hallucinated,
97.1% accuracy) independently converged on it. Our vocabulary already has it,
which is why our false-positive rate on correct-but-absent citations is 0.0%
where Gemini's is 65.9% — that rate is the whole difference between those two
systems' precision, and it is the one thing we already had right.

## Change 1: case-name matching, rebuilt (`verascite/names.py`)

The largest false-positive source. Measured over the 952 name comparisons the
corpus produces, of which 44 are genuine mismatches:

| matcher | false alarms | genuine caught |
|---|---:|---:|
| literal token overlap (v4) | 174 | 43/44 |
| + Table T6 / T10 abbreviations | 95 | 43/44 |
| + ordered acronym expansion | 38 | 43/44 |
| + containment and surname weighting | **26** | 42/44 |

- **Standard abbreviation tables are openly available** and ship inside
  `reporters-db` as `CASE_NAME_ABBREVIATIONS` (189 entries) and
  `STATE_ABBREVIATIONS` (50). The hand-written table they replace covered 18.
  Missing entries included `Bhd.`, `Auth.`, `Cmty.`, `Cnty.`, `Envtl.`,
  `Fed'n`, `Sec'y`, `Gov't`. `Messner Manor Assocs. v. Wis. Hous. & Econ. Dev.
  Auth.` against `Messner Manor Associates v. Wisconsin Housing and Economic
  Development Authority` scored 0.25 and is now 1.00.
- **Ordered acronym expansion.** `Wyoming v. USDA` against the spelled-out
  agency. Matching happens against the *ordered* words before noise removal,
  because dropping "of" first destroys the sequence `USDA` must match.
- **Containment and surname weighting.** Briefs shorten captions; the reporter
  keeps them whole, and stores first names briefs never use.

## Change 2: all three CourtListener name fields

A cluster carries `case_name`, `case_name_short` and `case_name_full`, and they
are genuinely different strings — they disagree on roughly 71% of records, and
some have an empty `case_name`. A caption renamed on appeal, a consolidated
case, or a party substitution can leave the brief matching one and not the
others. All three are compared and the best match wins.

## Change 3: eyecite truncates party names at apostrophes

`eyecite/helpers.py` strips lowercase stop-words from the plaintiff with
`re.sub(r"\b[a-z]\w*\b", "", plaintiff)`. An apostrophe is a word boundary, so
the `l` of `Nat'l` and the `n` of `Ass'n` are removed as if they were words:

    "Nat'l Ass'n of Mfrs. v. Dep't of Def."  ->  plaintiff "Nat' Ass'  Mfrs."

Contracted abbreviations are pervasive in institutional party names, so this
silently corrupted a large share of comparisons before they were made. This is
upstream issue #296. Rather than patch a dependency, the name is read out of
the citation's own span, which this tool already computes accurately — but only
when eyecite's version shows the truncation signature or is missing a party,
and only when the span yields a complete caption. Preferring the span
unconditionally was measured and is worse (56.0% vs 61.6%), because a span that
begins a word late yields one party.

## Change 4: a single party name cannot carry a FAIL

`Grigsby` against `In re Miracle Church of God in Christ` was a confident
mismatch. One surname against a full caption is weak evidence in both
directions — the brief may have named the other party, or a party the caption
drops. Comparisons where either side reduces to fewer than two distinctive
tokens now report `NOT_CHECKABLE`.

## Change 5: court-and-year parentheticals are not party names

eyecite sometimes reports the contents of a trailing parenthetical as a party —
`9th Cir. 1996` — which then "mismatched" a real caption.

## Threshold change: NAME_PASS_THRESHOLD 0.75 -> 0.60

The old threshold was calibrated against the old matcher. With canonical
abbreviation folding, same-case pairs now score at or near 1.00 rather than in
the 0.2–0.4 band, so the boundary sits in a different place. Swept:

| threshold | false alarms | genuine caught |
|---|---:|---:|
| 0.75 | 43 | 43/44 |
| 0.60 | 38 | 43/44 |
| 0.50 | 26 | 42/44 |

0.60 keeps every detection but one and is where the curve bends. 0.50 buys 12
fewer alarms for one more missed mismatch; it was not taken, because a missed
mismatch is a citation nobody checks and an alarm is a citation somebody reads.

## Not adopted, and why

- **Caselaw Access Project as a second source.** The clearest remaining
  precision lever in the literature: CiteTracer's ablation shows removing
  secondary sources collapses Real-class F1 from 97.0 to 31.4, and
  CourtListener/CAP disagreement is a strong signal for the unverifiable class
  rather than the hallucinated one. Not built here — it is a new integration,
  not a tweak.
- **Conformal risk control for threshold calibration.** Would replace hand-swept
  thresholds with a distribution-free guarantee that precision on emitted flags
  is at least 1−α. The feasibility test also states honestly how much
  abstention a precision target costs. Deferred.
- **Fellegi–Sunter scoring with learned m/u probabilities** (e.g. `splink`), so
  that agreement on "Smith" counts for less than agreement on "Zubaydah".
  Deferred.
