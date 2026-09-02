# Pre-registered scoring protocol — LePhantomCite

**Written 2026-08-31, before any benchmark data was downloaded, inspected, or
scored.** Nothing below may be revised in light of results. If a rule proves
wrong, the correct response is to say so, publish the number this protocol
produced, and register a v2 for a separate run — not to edit this file and
re-report.

The reason for the ceremony: the mapping from this tool's verdict vocabulary to
the benchmark's binary label is not neutral. Several defensible mappings exist
and they produce materially different F1 scores. Choosing among them after
seeing the scores would make the number meaningless, and it would be very easy
to do accidentally.

## The mapping

The benchmark labels each citation `hallucinated` or `not hallucinated`. This
tool emits five overall verdicts. The mapping is:

| verascite verdict | Scored as | Rationale |
|---|---|---|
| `FLAGGED` | **hallucinated** | Affirmatively contradicted by a retrieved source. |
| `VERIFIED` | not hallucinated | Confirmed on every applicable dimension. |
| `REVIEW` | not hallucinated | Something needs a human read; the tool is not asserting a defect. |
| `UNVERIFIED` | **not hallucinated** | See below. Non-negotiable. |
| `PENDING` | not hallucinated | Nothing was checked; asserting a defect would be unfounded. |

Only `FLAGGED` counts as a positive detection.

## Why `UNVERIFIED` maps to "not hallucinated"

This is design principle P1, and it is the single decision this project is
least willing to trade for a score.

`UNVERIFIED` means the sources consulted do not contain the authority. Free
databases lack recent decisions, unpublished dispositions, state trial court
opinions, and vendor-only identifiers. Mapping `UNVERIFIED` to "hallucinated"
would mean telling a lawyer that a real case is fabricated, which is the error
that makes practitioners abandon these tools.

**The cost is known and accepted in advance.** Some genuinely fabricated
citations in the benchmark will be absent from CourtListener for ordinary
coverage reasons rather than because they are fake. Under this mapping those
score as false negatives. Recall will be lower than systems that flag on
absence. That is the trade being made deliberately, not a defect to be tuned
away afterwards.

For reference, the published comparison: Gemini 2.5 Flash reached 66.9% recall
while flagging 65.9% of known-good-but-absent citations as hallucinated. That
is not a better tool. It is a tool that would tell a lawyer two out of three
of their real citations are fake.

## Reported metrics

All of these are reported together. None may be omitted from a summary.

1. **Precision, recall, F1** on hallucination detection, overall.
2. **The same, broken out by the benchmark's hallucination type** — the
   per-category numbers are the point of the exercise, not the aggregate.
3. **False-positive rate on the known-good-but-absent subset** (the 132
   citations that are correct but return nothing from CourtListener). Reported
   with the same prominence as F1, in the same table, never in a footnote.
   **Target: under 10%.**
4. **Verdict distribution** — what fraction lands in each bucket, and whether
   `REVIEW` stays small enough to be actionable.
5. **Cost** — requests, wall clock, per citation and per document.
6. **Determinism** — the same excerpt scored three times must produce identical
   verdicts.

## Run discipline

- The benchmark is run **once** against this protocol. A re-run is permitted
  only for a mechanical failure (crash, network outage), not for a
  disappointing score.
- No threshold in `verascite/config.py` may be changed between reading these
  results and publishing them. `THRESHOLDS_VERSION` at time of run is recorded
  in the results file.
- Only the deterministic layer is under test. M5's model-assisted checks do not
  exist yet. This is deliberate: the question being answered is *how far does
  pure deterministic checking get you*, and that is only answerable before a
  model is in the loop.
- Citations the pipeline could not parse at all are counted as processed and
  scored `not hallucinated`, not dropped. Dropping them would silently inflate
  precision.

## What a "good" result looks like

Not a high F1. The strongest published baseline is 68.8% F1 with 76.1%
precision. A deterministic-only layer that reaches high precision with a low
false-positive rate on the known-good subset is a success even at substantially
lower recall, because the recall gap is exactly what M5 is for and the
precision is what makes the tool usable at all.

A result showing 0% recall on incorrect-pincite and content-misrepresentation
is expected and desirable to document precisely: those categories are what M5
must earn its place against.

---

# Addendum: span matching and row handling

**Written 2026-08-31, after inspecting the dataset's file format and label
schema, and before computing any score.** No prediction had been run and no
metric computed when this was written. It is appended rather than edited into
the text above so the order of events stays visible.

The protocol above fixed the verdict mapping but did not say how a flagged
citation is matched to a labelled one. That has to be settled before scoring,
and the dataset's actual schema differs from its README (parallel fields rather
than a single dict), so it could not have been written blind.

## The gold standard

The eval split holds 390 excerpts and 1,334 citations.

- A citation is **hallucinated** iff it appears as a key in the excerpt's
  `list_hallucination_types`. That yields 312 hallucinated citations carrying
  321 type labels — matching the dataset README's eval column exactly
  (non-existent 32, case-name mismatch 63, wrong pincite 53, misquote 42,
  content misrepresentation 131).
- Every other entry in `citations_in_segment` is a **negative**: 1,022 of them.
- Spans typed `optional` in `list_hallucinations` (22) are ignored. They are
  excluded from `list_hallucination_types` and from the README's counts.

## Matching a prediction to a label

All 312 hallucinated citation strings occur verbatim in their excerpt, so gold
spans are located by exact string search and compared by character offset.

- A gold citation is **detected** if any citation this tool marked `FLAGGED`
  has a character span overlapping the gold span.
- A `FLAGGED` citation overlapping no gold span is a **false positive**.
- Overlap, not containment, in either direction: this tool's spans include the
  case name (`Aves v. Shah, 997 F.2d 762`) where the dataset's often do not
  (`997 F.2d 762`).

`content_misrepresentation` labels attach to citations, but the defect lives in
the surrounding proposition. No credit is taken for flagging a citation for an
unrelated reason in an excerpt that happens to contain a misrepresentation.
Detection still requires span overlap. Expected recall on this category is 0.

## Rows without citation-level labels

24 excerpts lack `list_hallucination_types`. Of those, 21 are clean and 3 carry
`list_hallucinations` that cannot be attributed to a specific citation.

- The 21 clean excerpts are **scored**; their citations are negatives and
  contribute to the false-positive rate.
- The 3 unattributable excerpts are **excluded**, and the exclusion is reported
  alongside the results. Their citations are counted in neither numerator nor
  denominator. Scoring them either way would be a guess.

## The known-good-but-absent subset

The whitepaper cites 132 citations that are correct but return nothing from
CourtListener. That subset is not distributed as a labelled field. It is
therefore reconstructed as: **negatives (not in `list_hallucination_types`)
whose existence check returned `NOT_FOUND` or `OUT_OF_SCOPE`.** The
false-positive rate on that reconstructed subset is reported with the same
prominence as F1, as required above. Because it is reconstructed rather than
the authors' own subset, it is labelled as such and its size is reported.

## Configuration for the run

- `--no-quotes` is **not** set. Misquote is a scored category.
- Court enrichment via the dockets endpoint is **disabled** for this run. It
  costs one request per authority against a 5-per-minute ceiling, and it cannot
  change any binary label: a court verdict of `PASS` yields `VERIFIED` and one
  of `NOT_CHECKABLE` yields `REVIEW`, both of which map to *not hallucinated*.
  No scored category concerns the court.
- `THRESHOLDS_VERSION` at time of run is recorded in the results file.
