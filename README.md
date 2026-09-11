# VeraScite — the citation checker that refuses to guess

**Detect fabricated, misattributed, and misrepresented case citations in legal briefs and AI-generated legal writing — and, just as importantly, never accuse a real one.**

[![tests](https://github.com/rakib-nyc/verascite/actions/workflows/ci.yml/badge.svg)](https://github.com/rakib-nyc/verascite/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![Version](https://img.shields.io/badge/version-0.3.0-blue.svg)](CHANGELOG.md)

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

As of **v0.2.0** it also checks **statutes and regulations** — including whether a cited
*subsection* of a real statute actually exists — and writes a **verification record**: a
dated, hash-bound account of what was checked against what, in the shape the standing orders
now governing AI-assisted filings actually contemplate.

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

## The number that matters most

About **one citation in ten** in a real appellate brief is absent from the free
archives — not fabricated, not defective, simply not held. Recent decisions, unpublished
dispositions, state trial court orders and vendor-only identifiers are missing as a matter of
routine.

So the question that decides whether a citation checker is usable is not how much it catches.
It is what it does with a citation it cannot find. Published figures for that case:

| System | Test set | Sound citations falsely flagged as fabricated |
|---|---|---:|
| Gemini | 132 real-but-absent | **65.9%** |
| GPT-5 | 132 real-but-absent | **25%** |
| **VeraScite** | 95 unreachable, from 996 sound citations | **0%** |
| **VeraScite** | 101 absent-from-all-sources control | **0%** |
| **VeraScite** | 149 vendor-only, *court-adjudicated fabricated* | **0%** |

**Zero false accusations across 345 unreachable citations**, from three independent samples.
The third is the strongest: those citations really were fabricated and a court said so — and
the tool still declined to say so, because a vendor-only identifier that was invented is
indistinguishable from one naming a real unreported decision when no free source holds either.

> Comparison rows come from published benchmark results on a different test set. Comparable
> in task, not item for item. Full protocol and limits:
> [`evals/abstention/PROTOCOL.md`](evals/abstention/PROTOCOL.md).

Measured 9.5% unreachable on that corpus, independently corroborating the ~10.2% reported in
the published work. Every run now tells you your own figure, before the counts:

```
> 11 citation(s) in this document are absent from the sources consulted and were
> not examined. That reflects what the free archives hold, not the quality of
> those citations. Read the counts below as describing only what could be examined.
```

---

## New in v0.3.0

**New guide:** [`docs/WORKFLOW.md`](docs/WORKFLOW.md) — checking citations from an
AI-assisted research session, written for students, independent researchers, and anyone
working without a paid research subscription.

### Plain-language reports, for the people who actually file these

Of the US filings in which a court has found a fabricated citation, roughly **six in ten were
filed by someone representing themselves** — no firm, no research subscription, and usually no
idea that the tool which drafted their filing can invent authority.

```bash
verascite my-motion.docx --out ./check --plain
```

`plain-english.md` says the same things without vocabulary anyone has to look up, and takes
particular care in the direction that matters: *"**This does not mean it is fake.** Many real
cases are not in free archives."*

### A Word add-in, where briefs are actually written

```bash
mkdir -p ~/Library/Containers/com.microsoft.Word/Data/Documents/wef
curl -o ~/Library/Containers/com.microsoft.Word/Data/Documents/wef/verascite.xml \
  https://raw.githubusercontent.com/rakib-nyc/verascite/main/word-addin/manifest.xml
```

Checks every citation in the draft and writes a Word comment on each finding — and on
**nothing** that is merely absent from an archive, because about one citation in ten in a real
brief is missing from free sources and is perfectly sound. Only the citation strings leave the
machine; never the document. [Install guide](word-addin/README.md).

### An MCP server, so the systems that write the citations can check them

```bash
python -m verascite.mcp_server
```

Free lookup APIs already answer *"is there a case at this citation."* What they do not do is
decline properly. This returns the three-state answer — `contradicted` / `unverified` /
`confirmed` — and ships the reporting constraint with **every** response, because a calling
model paraphrases whatever it is handed and `NOT_FOUND` becomes "this case does not exist" in
about three hops. JSON-RPC over stdio, standard library only.

---

## New in v0.2.0

### The model-assisted layer now runs from the command line

Through v0.1.0 the installed command line had no model in it. That was the right call for
the deterministic core and the wrong outcome for users: the deterministic layer alone is
37.3% F1, and content misrepresentation — the largest defect class — is **0% detectable**
without a model reading the opinion.

```bash
# Nothing leaves your machine. Point at any local endpoint.
verascite brief.docx --out ./audit --model local --model-name YOUR-MODEL

# Or a hosted endpoint speaking the chat-completions JSON interface.
verascite brief.docx --out ./audit --model api   --model-base-url https://your-endpoint/v1 --model-name YOUR-MODEL
```

`--model local` refuses any endpoint that is not a loopback address. The guarantee that no
citation, proposition, or opinion text leaves the machine is **enforced in code, not
promised in documentation** — which is what a lawyer holding privileged material needs.

Every verdict a model produced is marked in the report, alongside the backend, the model,
whether text left the machine, what the run cost, and a plain statement that a reading is not
reproducible. **`--deterministic-only` remains the default**; no model runs unless asked for.

> **Backend choice changes results, and the effect is far larger than "materially".**
> Measured on the same samples, a small 4B local model scored **5.6% recall against the
> 82.7% of the reader used in the evaluation** — it answers, but almost every answer is
> low-confidence and therefore reaches you as "a human should look at this."
> [`evals/backends/RESULTS.md`](evals/backends/RESULTS.md) has the numbers.
>
> The part worth keeping: **under a weak reader the tool degrades toward silence, not
> toward false accusation** — one false positive in 35, and the verbatim span interlock
> caught a further false accusation the weak model was about to make. Treat a small local
> model as a *privacy* floor, not a capability floor. Measure your own backend before
> relying on it; the harness that produced those numbers is in the repository.

### A verification record, shaped to the obligation

113 active standing orders now require certification that a licensed attorney independently
verified every citation. Sanctions attach to the failure to verify, not to the use of AI.

`verification-record.md` is written on every run. It binds itself to a SHA-256 of the exact
file reviewed, states the tool version and threshold set, lists every citation with the
sources consulted for it and when they were retrieved, and enumerates **what was not
examined — good-law status first, and before the results.** The attorney's attestation block
is deliberately blank.

**VeraScite certifies nothing.** The record is evidence of inquiry, and it says so in its
first lines.

### Statutes and regulations

Previously out of scope entirely. Now parsed and checked against free federal sources that
need **no credential**: the government link service, the eCFR versions endpoint, and the
official structural text of the US Code.

The check this enables is the one worth having:

```
42 U.S.C. § 1983(a)(2)  →  FLAGGED
    Section 1983 of title 42 is present in the official structural text
    and does not contain the subsection the citation names.
    Checked against release point Online@119-103.
```

Section 1983 has no subsections at all. A fabricated subsection of a real statute is
damaging and nearly invisible to a human reviewer, because everything before the parenthesis
is correct.

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
| Flag a misquotation against a **fixed** threshold | 42 defects | A **correct** quotation matches its source at median **0.929**, not 1.00, because both the brief and the archive are OCR output. Every threshold was swept: precision peaks at **17.2%**. **Still rejected.** But see below — calibrating to the document instead of to a constant changes the answer. |
| Flag a short-form name that matches neither party | 4 defects | 40% precision — and the false alarms were cases where *lookup* landed wrong, not where the brief erred. |
| A bounded negative-treatment signal ("is this still good law?") | — | Re-measured in v0.2.5. Proximity search is **66× better** than the rejected document-level version, and still does not separate: *Roe* (overruled) returns 46 hits, *Twombly* (good law) returns **45**. Three verified causes — the query cannot tell "X overruled Y" from "Y overruled X"; courts overrule *objections*; and an old case's rate is diluted by decades of pre-overruling citations. [Protocol](evals/treatment/PROTOCOL.md). |

Roughly **105 of the remaining misses sit behind measured rejections rather than unbuilt
work.** Full protocols in [`evals/`](evals/).

A shipped version of that last one would report "nothing found" for *Roe v. Wade* and
"3 signals found" for *Twombly* — a confident wrong answer about whether a case is good law,
which is the one thing this tool must never give. The protocol names four specific things to
try next, none of them tested and none of them claimed to work.

### One rejection was revisited, and the reason it failed was not the one on record

v0.1.0 recorded that misquote detection failed because the archive text was scanned, and
that publisher-quality text should fix it. **That turned out to be wrong**, and the
measurement is in [`evals/misquote/RESULTS.md`](evals/misquote/RESULTS.md).

Re-running the sweep on 236 opinion texts the archive itself marks as *not* scanned, the
scanned and non-scanned subsets behave almost identically at every noise level. **The
archive's scanning flag is not the variable.** The planned fix — gating the check on
`extracted_by_ocr == False` — would not have worked.

What decides the outcome is how far *this document* and the archive disagree, and a fixed
threshold cannot know that. A document, however, carries the evidence needed to measure it.
If nine of a brief's quotations match their sources at 0.999 and the tenth matches at 0.977,
the tenth is anomalous **for this document** — and the comparison is between quotations that
passed through the same author, the same word processor, and the same extraction.

| | Fixed threshold | Calibrated to the document |
|---|---:|---:|
| Precision | 17.2% | **80–91%** |
| Recall | — | 35–56% |

Where the document is too noisy to calibrate, the check **declines** — 28 of 29 documents at
the highest disagreement level, rather than guessing.

It ships as `REVIEW` and never as a finding, for two reasons. The evidence is synthetic
disagreement rather than real scanned briefs. And an anomaly in agreement is not
contradiction: the honest statement is *"this quotation matches its source less well than
the rest of yours do — read it."*

---

## External validation: citations courts actually sanctioned

Every figure above comes from one benchmark of *injected* defects. v0.2.0 added a corpus
where the labels are **judicial findings**: 633 citations that courts adjudicated to be
fabricated, extracted from a public database of sanctions decisions
([CC BY 4.0](evals/sanctioned/CORPUS.md)) with provenance pinned by hash. v0.2.5 runs it.

| | |
|---:|---|
| Scored (excluding 149 vendor-only, 32 unparsed) | 484 |
| **Flagged** | **239 — 49.4%** |
| Cost of the entire run | **4 requests** |

**49.4% is a floor, not recall.** The corpus labels a *record*, not always the citation
extracted from it. A narrative reading *"counsel cited X, which does not exist; the correct
case was Y"* can yield Y — `Iko v. Shreve, 535 F.3d 225` is real, and the fabrication in
that record was `Iko v. Shreve, 122 F.3d 707`. Others are real cases carrying a fabricated
*quotation*, which is not checkable from a citation string. Every kind of noise pushes the
number down, never up. Measured rather than asserted: excluding all 51 rows whose narrative
discusses a correction moves it only to 49.7%.

**The result that matters more:** 149 of the 633 are vendor-only identifiers like
`2019 WL 1396975`. Every one was reported `UNVERIFIED`. **None was flagged** — on citations
that really were fabricated, with a court order to prove it. That is the governing rule
holding under the hardest pressure there is, and it sets a ceiling worth stating plainly:
**23.5% of the defective citations in that corpus are ones this tool will never flag, by
design.** A checker that flagged them would score better and be a worse tool.

### What it found that the benchmark could not

Asking why 49 citations came back `VERIFIED` exposed a real defect. The case-name matcher
scored `State v. Pune` against `State v. Ing` at **1.00**, and would confirm a fabricated
case name against any unrelated decision printed at the same page whenever the two shared a
generic party — `State v.`, `People v.`, `Commonwealth v.`, `United States v.`, `In re`.
That is an enormous share of American case law.

The benchmark could not have found it: its citations carry years and courts, which the
metadata stage uses to disambiguate. Strip those, as a bare citation in a sanctions order
does, and the case name is doing all the work alone.

It was a **recall** defect, not a false-accusation one — it made the tool report `VERIFIED`
where it should have reported a mismatch — which is why it survived: this project's tests
attack false accusation hardest. Fixed, locked under test, and the fix moved the corpus from
46.7% to 49.4%. Analysis in [`evals/names/V9_PROTOCOL.md`](evals/names/V9_PROTOCOL.md).

**This is what external validation is for**, and it is the most useful thing the corpus has
produced.

> The corpus is built from court **orders**, not from the **filings** that contained the
> citations. It answers *"given a citation a court adjudicated to be fabricated, is it
> flagged?"* — not *"does this catch bad citations in a real brief?"* The limitations are
> listed in full before any result in [`CORPUS.md`](evals/sanctioned/CORPUS.md).

---

## Install

**[Every way to use it, step by step →](docs/INSTALL-ALL.md)** — web page, browser
extension, Word add-in, command line, MCP server, and how to set your own CourtListener
token on each (optional; everything works without one).

> **AI can make mistakes. For experimental and research use only.**

## Getting started

### Step 1 — Check you have Python 3.10 or newer

```bash
python3 --version
```

If that prints anything below `3.10`, install a newer Python from
[python.org/downloads](https://www.python.org/downloads/) first.

### Step 2 — Install VeraScite

```bash
pip install "verascite[all] @ git+https://github.com/rakib-nyc/verascite.git"
```

<details>
<summary>Recommended: install into a virtual environment (keeps it isolated)</summary>

```bash
python3 -m venv verascite-env
source verascite-env/bin/activate        # Windows: verascite-env\Scripts\activate
pip install "verascite[all] @ git+https://github.com/rakib-nyc/verascite.git"
```

Re-run the `activate` line in any new terminal before using `verascite`.
</details>

The `[all]` extra adds PDF and DOCX support. For Markdown and plain text only,
drop it and install `git+https://github.com/rakib-nyc/verascite.git`.

### Step 3 — Confirm it installed

```bash
verascite --help
```

You should see the usage message. If the shell says `command not found`, see
[Troubleshooting](docs/INSTALL.md#troubleshooting).

### Step 4 — Run it on a document

```bash
verascite brief.pdf --out ./audit
```

Accepts `.pdf`, `.docx`, `.md`, and `.txt`. Two files are written:

| File | What it is |
|---|---|
| `audit/report.md` | The evidence package — open this |
| `audit/ledger.json` | Machine-readable results for scripting |

The exit code is `2` if anything was contradicted by a retrieved source, `0`
otherwise — so it drops into a pre-commit hook or CI job unchanged.

### Step 5 (optional) — Add a CourtListener token for wider coverage

VeraScite works with **no credentials at all** against the public-domain
Caselaw Access Project. A free [CourtListener](https://www.courtlistener.com/)
token adds coverage of recent decisions:

1. Create a free account at [courtlistener.com/sign-in](https://www.courtlistener.com/sign-in/)
2. Copy your token from [courtlistener.com/profile/api](https://www.courtlistener.com/profile/api/)
3. Put it in your environment:

```bash
export COURTLISTENER_API_TOKEN="your-token-here"
```

To persist it, add that line to `~/.zshrc` or `~/.bashrc`.

The token is never written to reports, ledgers, cache keys, or logs — enforced
by `tests/test_no_credential_leak.py`.

> **On PyPI:** once published, this becomes `pip install "verascite[all]"`.
> Until then the Git URL above is the install path, and it works today.

Full instructions, including Windows and offline installation:
**[`docs/INSTALL.md`](docs/INSTALL.md)**

---

## Try it in 30 seconds

Save this as `demo.md`:

```markdown
Plaintiff's complaint fails to state a claim. A pleading must contain more than
"labels and conclusions." Bell Atlantic Corp. v. Twombly, 550 U.S. 544, 555 (2007).

The court should also consider Smith v. Fictional Reporter Co., 88 Jurisprudentia
100 (9th Cir. 2018), which is directly on point.

See also Tinch v. Video Indus. Servs., Inc., 2019 WL 1396975 (E.D. Mich. 2019).
```

Then run:

```bash
verascite demo.md --out ./audit --offline
```

`--offline` keeps every citation string on your machine. You will see the
distinction the tool exists to make:

- **`Jurisprudentia`** is `FLAGGED` — no such reporter has ever been published.
- **`2019 WL 1396975`** is `UNVERIFIED` — a real decision with a vendor-only
  identifier. **Not** reported as fabricated.

---

## Other ways to run it

```bash
# Local checks only. No citation string leaves the machine.
verascite brief.docx --out ./audit --offline

# Skip opinion-text retrieval (faster, fewer API calls)
verascite brief.md --out ./audit --no-quotes

# Resume an interrupted run from the existing ledger
verascite brief.pdf --out ./audit --resume

# Read each authority against the proposition it is cited for, entirely locally
verascite brief.docx --out ./audit --model local --model-name YOUR-MODEL

# Check subsections of cited statutes against the official structural text
# (downloads ~18MB per US Code title, cached)
verascite brief.docx --out ./audit --download-code-titles

# Review a whole matter folder, with one consolidated report
verascite ./matter-2026-114 --batch --out ./audit
```

### Batch review

`--batch` treats the path as a directory and reviews every `.pdf`, `.docx`, `.md` and
`.txt` under it, writing each document's own evidence package plus a consolidated
`batch-report.md` ordered most-severe-first.

Runs are **sequential and share the cache** — deliberately. The archives' rate limits are
shared across the batch too, so running four documents at once would spend the budget four
times as fast and finish no sooner. Two briefs citing the same authority cost one lookup
between them.

A document that cannot be read is recorded as **unexamined**, named in the summary, and the
batch continues. It is never reported as clean.

### Grounded reading options

| Flag | Effect |
|---|---|
| `--model` | `local`, `api`, `command`, or `none` (default) |
| `--model-name` | Model identifier your backend expects |
| `--model-base-url` | Endpoint; required for `api`, loopback default for `local` |
| `--model-param KEY=VALUE` | Extra request-body key, repeatable. JSON values are sent as JSON |
| `--model-max-reads N` | Stop after N opinions — reading is the expensive part of a run |
| `--model-cost-per-1k-input` / `--output` | Your rates, for the cost line in the report |

No price table for any service ships with this tool. A stale hardcoded price is worse than
no price, so cost is reported only from rates you supply.

If your model emits a reasoning scratchpad, it may spend the whole reply budget on it and
return nothing. Suppress it with whatever key your backend uses, for example
`--model-param think=false`, or raise `--model-max-tokens`.

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
- Cover legislative materials or secondary sources. These are routed to manual review.
  Statutes and regulations *are* covered as of v0.2.0, but **their currency is not**: the
  answer describes the text currently in force, so a provision amended or repealed since the
  events in issue is still reported as existing.
- Cover non-US authority.
- Guarantee that an `UNVERIFIED` citation is fake, or that a `VERIFIED` one is apt.

Known measured weaknesses, stated plainly:

- **Pincite recall is 13.2%** in the deterministic layer.
- **Misquote detection is 0%** against a fixed threshold. The calibrated check added in
  v0.2.0 reaches 80–91% precision on synthetic evidence, ships as `REVIEW` only, and
  **declines entirely** on a document too noisy to calibrate.
- **Regulatory subsection checking is weaker than statutory**, and says so in every result:
  regulation text is flat, so the paragraph hierarchy is inferred from printed designators
  rather than read from structure. It never reports a paragraph as absent.
- **Grounded reading is not reproducible**, and results depend materially on the backend.
- **Vendor-only identifiers can never be flagged.** Measured on real sanctioned filings, that
  is 23.5% of defective citations. This is by design and is not going to change.

---

## Documentation

| Document | Contents |
|---|---|
| [`docs/INSTALL.md`](docs/INSTALL.md) | Full installation guide, Windows, troubleshooting |
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
  version = {0.3.0},
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
