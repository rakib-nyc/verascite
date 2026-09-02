# v4 protocol

**Written 2026-09-01.** Four changes, all driven by reading the actual false
positives rather than by tuning. No threshold in `verascite/config.py` changed;
`THRESHOLDS_VERSION` remains `1`. The regression corpus passes unchanged.

Measured on the full corpus (387 excerpts, cache-served quotations):

| | v3 | v4 |
|---|---:|---:|
| False positives | 94 | **71** |
| Precision | 39.4% | **46.2%** |
| F1 | 26.1% | **27.5%** |
| True positives | 61 | 61 |
| FPR on correct-but-absent | 0.0% | **0.0%** |

No detections were traded away for the precision gain.

## 1. Agency acronyms — the largest single cause

Briefs write agency names as acronyms; CourtListener stores them spelled out.
`Landry v. F.D.I.C.` scored 0.20 against `Landry v. Federal Deposit Insurance
Corp.` and was reported as citing a different case.

Rather than maintain a table of agencies, an acronym is matched when its
letters are the initials of consecutive words on the other side. Dotted forms
are collapsed first (`F.D.I.C.` is one token, not four), because splitting them
into single letters hid them from the rule.

This does not loosen genuine mismatch detection: *Tinch v. Video Indus. Servs.*
against *Dolberry v. Jakob* still scores 0.00.

## 2. Non-case sources classified as invented reporters

eyecite parses `80 Fed. Reg. at 64,545` as a **case** short form. The Federal
Register is not in reporters-db, because it is not a case reporter — so a
correct citation to it was reported as a fabricated reporter, 13 times.

The reporter check now consults the journal, statute, and government-publication
lists before ever calling a reporter invented, and such citations are routed to
`OUT_OF_SCOPE` with a pointer to the issuing source.

## 3. Curly apostrophes and spacing variants

`Fed. App'x` — the Federal Appendix, a real reporter — was reported as invented
because OCR emits a typographic apostrophe where reporters-db stores a straight
one. Reporter keys now normalise apostrophes and match spacing variants, so
`ALA.L.REV.` finds `Ala. L. Rev.` too.

## 4. Quotation differences classified by kind

A near-miss is now classified as *typographic* (same words, different
punctuation or spacing — not a misquote), *omission* (function words added or
dropped, no word replaced — a human's call), or *substitution* (a word replaced
by another — the failure the benchmark is built around, and the only one
reported as a misquote).

This changed nothing on the benchmark, because the quotation false positives
there are dominated by attribution and by opinion text that could not be
retrieved. It is retained because it is correct, and because it is what
prevents OCR punctuation noise from reading as fabrication on real briefs.

## What remains

The false-positive composition is now: `case_name` 46, `quote` 22, `year` 3,
`quote_source` 1, `reporter_valid` 1. The remaining case-name failures are
mostly short forms resolved to the wrong antecedent and corporate-name
variants, which are a different problem from acronyms.

---

## Addendum: three changes that did not help

Recorded because a change that fails to help is as much a result as one that
does, and because reporting only what worked would misrepresent the search.

**Back-references inherit their antecedent's case-name verdict** rather than
computing one. Measurement motivated it: 27 of 46 false case-name failures were
short forms against 9 of 43 true ones. It changed nothing, because the short
forms and their antecedents were failing *together* — 21 of the short-form
false positives sit under a parent that also failed. The root cause is upstream
resolution, not the short form. The change is kept because one authority should
carry one verdict.

**Fuzzy token matching for spelling variants.** `Mikes v. Strauss` against the
reported `Mikes v. Straus` scored 0.50 and was called a different case. Tokens
of five or more characters now match at a 0.86 similarity ratio. Verified not
to loosen genuine detection: *Tinch* against *Dolberry* stays 0.00, *Roe v.
Wade* against *Doe v. Bolton* stays 0.00.

**Longer party-name walk-back.** `In re Application of the United States for
Historical Cell Site Data` is fourteen words; the ten-word cap captured only
`Cell Site Data`, which matched nothing.

Net effect on the corpus: false positives 71 → 70, true positives 61 → 60,
precision unchanged at 46.2%. All three are kept because they are correct on
real briefs -- a lawyer whose surname is misspelled by a reporter should not be
told their citation is fabricated -- but none of them is why precision improved.

The remaining 45 case-name false positives are dominated by CourtListener
returning a record whose name genuinely differs from the brief's, which is not
something this tool can adjudicate.
