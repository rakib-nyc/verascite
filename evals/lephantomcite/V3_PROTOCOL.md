# v3 protocol

**Written 2026-09-01.** Follows `V2_PROTOCOL.md`. Two code changes and one
correction to the v2 scoring rule. No threshold in `verascite/config.py`
changed; `THRESHOLDS_VERSION` remains `1`.

## Correction to a v2 scoring rule

**v2 item 3 was wrong and is withdrawn.** It collapsed a flagged
back-reference onto its antecedent's span. The reasoning — that `id.` inherits
its antecedent's verdict, so scoring it separately double-counts — was sound,
but the implementation lost real detections, because **the benchmark labels
short forms too**.

Concretely: excerpt 2 carries two labels, `wrong_pincite` on
`755 N.E.2d 589, 591` and `misquote` on `755 N.E.2d at 598`. The tool detected
the misquote correctly, on the short form, with a clean diff
(`opinion says 'protection of', brief says 'protect'`). Collapsing that
detection onto the antecedent's span turned one correct finding into one missed
misquote *and* one false positive.

The corrected rule keeps each flagged citation's own span for matching, and
avoids double counting at the false-positive step instead: a flagged
back-reference whose antecedent was itself matched is part of that one
detection, not a separate error.

This is worth stating plainly because the v2 rule was introduced to fix a
measurement artefact and introduced a worse one. Measured on the same 75
excerpts, the correction moved misquote recall from 0/9 to 2/9.

## Code change 1: possessives and OCR apostrophes

Normalisation replaced apostrophes with a space, turning `plaintiff's` into
`plaintiff s`, which then failed to match an opinion reading `plaintiffs` and
was reported as an altered quotation. OCR'd brief text and clean opinion text
disagree about possessives constantly, and that disagreement is typographic.
Apostrophes are now deleted rather than spaced.

## Code change 2: quotations inside string cites are not attributable

A quotation followed by `A v. B, 1 F.3d 1; C v. D, 2 F.3d 2` cannot be
attributed to one of them by proximity. Checking it against the wrong opinion
produces a misquote finding that is not one.

A competing citation is now identified structurally rather than by distance:
only a citation joined to the winner by string-cite punctuation (`;` `,`
whitespace, nothing else) competes. A citation in the following sentence does
not. Distance alone was tried first and was too aggressive — it discarded
quotations whose owner was unambiguous because an unrelated citation happened
to sit nearby, which the regression corpus caught.

## Result on the v2 slice (75 excerpts, cache-served)

| | v2 | v3 |
|---|---:|---:|
| Precision | 20.4% | **25.0%** |
| Recall | 20.4% | **25.0%** |
| F1 | 20.4% | **25.0%** |
| misquote recall | 0/9 | **2/9** |
| case name mismatch | 7/7 | 7/7 |
