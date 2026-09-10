# V9 — the case-name matcher confirmed fabricated names against unrelated cases

**2026-09-09.** Written after the change, against results already recorded.
Declared here because it alters the behaviour `NAME_PASS_THRESHOLD` and
`NAME_REVIEW_THRESHOLD` are applied to, which is governed by the same rule as
changing the thresholds themselves. **No value in `verascite/config.py`
changed.**

## How it was found

Not by the benchmark. By running the tool against 633 citations that courts
adjudicated to be fabricated (`evals/sanctioned/`), and asking why 49 of them
came back `VERIFIED`.

`State v. Pune, 94 Hawaii 200` is the clearest case. The order says the court
"found no such published opinion and Westlaw returned zero results." The tool
reported:

```
existence   PASS   CourtListener returned 14 candidates for '94 Hawaii 200'
case_name   PASS   case name matches after normalization (score 1.00)
OVERALL     VERIFIED
```

None of the fourteen candidates is named *Pune*. The one the tool settled on is
*State v. Ing*.

## The defect

`name_similarity` scored `State v. Pune` against `State v. Ing` at **1.00**.

Two mechanisms combined. `_overlap` normalises the matched-token count by
`min(len(left), len(right))`, which makes it a *containment* measure rather
than a symmetric one. And the distinctive-token filter keeps only tokens of
four characters or more, so `{state, ing}` reduces to `{state}` — which is
contained in `{state, pune}`, and containment scores 1.00.

The same shape appeared through the archive's `case_name_short` field, which
is frequently a bare caption abbreviation. `Com v. Reid` scored 1.00 against
`Com.`, and the record at `770 A.2d 771` is *Commonwealth v. Burton*.

**Why the benchmark never caught it.** Its citations carry years and courts, and
the metadata stage uses those to disambiguate. Strip them — as a bare citation
in a sanctions order does — and the case name is doing all the work alone.

**Scale of what it affects.** Criminal and government captions — `State v.`,
`People v.`, `Commonwealth v.`, `United States v.`, `In re` — are an enormous
share of American case law. Any of them with a short distinctive surname (Ing,
Doe, Roe, Lee, Ho, Ng, Kim) could be confirmed against any unrelated decision
printed at the same page.

**Direction of the error.** It is a *recall* defect, not a false-accusation
defect: it made the tool report `VERIFIED` where it should have reported a
mismatch. No citation was wrongly accused because of it. That is why it
survived undetected — the project's tests attack false accusation hardest.

## The change

One rule, applied before containment:

> If everything two names have in common is caption boilerplate — `State`,
> `People`, `Com.` — then they agree on nothing that identifies a case, and the
> score is capped at `GENERIC_ONLY_CEILING = 0.30`.

Supporting pieces:

- `GENERIC_PARTIES`: party words that name a *side* of a case without naming
  the case, including the abbreviated caption forms archives store.
- `_shared_tokens`: the tokens two names have in common, **fuzzy**, so a
  spelling difference (`Ziglar` / `Zigler`) is still a match and never becomes
  an accusation.
- Identical token sets short-circuit to 1.00 first, so `In re Doe` against
  `In re Doe` is not called a mismatch merely because neither name is
  distinctive.

0.30 sits below `NAME_REVIEW_THRESHOLD` (0.34), so the result is an affirmative
mismatch rather than a borderline one. That is the correct reading: the
retrieved record names a different case.

## Verification

| Pair | Before | After | Correct |
|---|---:|---:|---|
| `State v. Pune` / `State v. Ing` | 1.00 | **0.30** | different |
| `Com v. Reid` / `Com.` | 1.00 | **0.30** | different |
| `Com v. Reid` / `Commonwealth v. Burton` | 0.00 | 0.00 | different |
| `People v. Smith` / `People v. Rodriguez` | 0.50 | **0.30** | different |
| `United States v. Braswell` / `United States v. Petrofac` | 0.50 | **0.30** | different |
| `Twombly` / `Bell Atlantic Corp. v. Twombly` | 1.00 | 1.00 | same, short form |
| `State v. Smith` / `Smith` | 1.00 | 1.00 | same, short form |
| `Ashcroft v. Iqbal` / `Iqbal` | 1.00 | 1.00 | same, short form |
| `Ziglar v. Abbasi` / `Zigler v. Abbasi` | 1.00 | 1.00 | same, misspelled |
| `In re Doe` / `In re Doe` | 1.00 | 1.00 | same |
| `United States v. Ing` / `United States v. Ing` | 1.00 | 1.00 | same |

**Regression corpus: 25 tests, all passing, no locked verdict moved.** Full
suite 447 → 455 with the new cases.

## Measured effect on the sanctioned corpus

| | before | after |
|---|---:|---:|
| Scored | 484 | 484 |
| Flagged | 226 | **239** |
| Share flagged | 46.7% | **49.4%** |
| `VERIFIED` | 49 | 46 |
| `REVIEW` | 63 | 53 |
| Vendor-only reported `UNVERIFIED` | 149 / 149 | **149 / 149** |

Thirteen more citations correctly flagged, and the governing rule held
unchanged: every vendor-only identifier still reports `UNVERIFIED`, none
flagged.

## Effect on the published benchmark figures — not measured

The deterministic figures on record (83.3% precision, 24.0% recall, 37.3% F1)
were produced with the defective matcher. This change can only convert some
`case_name` `PASS` into `FAIL`, so it should raise recall on the name-mismatch
class and can only lower precision if a *correct* name is now called a
mismatch — which the table above is designed to rule out and the regression
corpus did not observe.

**The benchmark was not re-run for this release**, so the headline figures are
unchanged and remain the pre-fix measurement. They are therefore conservative
with respect to this change. Re-running it is the obvious next protocol.
