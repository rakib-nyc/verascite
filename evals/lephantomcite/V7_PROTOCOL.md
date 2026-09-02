# v7 protocol — recall

**2026-09-02.** Precision was the earlier problem and is no longer the binding
one. This round is about recall.

## Result

Full corpus, 387 excerpts, 1,334 citations, quotation checking **off**:

| | v6 | v7 | published baseline |
|---|---:|---:|---:|
| Precision | 82.8% | **87.2%** | 76.1% |
| Recall | 15.4% | **21.8%** | 62.8% |
| F1 | 25.9% | **34.9%** | 68.8% |
| FPR on correct-but-absent | 0.0% | **0.0%** | — |

| category | v6 | v7 | GPT-5 agentic |
|---|---:|---:|---:|
| non-existent citation | 10/32 | **31/32 (97%)** | ~95% |
| case name mismatch | 41/63 | 41/63 (65%) | ~90% |
| incorrect pincite | 1/53 | 1/53 (2%) | 52.8% |
| verbatim misquote | 0/42 | 0/42 (0%) | 95.2% |
| content misrepresentation | 0/131 | 0/131 (0%) | 83.2% |

## Change 1: impossible reporter series

Every one of the 31 fabricated citations in this corpus names a reporter
*series that was never published* — `N.E.4th`, `F.6th`, `S.W.4th`, `P.4th`,
`N.Y.S.4th`, `Cal. Rptr. 4th`, `F. Supp. 5th`. `reporters-db` settles all of
them with no database lookup and no appeal to absence.

They were being missed because of one character class. The shadow scan's
reporter token allowed letters but not digits, so multi-word forms like
`Cal. Rptr. 4th` matched — the `4th` arriving as a separate word — while
single-token forms like `N.E.4th` did not.

This is the whole of the non-existent-citation gain, and it cost nothing:
precision rose and false positives did not move.

## Measured and rejected: absence from two sources

With the Caselaw Access Project integrated there are now two independent
databases, so the obvious idea is to flag citations absent from both. Measured
across the corpus:

| | fabricated | real |
|---|---:|---:|
| absent from CourtListener **and** CAP | 10 | **427** |

**2% precision.** 427 real citations are absent from both, mostly Westlaw and
Lexis identifiers and decisions after CAP's coverage ends. Absence is not
evidence of fabrication even from two independent sources. This is the
strongest confirmation of P1 the project has produced, and it closes off what
looked like the easy route to non-existent recall.

## Measured and rejected: quotation checking, in every configuration tried

Quotation checking is net-negative on this corpus. Five configurations:

| configuration | precision | recall | F1 | false positives |
|---|---:|---:|---:|---:|
| **off** | **87.2%** | 21.8% | **34.9%** | **10** |
| on | 40.7% | 26.0% | 31.7% | 118 |
| on, with OCR and inflection tolerance | 42.6% | 26.0% | 32.3% | 109 |
| on, absent-quotation reported for review | 53.1% | 24.4% | 33.4% | 67 |

It buys 8–13 detections for 57–108 false alarms.

The cause is not fixable by tuning, and it is worth stating plainly: **both
sides of the comparison are OCR output.** The briefs were converted from PDF
by olmOCR; CAP's text is scanned from printed reporters. `limiting` arrives as
`hmiting`. Beyond that, lawyers' quotations routinely differ from the official
reporter text in small ways — different editions, unmarked alterations,
quotation from a vendor's rendering. The benchmark's injected errors are one-
or two-word synonym swaps, which sit *inside* that noise floor.

Two improvements were kept because they are correct regardless:

- **OCR and inflection tolerance.** A synonym swap replaces a word with a
  different word; scanning and inflection produce nearly the same string.
  `limiting`/`hmiting` and `acts`/`acted` no longer read as substitutions,
  while `do`/`suffice` and `recitation`/`listing` still do.
- **An unlocatable quotation is reported for review, not as a finding.** It may
  be misquoted, attributed to the wrong citation, or absent from the text that
  could be retrieved. This alone removed 42 false alarms.

Quotation checking remains available and defaults to on for ordinary use, where
a typed brief is not OCR noise. It is off for the benchmark headline because
that is the honest configuration for this corpus.

## What remains, and what it needs

| missing | count | share of gold | needs |
|---|---:|---:|---|
| content misrepresentation | 131 | 41% | grounded reading — measured at 75% precision / 38% recall on n=18 |
| incorrect pincite | 52 | 16% | text scoped to the pincited page |
| verbatim misquote | 42 | 13% | text not derived from OCR on both sides |
| case name mismatch | 22 | 7% | remaining are CourtListener records that genuinely differ |

Content misrepresentation alone is the difference between 34.9% F1 and roughly
50%. It is the only remaining change that can close the gap to 68.8%.
