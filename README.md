# VeraScite — legal citation verification that never guesses

**Detect fabricated, misattributed, and misrepresented case citations in legal briefs and AI-generated legal writing — with evidence for every finding.**

[![tests](https://github.com/rakib-nyc/verascite/actions/workflows/ci.yml/badge.svg)](https://github.com/rakib-nyc/verascite/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![Version](https://img.shields.io/badge/version-0.1.0-blue.svg)](CHANGELOG.md)

> **Independent research project.** Experimental software, released for research and
> evaluation. **No warranty of any kind. Every result requires human verification by a
> licensed attorney before it is relied on.** See [Scope and limits](#scope-and-limits).

---

## The problem

Courts have sanctioned lawyers in a growing number of matters for filing briefs containing
citations to cases that do not exist, quotations that were never written, and authorities
that say the opposite of what the brief claims. Generative AI produces citations that are
formatted perfectly and are entirely fictitious — and *format* is exactly what a human
reviewer uses to judge whether a citation looks real.

The failure is not only invention. In a corpus of real reported incidents, **the majority of
defects are not non-existent cases at all** — they are real cases cited for propositions
they do not support, and real cases quoted inaccurately. A tool that only asks *"does this
case exist?"* misses most of the problem.

## What VeraScite does

VeraScite extracts every citation from a brief, resolves each one against free public
caselaw archives, and checks it along independent dimensions — reporter validity, existence,
case name, court, year, precedential status, quotation accuracy, pincite, and whether the
authority supports the proposition it is cited for.

It produces an **evidence package**: for each finding, what was checked, what source was
retrieved, when it was retrieved, and the text that supports the conclusion.

### The design rule that governs everything

> **Absence of evidence is never evidence of fabrication.**

If VeraScite cannot find a case, it says `UNVERIFIED` and names the sources it consulted. It
does **not** say the case is fake. There is no `FABRICATED` verdict anywhere in this
codebase, by design.

This matters because the expensive error in legal practice is not a missed defect — it is a
**false accusation against a sound citation**. Recent decisions are routinely absent from
free archives. Unpublished dispositions, state trial court orders, and vendor-only
identifiers are absent by default. Treating absence as a finding would make the tool
unusable, and would put an attorney in the position of "correcting" a citation that was
right.

Every check that could fire on absence has been measured and, where it could not clear that
bar, **deliberately not built** — see [Measured and rejected](#measured-and-rejected).

---

## Results

Measured on a public benchmark of hallucinated legal citations: **387 scored excerpts,
1,334 citations, 321 labelled defects.** All figures below are measurements on the full
corpus, not projections or samples.

### Deterministic layer (no model, fully reproducible)

| Metric | Value |
|---|---:|
| Precision | **83.3%** |
| Recall | 24.0% |
| F1 | 37.3% |
| **False positives on citations absent from all sources** | **0 of 101** |

That last row is the design rule, measured: on 101 citations that no consulted source
contained, the tool raised **zero** fabrication findings.

### Deterministic + model-assisted reading

| System | Precision | Recall | F1 |
|---|---:|---:|---:|
| **VeraScite (full)** | **86.4%** | 59.2% | **70.2%** |
| Strongest published baseline | 76.1% | 62.8% | 68.8% |
| VeraScite (deterministic only) | 83.3% | 24.0% | 37.3% |

**Read this honestly:** VeraScite leads on precision by ~10 points and on F1 by 1.4 points.
**1.4 F1 points is roughly four citations out of 321** — ahead, but not by a margin anyone
should call decisive. Recall is ~3.6 points *behind* the baseline. The trade is deliberate:
in this domain a false accusation costs more than a miss.

> The baseline figure is taken from published benchmark results and has **not been
> independently reproduced here.** Treat the comparison accordingly.

### Recall by defect type (deterministic layer)

| Defect | Found | In corpus | Recall |
|---|---:|---:|---:|
| Non-existent citation | 31 | 32 | **96.9%** |
| Case name mismatch | 42 | 63 | 66.7% |
| Wrong pincite | 7 | 53 | 13.2% |
| Misquotation | 0 | 42 | 0.0% |
| Content misrepresentation | 0 | 131 | 0.0% (model layer: 88%) |

Reported precision is a **floor**. The benchmark labels only *injected* errors, so genuine
errors already present in the filed briefs score against the tool that finds them. Three
were verified by hand against a second archive — for example, `Vela v. City of Houston,
216 F.3d 659` is wrong; *Vela* is at **276** F.3d 659. Counting only those three verified
cases, precision is **87.7%**.

---

## Measured and rejected

Negative results are recorded as carefully as positive ones, because the interesting failures
share one cause: **a rare defect on a noisy channel, where a balanced test flatters a check
that collapses at the real base rate.**

| Approach | Available gain | Why it was rejected |
|---|---:|---|
| Flag a pincite when the proposition is absent from the cited page | 46 defects | Balanced sample: 77% recall / 77% specificity. At the true base rate (29 wrong vs 562 sound pincites) this yields **128 false accusations for 22 findings — 14.9% precision.** Absence promoted to a finding. |
| Flag a misquotation when text does not match the source | 42 defects | A **correct** quotation matches its source at median **0.929**, not 1.00, because both the brief and the archive are OCR output. Injected misquotes sit at 0.719. Every threshold was swept: precision peaks at **17.2%**. |
| Flag a short-form name that matches neither party | 4 defects | 40% precision — and the false alarms were cases where *lookup* landed wrong, not where the brief erred. |

Roughly **105 of the remaining misses sit behind measured rejections rather than unbuilt
work.** Further recall on this corpus needs a better text source — publisher-quality text
instead of scanned OCR — not a cleverer checker. Full protocols in
[`evals/`](evals/).

---

## Install

```bash
pip install verascite            # core
pip install "verascite[all]"     # + PDF and DOCX input
```

From source:

```bash
git clone https://github.com/rakib-nyc/verascite.git
cd verascite
pip install -e ".[dev]"
pytest -q                        # 295 tests, no network, no API key needed
```

### Optional: a CourtListener API token

VeraScite works with no credentials against the Caselaw Access Project. A free
[CourtListener](https://www.courtlistener.com/help/api/) token adds coverage of recent
decisions:

```bash
export COURTLISTENER_API_TOKEN="your-token"
```

The token is never written to reports, ledgers, cache keys, or logs — this is enforced by
tests (`tests/test_no_credential_leak.py`).

---

## Quick start

```bash
verascite brief.pdf --out ./audit
```

Produces `./audit/report.md` (the evidence package) and `./audit/ledger.json` (machine-readable).

```bash
# Local checks only. No citation string leaves the machine.
verascite brief.docx --out ./audit --offline

# Skip opinion-text retrieval (faster, fewer API calls)
verascite brief.md --out ./audit --no-quotes

# Resume an interrupted run from the existing ledger
verascite brief.pdf --out ./audit --resume
```

Exit code is `2` when anything is `FLAGGED`, `0` otherwise — so it drops into a
pre-commit hook or CI job unchanged.

### Python API

```python
from pathlib import Path
from verascite.run_audit import audit_document

ledger = audit_document(Path("brief.pdf"), out_dir=Path("./audit"))
# offline=True keeps every citation string on the machine

for entry in ledger:
    if entry.overall.value == "FLAGGED":
        print(entry.citation.raw_text)
        for name, check in entry.checks.items():
            if check.verdict.value == "FAIL":
                print(f"   {name}: {check.evidence}")
```

---

## Reading the output

VeraScite reports **five** document-level results. The distinction between the middle two is
the whole point of the tool.

| Result | Meaning | What to do |
|---|---|---|
| `VERIFIED` | Confirmed on every dimension checked | Nothing. Not a statement that it is still good law. |
| `FLAGGED` | **Contradicted by a source that was retrieved** | Fix before filing. Evidence is attached. |
| `UNVERIFIED` | **Absent from the sources consulted. NOT a finding of fabrication.** | Check manually. Routine for recent, unpublished, or vendor-only authorities. |
| `REVIEW` | Something needs a human read | Read it. |
| `PENDING` | Not yet checked | — |

`FLAGGED` requires affirmative contradiction from retrieved text. `UNVERIFIED` is what
absence produces. **They are structurally different states and are never merged.**

---

## Example use cases

### 1. Pre-filing check on a brief

```bash
verascite motion-to-dismiss.docx --out ./audit
```

The associate runs this before the partner reads the draft. `FLAGGED` items have retrieved
text attached, so verifying a finding takes seconds rather than a trip to a database.

### 2. Reviewing AI-assisted work product

A summer associate returns a research memo drafted with an AI assistant. Every citation is
perfectly formatted. Running VeraScite separates *"this reporter series has never existed"*
(a hard finding) from *"this 2025 decision is not in free archives yet"* (routine, not
suspicious) — a distinction format review cannot make.

### 3. Reviewing an opponent's filing

```bash
verascite opposing-msj.pdf --out ./audit
```

The command line runs the deterministic layer only — it ships with no model — so every
verdict is reproducible and defensible if you intend to raise an issue with the court.

### 4. CI for a legal-tech pipeline

```bash
# Exit code 2 means at least one citation was contradicted by a retrieved source.
verascite generated-memo.md --out ./audit --offline || exit 1
```

Fails the build on affirmative contradictions only — never on absence.

### 5. Research on citation hallucination

The benchmark harness, pre-registered protocols, and every negative result are in
[`evals/`](evals/). Runs are reproducible from cache.

More in [`examples/`](examples/).

---

## Using VeraScite inside an AI agent or assistant

VeraScite is designed to be called *by* an AI system as a verification step. The integration
contract is deliberately strict, and the reason is in
[Guardrails](#guardrails-for-agent-integration).

Full instructions: **[`docs/INTEGRATION.md`](docs/INTEGRATION.md)**

Minimal tool definition:

```json
{
  "name": "verify_citations",
  "description": "Verify legal citations in a document against public caselaw archives. Returns an evidence package. UNVERIFIED means absent from consulted sources, NOT fabricated.",
  "input_schema": {
    "type": "object",
    "properties": {
      "path": {"type": "string", "description": "Path to .pdf, .docx, .md, or .txt"},
      "offline": {"type": "boolean", "default": false, "description": "Local checks only; no citation leaves the machine"}
    },
    "required": ["path"]
  }
}
```

```python
from pathlib import Path

from verascite.run_audit import audit_document

def verify_citations(path: str, offline: bool = False) -> dict:
    """Return the ledger as a dict. UNVERIFIED != fabricated."""
    ledger = audit_document(Path(path), out_dir=Path("./audit"), offline=offline)
    return ledger.to_dict()
```

### Guardrails for agent integration

An agent consuming these results **must**:

1. **Never report `UNVERIFIED` as fabricated, fake, or hallucinated.** Say "not found in the
   sources consulted" and name them.
2. **Never upgrade a verdict.** A check may be downgraded by a later stage, never raised.
3. **Quote the evidence, not the conclusion.** Every `FLAGGED` finding carries retrieved text
   with a retrieval timestamp — surface it.
4. **Never present output as a certification.** It is an evidence package. Rule 11 and the
   applicable rules of professional conduct remain entirely with the filing attorney.

---

## How it works

```
  ingest ──► extract ──► resolve ──► verify ──► report
   PDF        eyecite    archives    9 checks   evidence
   DOCX       + spans    CAP / CL    monotonic  package
   MD/TXT                            verdicts
```

1. **Ingest** — PDF, DOCX, Markdown, or text, preserving character offsets so every finding
   points at a real location in the source.
2. **Extract** — citations, short forms and their antecedents, quotations, and the
   proposition each citation supports.
3. **Resolve** — against the [Caselaw Access Project](https://case.law/) (public domain, no
   key) and [CourtListener](https://www.courtlistener.com/) (free API).
4. **Verify** — nine independent dimensions. Verdicts are monotonic: a stage may downgrade a
   result, never upgrade one.
5. **Report** — Markdown evidence package plus a JSON ledger.

### Notable implementation details

- **Page-level pincite checking.** Pages are reconstructed from star pagination, so a wrong
  pincite is detectable even though it still falls inside the opinion.
- **Parallel reporter resolution.** Archives hold different reporters; a citation to one is
  reached through another. The pincite is deliberately *not* carried across — page 1045 of
  one reporter is not page 1045 of another.
- **Case-identity gate.** A proposition is never read against a case the citation does not
  name. Resolution is by volume/reporter/page, which lands on the wrong case when a page is
  off; reading it would produce a confident accusation about a sound citation.
- **Verbatim span interlock.** Any model-assisted answer whose supporting quotation is not
  verbatim in the retrieved opinion is discarded and can never become a `FAIL`.
- **Offset-preserving normalization**, so a line break inside a citation never shifts a span.

---

## Scope and limits

**This is experimental software from an independent research project. It is provided "as is",
without warranty of any kind. It does not provide legal advice.**

**Human verification by a licensed attorney is required before relying on any output.**

VeraScite does **not**:

- Certify any filing, or discharge any professional obligation.
- Act as a citator. **It cannot tell you whether an authority is still good law** — it does
  not detect overruled, reversed, vacated, abrogated, or superseded decisions.
- Cover statutes, regulations, legislative materials, or secondary sources. These are routed
  to manual review.
- Cover non-US authority.
- Guarantee that an `UNVERIFIED` citation is fake, or that a `VERIFIED` one is apt.

Known measured weaknesses, stated plainly: misquotation detection is **0%** on OCR-derived
text and pincite recall is **13.2%** in the deterministic layer. Both are documented above
with the measurements that produced them.

---

## Documentation

| Document | Contents |
|---|---|
| [`docs/INTEGRATION.md`](docs/INTEGRATION.md) | Full agent / pipeline integration guide |
| [`docs/OUTPUT.md`](docs/OUTPUT.md) | Verdict vocabulary and ledger schema |
| [`evals/`](evals/) | Benchmark protocols, results, and rejected approaches |
| [`examples/`](examples/) | Worked end-to-end examples |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Development setup and conventions |
| [`CHANGELOG.md`](CHANGELOG.md) | Version history |

## Citing this work

```bibtex
@software{islam_verascite_2026,
  author  = {Islam, Muhammad Rakibul},
  title   = {VeraScite: Transparent, Auditable Legal Citation Verification},
  year    = {2026},
  version = {0.1.0},
  url     = {https://github.com/rakib-nyc/verascite},
  license = {Apache-2.0}
}
```

## Author

**Muhammad Rakibul Islam** — <rakib.islam@rutgers.edu>

An independent research project. Not affiliated with, endorsed by, or representing any
employer, institution, court, or bar association.

## Acknowledgements

Built on open infrastructure from the [Free Law Project](https://free.law/) — `eyecite`,
`reporters-db`, `courts-db`, and the CourtListener API — and on the
[Caselaw Access Project](https://case.law/).

## License

[Apache License 2.0](LICENSE) © 2026 Muhammad Rakibul Islam

---

<sub>**Keywords:** legal citation verification · AI hallucination detection · fabricated case
citations · phantom citations · cite checking · legal brief validation · Rule 11 compliance ·
caselaw verification · CourtListener API · Caselaw Access Project · eyecite · legal tech ·
litigation technology · LLM hallucination · AI-generated legal writing · citation checker ·
open source legaltech</sub>
