# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
