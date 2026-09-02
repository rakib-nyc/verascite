# v2 protocol

Supersedes nothing in `PREREGISTRATION.md`. The verdict mapping, the metrics
reported, and the run discipline are unchanged. This records exactly what
differs between v1 and v2, so the delta is attributable rather than merely
observed.

**Written 2026-08-31, after the v1 stopping decision was committed (`2af2138`)
and after the three defects were characterised, before v2 was run.**

## What changed: two code fixes

**1. Journals and statutory compilations are no longer reported as invented
reporters.** `reporters-db` keys journals by abbreviation (`Or. L. Rev.`) and
carries the spelled-out name as a value (`Oregon Law Review`). eyecite matches
the key only, so `84 Oregon Law Review 227` parsed as nothing, the shadow scan
claimed the span, and a real law review citation was reported as a fabricated
reporter. The shadow scan now checks both forms, plus `LAWS`, and emits such
spans as out-of-scope journal citations.

**2. Emphasis markers are neutralised at ingest, length-preservingly.** The
corpus is PDF-converted, and case names are conventionally italicised, so they
arrive as `*Heartland Regional Med. Ctr. v. Sebelius*`. The asterisk halted the
case-name walk-back, yielding `Regional Med.Ctr. v. Sebelius*` — first party
lost, marker retained. 225 of 390 excerpts contain asterisks and 807 spans are
asterisk-wrapped capitalised text, so this was the dominant parsing defect.

It caused errors in **both** directions, which is why it matters more than the
false-positive count suggested:

- *False positives*: a correct citation compared against a truncated name.
  `Heartland Regional Med.Ctr. v. Sebelius, 415 F.3d 24` scored 0.00 against
  the retrieved `Heartland Regional Medical Center v. Leavitt` and was
  FLAGGED. With the name intact it scores in the review band and is not
  flagged — which is right, since Leavitt and Sebelius are successive holders
  of the same office and re-captioning is ordinary.
- *False negatives*: a truncated name that still matched by token overlap,
  concealing a genuine mismatch. `*SEC v. Chenery Corp.*` lost its first party
  and matched anyway.

Asterisks are replaced with spaces rather than deleted, so every character
offset still addresses the original document.

## What changed: one scoring fix

**3. A flagged back-reference is attributed to its antecedent.**

`id.` and short forms inherit their antecedent's verdict, by design — a reader
who is told `Heartland, 566 F.3d 1` is wrong needs to know that the later
`id. at 197-98` is the same authority. The benchmark labels only the full
citation, so the tool's flag on the back-reference matched no gold span and
scored as a false positive. That is an artefact of comparing a
citation-graph-aware tool against span labels, not a defect in either.

For scoring, a flagged back-reference now contributes its antecedent's span,
and predictions are de-duplicated by span. A back-reference whose antecedent is
correctly flagged is therefore part of that one detection; one whose antecedent
is wrongly flagged is part of that one false positive. Neither is double
counted.

This is a change to measurement, not to the tool. The tool's behaviour is
unchanged and is believed correct.

## What did not change

- No threshold in `verascite/config.py`. `THRESHOLDS_VERSION` remains `1`.
- The verdict mapping. `UNVERIFIED` still scores as *not hallucinated*.
- The metrics reported, including the false-positive rate on the reconstructed
  correct-but-absent subset at equal prominence with F1.

## Run order

1. **The same 75 excerpts as v1, first.** The delta is then attributable purely
   to the three changes above. Changing the slice and the code together would
   confound them, which is the mistake this whole exercise exists to avoid.
2. Then the full corpus, resources permitting.

## Reporting

Recall is reported decomposed by hallucination category, always. A single
recall figure averages a category the deterministic layer can detect against
one it provably cannot, and that average is not meaningful. The honest
statement has the shape: *this is what deterministic checking achieves on
existence and metadata, this is the structural zero on content
misrepresentation, and this is the ceiling any non-model pipeline reaches.*
