# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] — 2026-09-09

The model-assisted layer becomes reachable from the command line, statutory and
regulatory citations leave `OUT_OF_SCOPE`, and the run produces a record shaped
to the obligation courts are actually imposing.

### Added

**Grounded reading, from the command line.** The layer that reads a retrieved
opinion and judges whether it supports the proposition it was cited for existed,
was tested, and was unreachable outside a host application. It now runs from
`--model`, with three backends: `local` (a loopback endpoint — no citation,
proposition, or opinion text leaves the machine, enforced rather than promised),
`api` (any endpoint speaking the chat-completions JSON interface), and `command`
(a subprocess reading a prompt on stdin). `--model-param KEY=VALUE` passes
backend-specific options through, so no vendor's option table is baked in.
Cost and latency are reported per run; money only when the user supplies rates.

**Verification record.** `verification-record.md`, written alongside the report.
Binds the run to a SHA-256 of the exact file reviewed; states timestamp, tool
version and threshold-set version; lists every citation with its result, the
sources consulted for it, and when they were retrieved; enumerates what was
**not** examined, good-law status first and before the results; and leaves the
attorney's attestation block deliberately blank. It is evidence of inquiry, and
it says in its first lines that it is not a certification.

**Statutes and regulations.** Previously `OUT_OF_SCOPE` in their entirety. Now
parsed into title, section and subsection path, and checked against free federal
sources that need no credential: the government link service, the eCFR versions
endpoint, and the official structural text of the US Code. The check this
enables is a **fabricated subsection of a real statute** — `42 U.S.C. § 1983(a)(2)`,
where § 1983 has no subsections at all — which is damaging and close to
invisible to a human reviewer, because everything before the parenthesis is
correct. `--download-code-titles` opts in to the ~18MB structural text a
subsection check requires; without it the dimension reports unchecked rather
than guessing.

**Document-calibrated quotation agreement.** Flags a quotation that agrees with
its source markedly worse than the rest of the same document's quotations do.
Emitted only as `REVIEW`, never as a finding. Replaces a fixed-threshold check
that was measured at 17.2% precision and rejected in v0.1.0.

**Batch review.** `--batch` reviews every document under a directory and writes a
consolidated report ordered most-severe-first. Sequential and cache-sharing, because
the archives' rate limits are shared across the batch. A document that cannot be read
is recorded as unexamined and named in the summary, never reported as clean.

**External validation corpus.** 633 citations that courts adjudicated to be
fabricated, extracted from a public database of sanctions decisions under CC BY,
with provenance pinned by hash.

### Measured

- **Backend choice dominates the model layer's results.** On the same samples a small
  4B local model reached **5.6% recall against the 82.7%** of the reader used in the
  original evaluation. Under a weak reader the tool degrades toward silence rather than
  toward false accusation: one false positive in 35, and the verbatim span interlock
  rejected a further reading that would have been a false accusation on a clean citation.
  See `evals/backends/RESULTS.md`. The published 88.5% / 82.7% figures are a property of
  that reader on that sample, not of the tool.
- **Statutory subsection checking**, verified against the official structural
  text of title 42 at release point 119-103: real subsections confirmed,
  fabricated ones reported absent, including both canary cases on § 1983.
- **Quotation agreement, calibrated:** 80–91% precision at 35–56% recall across
  simulated documents, against a 17.2% ceiling for the best fixed threshold. The
  gate declined 28 of 29 documents at the highest disagreement level rather than
  guessing. Evidence is synthetic disagreement; see `evals/misquote/RESULTS.md`.
- **The archive's scanning flag is not the variable** in misquote detection, as
  the roadmap had assumed. Scanned and non-scanned subsets behave almost
  identically; what decides the outcome is disagreement between the document and
  the archive. The planned `extracted_by_ocr` gate would not have worked.
- **External validation, credential-free subset:** 8.9% detection on 484 scored
  citations. Existence resolution did not run for want of a credential, so this
  is not the tool's recall on that corpus. See `evals/sanctioned/PROTOCOL.md`.
- **All 149 vendor-only citations in that corpus were reported `UNVERIFIED`,
  none flagged** — the governing rule holding on citations that really were
  fabricated, with a judicial finding to prove it.

### Changed

- The report marks every verdict a model produced, and prints the backend, the
  model, whether text left the machine, what it cost, and a plain statement that
  a reading is not reproducible.
- Without a model configured, the report now says outright that no citation's
  substance was examined, rather than listing the dimension as merely unchecked.
- Opinion text fetched for quotation checking is reused for grounded reading, so
  a cluster is never fetched twice in one run.

### Notes

- 431 tests, up from 295. The unit suite still blocks network at the socket layer and
  still fails if a live credential is present.
- `--deterministic-only` remains the default. No model runs unless asked for.
- No threshold in `verascite/config.py` changed in this release.
- The published comparison baseline (76.1 / 62.8 / 68.8) still has **not** been
  independently reproduced in this project.

## [0.1.0] — 2026-09-02

First public release. Experimental research software.

### Added
- Deterministic verification across nine dimensions: reporter validity, existence, case
  name, court, year, precedential status, quotation, quotation attribution, and pincite.
- Page-level pincite verification using reconstructed star pagination, so a wrong pincite is
  detectable even when it still falls inside the opinion.
- Parallel-reporter resolution: a citation to a reporter one archive lacks is reached through
  another reporter of the same decision. Pincites are deliberately not carried across.
- Case-identity gate: a proposition is never read against a case the citation does not name.
- Verbatim span interlock on all model-assisted reading; an answer whose supporting
  quotation is not verbatim in the retrieved opinion is discarded and can never become
  a `FAIL`.
- `audit_document()` public library API.
- Markdown evidence package and JSON ledger output; annotated `.docx` output.
- 295 tests, including property-based tests and adversarial input tests. The unit suite
  blocks network at the socket layer and fails if a live credential is present.

### Measured
- Deterministic layer: 83.3% precision, 24.0% recall, 37.3% F1 on a 387-excerpt public
  benchmark of hallucinated citations.
- Zero false positives on 101 citations absent from every consulted source.
- With the model-assisted layer: 86.4% precision, 59.2% recall, 70.2% F1.
- Non-existent citation detection: 96.9% recall.

### Deliberately not built
- Pincite inference from absence on the cited page — 14.9% precision at the true base rate.
- Quotation-mismatch flagging on OCR-derived text — precision peaks at 17.2%.
- Short-form name flagging on single-party names — 40% precision.

Each rejection is documented with the measurement that produced it in `evals/`.

### Known limits
- Misquotation detection is 0% against OCR-derived archive text.
- Pincite recall is 13.2% in the deterministic layer.
- Not a citator; does not detect overruled or superseded authority.
- US case law only. Statutes, regulations, and secondary sources route to manual review.

[0.1.0]: https://github.com/rakib-nyc/verascite/releases/tag/v0.1.0
