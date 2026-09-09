"""The verification record: what was checked, against what, and when.

**The obligation this answers.** Standing orders on AI-assisted filings have
converged on a single requirement, and it is not disclosure. It is
certification that a licensed attorney *independently verified* every citation.
Sanctions attach to the failure to verify, not to the use of a drafting tool.
An attorney who has done that work needs something to point at afterwards, and
"a tool told me the citations were fine" is not it.

**What this file produces, and what it refuses to produce.** A record of
inquiry: for every citation, what was looked for, which source answered, on
what date, and what the answer was. It is bound to one exact file by a content
hash, so a record cannot be attached to a document that has since been edited.

It is not a certification and this module will not let it read like one. Three
rules are enforced here rather than left to the wording:

1. **The record never certifies.** It says what was checked. The attorney
   certifies, and the record is evidence supporting that certification.
2. **What was not checked is stated as prominently as what was.** A record
   that lists twelve confirmed citations and omits that good-law status was
   never examined is worse than no record: it invites exactly the reliance the
   tool exists to prevent. The unchecked list is therefore not optional, not
   collapsible, and appears before the results.
3. **UNVERIFIED is described in the record as absence, every time it appears.**
   The governing rule survives the change of format or it is not a rule.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import __version__
from .ledger import Ledger
from .report import DIMENSION_LABELS, MODEL_SOURCE
from .safety import flatten_for_output
from .verdicts import Overall, Verdict

#: Read in blocks: a brief is small, but nothing here should assume it.
_HASH_BLOCK = 1 << 20

#: How each document-level result is described in a record a court may read.
#: Written out in full rather than abbreviated, because the reader of a record
#: is not the reader of a terminal and may see the word once, in isolation.
RESULT_MEANING = {
    Overall.FLAGGED: (
        "A source was retrieved and it contradicts the document. Evidence is "
        "attached to the finding."
    ),
    Overall.UNVERIFIED: (
        "Not found in the sources consulted. **This is not a finding that the "
        "authority does not exist or was fabricated.** Recent decisions, "
        "unpublished dispositions, state trial court orders, and "
        "vendor-only identifiers are routinely absent from free archives."
    ),
    Overall.REVIEW: "Something about this citation needs a human read.",
    Overall.PENDING: "Not checked in this run.",
    Overall.VERIFIED: (
        "Confirmed against a retrieved source on every dimension that applied. "
        "**This is not a statement that the authority is still good law.**"
    ),
}

#: Stated in every record, in this order. Good-law status is first because it
#: is the omission most likely to be assumed away by a reader in a hurry.
NOT_EXAMINED = [
    (
        "Whether any authority remains good law",
        "This tool is not a citator. It does not detect decisions that have been "
        "overruled, reversed, vacated, abrogated, superseded, or called into "
        "doubt. Nothing in this record speaks to the current precedential value "
        "of any authority in the document. That question was not asked and is "
        "not answered here.",
    ),
    (
        "Whether an authority is apt for the argument it supports",
        "Verifying that an opinion exists and says what it is cited for is not "
        "the same as verifying that it is the right authority for this motion, "
        "in this posture, in this jurisdiction.",
    ),
    (
        "Whether a statute or regulation read the same way on the date it matters",
        "Statutory and regulatory citations are checked against the text "
        "currently in force. A provision that has been amended or repealed since "
        "the events in issue will still be reported as existing. Currency was "
        "not examined.",
    ),
    (
        "Legislative materials and secondary sources",
        "These are outside the scope of the checks run here and were routed to "
        "manual verification.",
    ),
    (
        "Non-US authority",
        "Not covered.",
    ),
    (
        "Subsequent history and parallel proceedings",
        "Not examined.",
    ),
]


def content_hash(path: Path) -> str:
    """SHA-256 of the exact bytes reviewed.

    The record is worthless if it can be detached from the file it describes
    and attached to a later draft. The hash is what prevents that, so it is
    taken over the raw bytes rather than over extracted text -- extracted text
    is a function of the extractor's version, and two runs of different
    versions on one file must not produce two different identities.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(_HASH_BLOCK), b""):
            digest.update(block)
    return digest.hexdigest()


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _sources_for(entry) -> list[str]:
    """Every source consulted for one citation, deduplicated, in a stable order."""
    seen: list[str] = []
    for source in entry.sources_consulted or []:
        if source not in seen:
            seen.append(source)
    for check in (entry.checks or {}).values():
        for source in check.sources_consulted or []:
            if source not in seen:
                seen.append(source)
    return seen


