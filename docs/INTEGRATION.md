# Integrating VeraScite

VeraScite is built to be called *by* another system — an AI assistant, a document-management
workflow, or CI. This document is the full contract.

> **Read this first.** VeraScite is experimental research software with no warranty. It does
> not certify filings and does not replace review by a licensed attorney.

---

## 1. The one rule a caller must not break

> **`UNVERIFIED` does not mean fabricated.**

`UNVERIFIED` means *the sources consulted did not contain this authority*. That is routine
for recent decisions, unpublished dispositions, state trial court orders, and vendor-only
identifiers.

A caller that renders `UNVERIFIED` as "fake", "hallucinated", or "does not exist" converts a
neutral observation into a false accusation against what is very often a perfectly sound
citation. **This is the single most damaging way to misuse this tool.**

| Verdict | Correct phrasing to a user | Never say |
|---|---|---|
| `FLAGGED` | "Contradicted by the retrieved opinion: …" | — |
| `UNVERIFIED` | "Not found in the sources consulted (CourtListener, CAP). Verify manually." | "fabricated", "fake", "hallucinated", "does not exist" |
| `REVIEW` | "Needs a human read: …" | "failed" |
| `VERIFIED` | "Confirmed on the dimensions checked." | "valid", "good law", "safe to file" |

---

## 2. Install and invoke

```bash
pip install "verascite[all] @ git+https://github.com/rakib-nyc/verascite.git"
export COURTLISTENER_API_TOKEN="..."   # optional; adds recent-decision coverage
```

Full instructions, including Windows: [`INSTALL.md`](INSTALL.md).

### Python

```python
from pathlib import Path
from verascite.run_audit import audit_document

ledger = audit_document(
    Path("brief.pdf"),
    out_dir=Path("./audit"),
    offline=False,      # True = no citation string leaves the machine
    quotes=True,        # False = skip opinion-text retrieval (faster)
    quiet=True,
)

for entry in ledger:
    if entry.overall.value == "FLAGGED":
        for name, check in entry.checks.items():
            if check.verdict.value == "FAIL":
                print(entry.citation.raw_text, "->", name, ":", check.evidence)
```

### Command line

```bash
verascite brief.pdf --out ./audit
```

Exit codes: `0` clean, `2` at least one `FLAGGED`.

---

## 3. Tool definition for an AI assistant

```json
{
  "name": "verify_citations",
  "description": "Verify legal citations in a document against public caselaw archives. Returns an evidence package with a verdict per citation. IMPORTANT: a verdict of UNVERIFIED means the citation was not found in the sources consulted -- it does NOT mean the citation is fabricated. Recent, unpublished, and vendor-only authorities are routinely UNVERIFIED.",
  "input_schema": {
    "type": "object",
    "properties": {
      "path": {
        "type": "string",
        "description": "Path to the document (.pdf, .docx, .md, .txt)"
      },
      "offline": {
        "type": "boolean",
        "default": false,
        "description": "Local checks only; no citation string leaves the machine"
      }
    },
    "required": ["path"]
  }
}
```

Handler:

```python
from pathlib import Path
from verascite.run_audit import audit_document

def verify_citations(path: str, offline: bool = False) -> dict:
    ledger = audit_document(Path(path), out_dir=Path("./audit"), offline=offline)
    return ledger.to_dict()
```

---

## 4. System-prompt text for an agent

Paste this into the system prompt of any assistant that calls the tool:

```text
You have a citation verification tool. When you report its results:

1. NEVER describe an UNVERIFIED citation as fabricated, fake, hallucinated, or
   non-existent. Say it was not found in the sources consulted, and name them.
   Absence from a free archive is normal for recent, unpublished, and
   vendor-only authorities.

2. Only FLAGGED means a source was retrieved and contradicts the document.
   Quote the retrieved evidence, not just the conclusion.

3. Never upgrade a verdict. If the tool says REVIEW, it is REVIEW.

4. Never present the output as a certification, a clearance, or legal advice.
   It is an evidence package. Responsibility under Rule 11 and the applicable
   rules of professional conduct remains with the filing attorney.

5. The tool is not a citator. It cannot tell anyone whether an authority is
   still good law. Do not imply that it can.
```

---

## 5. Reading the ledger

`ledger.json`:

```jsonc
{
  "schema_version": "...",
  "document_path": "brief.pdf",
  "created_at": "2026-09-02T...Z",
  "sources_available": ["reporters_db", "courts_db", "courtlistener_citation_lookup"],
  "counts": { "FLAGGED": 2, "UNVERIFIED": 3, "REVIEW": 1, "VERIFIED": 5 },
  "entries": [
    {
      "citation_id": "cite_0002",
      "citation": { "raw_text": "...", "kind": "full_case", "location": {"char_start": 0} },
      "overall": "FLAGGED",
      "checks": {
        "quote": {
          "verdict": "FAIL",
          "evidence": "altered quotation. opinion says 'do', brief says 'suffice'.",
          "sources_consulted": ["courtlistener_opinion_text"]
        }
      },
      "resolved_to": { "case_name": "...", "url": "https://www.courtlistener.com/..." }
    }
  ]
}
```

### Invariants a caller can rely on

- A `FAIL` **always** carries `evidence` naming retrieved text. Enforced at construction.
- A `NOT_FOUND` **always** carries `sources_consulted`. Enforced at construction.
- Verdicts are **monotonic**: a later stage may downgrade a check, never upgrade it.
- There is **no `FABRICATED` verdict** anywhere in the codebase.

---

## 6. Rate limits and etiquette

The CourtListener API is a free public service run by a non-profit. VeraScite respects three
limits, two of which are undocumented and were found by measurement:

| Limit | Value |
|---|---|
| Citations per minute | 60 |
| Requests per minute | 5 |
| Requests per day | 125 |

Results are cached on disk. Re-running against the same brief costs nothing. Please do not
remove the limiter.

The Caselaw Access Project static archive has no key and no rate limit.

---

## 7. Credential handling

The API token is never written to reports, ledgers, cache keys, or logs. This is enforced by
`tests/test_no_credential_leak.py`, and the unit suite blocks network access at the socket
layer so tests cannot silently reach the network or use a real token.

Supply the token by environment variable (`COURTLISTENER_API_TOKEN`) or the `token=`
argument. Do not commit it.