def _retrieved_at(entry) -> str:
    resolution = entry.resolved_to
    return (resolution.retrieved_at or "") if resolution else ""


def render_record(
    ledger: Ledger,
    document: Path,
    generated_at: Optional[str] = None,
    document_hash: Optional[str] = None,
) -> str:
    """Render the verification record for one run."""
    stamp = generated_at or _utcnow()
    digest = document_hash or (content_hash(document) if Path(document).exists() else "")
    thresholds = ledger.run_meta.get("thresholds") or {}
    reading = ledger.run_meta.get("grounded_reading") or {}
    counts = ledger.counts()

    lines: list[str] = []
    lines.append("# Record of citation verification inquiry")
    lines.append("")
    lines.append(
        "> **This record is evidence of the inquiry described in it. It is not a "
        "certification, and it does not discharge any professional obligation.** "
        "Responsibility under Rule 11 and the applicable rules of professional "
        "conduct remains entirely with the filing attorney. The tool that "
        "produced this record verifies nothing on anyone's behalf; it records "
        "what was looked for, where, and what came back."
    )
    lines.append("")

    # --- identity ---------------------------------------------------------
    lines.append("## 1. The document this record describes")
    lines.append("")
    lines.append("| | |")
    lines.append("|---|---|")
    lines.append(f"| File | `{flatten_for_output(Path(document).name, limit=200)}` |")
    lines.append(f"| SHA-256 of file contents | `{digest or 'unavailable'}` |")
    lines.append(f"| Format | `{ledger.document_format or 'unknown'}` |")
    lines.append(f"| Citations found | {len(ledger)} |")
    lines.append("")
    lines.append(
        "The hash identifies the exact bytes reviewed. **If the document has been "
        "edited since, this record does not describe it** -- recompute the hash "
        "before relying on the record, and re-run if it differs."
    )
    lines.append("")

    # --- provenance -------------------------------------------------------
    lines.append("## 2. When, and with what")
    lines.append("")
    lines.append("| | |")
    lines.append("|---|---|")
    lines.append(f"| Inquiry recorded | {stamp} |")
    lines.append(f"| Run began | {ledger.created_at} |")
    lines.append(f"| Tool version | `{__version__}` |")
    lines.append(f"| Threshold set | `{thresholds.get('version', 'unrecorded')}` |")
    lines.append("")
    lines.append("**Sources consulted in this run**")
    lines.append("")
    for source in ledger.sources_available or ["(none)"]:
        lines.append(f"- `{source}`")
    lines.append("")
    if reading:
        lines.append(
            f"A model read retrieved opinion text for {reading.get('read', 0)} "
            f"citation(s) to judge whether the authority supports the proposition "
            f"it was cited for (backend `{reading.get('backend')}`, model "
            f"`{reading.get('model')}`). Readings are marked in section 5. "
            "**A reading is not reproducible**: the same passage read again, or "
            "read by a different model, may give a different answer. No reading "
            "could produce a finding unless it quoted the opinion verbatim."
        )
        lines.append("")

    # --- the limits, before the results -----------------------------------
    lines.append("## 3. What this inquiry did not examine")
    lines.append("")
    lines.append(
        "Stated before the results, because a record of what was checked is "
        "misleading without it."
    )
    lines.append("")
    for heading, detail in NOT_EXAMINED:
        lines.append(f"**{heading}.** {detail}")
        lines.append("")

    # --- how to read a result ---------------------------------------------
    lines.append("## 4. What each result means")
    lines.append("")
    lines.append("| Result | Count | Meaning |")
    lines.append("|---|---:|---|")
    for overall in (Overall.FLAGGED, Overall.UNVERIFIED, Overall.REVIEW,
                    Overall.VERIFIED, Overall.PENDING):
        count = counts.get(overall.value, 0)
        lines.append(f"| `{overall.value}` | {count} | {RESULT_MEANING[overall]} |")
    lines.append("")

    # --- the citation-by-citation record ----------------------------------
    lines.append("## 5. Every citation, and what was done about it")
    lines.append("")
    lines.append(
        "One row per citation, in the order they appear in the document. "
        "`Sources` names what was actually consulted for that citation; an "
        "empty cell means nothing was consulted and the result reflects only "
        "local checks."
    )
    lines.append("")
    lines.append("| # | Citation | Location | Result | Checks | Sources consulted | Retrieved |")
    lines.append("|---|---|---|---|---|---|---|")
    ordered = sorted(ledger, key=lambda e: e.citation.location.char_start)
    for index, entry in enumerate(ordered, start=1):
        checks = _checks_cell(entry)
        sources = ", ".join(f"`{s}`" for s in _sources_for(entry))
        lines.append(
            f"| {index} "
            f"| {flatten_for_output(entry.citation.raw_text, limit=140)} "
            f"| {entry.citation.location.char_start} "
            f"| `{entry.overall.value}` "
            f"| {checks} "
            f"| {sources} "
            f"| {_retrieved_at(entry) or '--'} |"
        )
    lines.append("")

    # --- the attestation the attorney fills in ----------------------------
    lines.append("## 6. Attorney's independent review")
    lines.append("")
    lines.append(
        "This section is deliberately blank. The tool cannot fill it in, and a "
        "record that arrived pre-filled would be a certification the tool has no "
        "standing to make."
    )
    lines.append("")
    lines.append("| | |")
    lines.append("|---|---|")
    lines.append("| Reviewing attorney | |")
    lines.append("| Bar number and jurisdiction | |")
    lines.append("| Date of independent review | |")
    lines.append("| Citations independently confirmed | |")
    lines.append("| Authorities checked for current validity, and how | |")
    lines.append("| Notes | |")
    lines.append("")
    lines.append(
        "The fourth row matters most. Every `UNVERIFIED` result above is a "
        "citation this tool could not locate in the archives it consulted, which "
        "says nothing about whether the authority exists. Those require a human "
        "to look somewhere else."
    )
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        f"Record generated {stamp} by VeraScite {__version__}. This tool is "
        "experimental research software provided without warranty. It does not "
        "provide legal advice and it certifies nothing."
    )
    lines.append("")
    return "\n".join(lines)


def _checks_cell(entry) -> str:
    """Every dimension decided for this citation, most severe first."""
    order = {
        Verdict.FAIL: 0, Verdict.NOT_FOUND: 1, Verdict.AMBIGUOUS: 2,
        Verdict.NOT_CHECKABLE: 3, Verdict.OUT_OF_SCOPE: 4, Verdict.NA: 5,
        Verdict.PASS: 6, Verdict.PENDING: 7,
    }
    decided = [
        (name, check)
        for name, check in (entry.checks or {}).items()
        if check.verdict is not Verdict.PENDING
    ]
    decided.sort(key=lambda pair: order.get(pair[1].verdict, 9))
    parts = []
    for name, check in decided:
        label = DIMENSION_LABELS.get(name, name)
        mark = " (model reading)" if MODEL_SOURCE in (check.sources_consulted or ()) else ""
        parts.append(f"{label}: `{check.verdict.value}`{mark}")
    return "; ".join(parts) or "none decided"


def write_record(ledger: Ledger, document: Path, path: Path) -> Path:
    """Write the verification record beside the report."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_record(ledger, Path(document)), encoding="utf-8")
    return path
