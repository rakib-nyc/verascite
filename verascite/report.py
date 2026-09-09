"""Stage 6: turn the ledger into something a lawyer can act on.

The report is the product. A ledger nobody reads protects nobody, and a
report that leads with reassurance trains people to stop reading -- which is
the automation-bias risk the whole design is built against (spec 9). So:

* Findings come first, worst first. Successes are collapsed into a count.
* Every finding names a concrete next action, not just a verdict.
* What was *not* checked is stated as prominently as what was.
* The distinction between "absent from our sources" and "contradicted by our
  sources" is restated in plain language on the first screen.

The mandatory disclosure block lives in this module rather than in an editable
template. P9 makes it non-negotiable, and a header that can be silently
removed by deleting an asset file is not non-negotiable.
"""

from __future__ import annotations

import textwrap
from datetime import datetime, timezone
from typing import Optional

from .ledger import Ledger
from .safety import flatten_for_output
from .models import CiteKind, LedgerEntry
from .verdicts import SEVERITY_ORDER, CheckResult, Overall, Verdict

DISCLOSURE = """\
**This is an evidence package, not a certification.** It does not certify any
filing. It is not a citator: it cannot tell you whether an authority is still
good law. It is not legal advice. The reviewing attorney retains full
responsibility under Rule 11 and the applicable rules of professional conduct.

**`UNVERIFIED` does not mean fabricated.** It means the sources consulted do
not contain the authority. That is routine for recent decisions, unpublished
dispositions, state trial court opinions, and Westlaw- or Lexis-only
identifiers. Treat it as a prompt to check manually, not as a finding that the
case does not exist.
"""

VERDICT_MEANING = {
    Overall.FLAGGED: (
        "Contradicted by a retrieved source. Do not file without fixing."
    ),
    Overall.REVIEW: "Something here needs a human read.",
    Overall.UNVERIFIED: (
        "Absent from the sources consulted. Check manually. **Not** a finding "
        "of fabrication."
    ),
    Overall.PENDING: "Not checked.",
    Overall.VERIFIED: "Confirmed on every applicable dimension that was checked.",
}

DIMENSION_LABELS = {
    "existence": "Existence",
    "reporter_valid": "Reporter",
    "case_name": "Case name",
    "court": "Court",
    "year": "Year",
    "pincite": "Pincite",
    "quote": "Quotation",
    "quote_source": "Quotation attribution",
    "quote_agreement": "Quotation agreement",
    "proposition": "Proposition support",
    "treatment": "Later treatment",
    "precedential": "Precedential status",
    "jurisdiction": "Jurisdiction",
    "statute_parts": "Statutory citation",
    "statute_currency": "Statutory currency",
}

#: (dimension, verdict) -> what the reader should actually do.
ACTIONS = {
    ("reporter_valid", Verdict.FAIL): (
        "Confirm this citation against the original source. The reporter "
        "abbreviation is not one used in American law, which usually means the "
        "citation was invented or badly garbled."
    ),
    ("existence", Verdict.NOT_FOUND): (
        "Look this citation up in Westlaw, Lexis, or the issuing court's own "
        "site. Free databases have real coverage gaps."
    ),
    ("existence", Verdict.OUT_OF_SCOPE): (
        "Verify manually. This authority type is outside what this tool covers."
    ),
    ("existence", Verdict.AMBIGUOUS): (
        "Several different cases match this citation. Determine which one is "
        "meant and cite it unambiguously."
    ),
    ("case_name", Verdict.FAIL): (
        "The reporter citation belongs to a different case than the one named. "
        "Correct the case name or the citation."
    ),
    ("court", Verdict.FAIL): (
        "Correct the court. This changes whether the authority binds the forum."
    ),
    ("year", Verdict.FAIL): "Correct the year.",
    ("quote", Verdict.FAIL): (
        "Compare the quotation against the opinion and correct it, or remove "
        "the quotation marks if you are paraphrasing."
    ),
    ("quote_source", Verdict.FAIL): (
        "Either attribute the language to its actual source in the citation, or "
        "rely on language from the opinion of the court instead."
    ),
    ("quote_source", Verdict.NOT_CHECKABLE): (
        "Read the surrounding passage in the cited opinion to see whom it was "
        "quoting, and attribute accordingly."
    ),
    ("treatment", Verdict.NOT_CHECKABLE): (
        "Check this authority in a citator before relying on it. Whether it has "
        "been overruled, reversed, vacated, or superseded is the one question "
        "this tool cannot answer at all."
    ),
    ("proposition", Verdict.NOT_CHECKABLE): (
        "Read the cited passage and confirm it says what the brief says it says. "
        "This is the check most likely to matter and the one least amenable to "
        "automation."
    ),
    ("proposition", Verdict.FAIL): (
        "The cited authority contradicts the proposition. Remove it or cite it "
        "for what it actually holds."
    ),
    ("precedential", Verdict.NOT_CHECKABLE): (
        "Check the forum's local rules before citing this disposition."
    ),
    ("existence", Verdict.OUT_OF_SCOPE): (
        "Verify manually. This authority type is outside what this tool covers."
    ),
    ("statute_currency", Verdict.OUT_OF_SCOPE): (
        "Statutes are amended. Confirm the quoted text against the current "
        "version on GovInfo or eCFR, and note the effective date you relied on."
    ),
}

GENERIC_ACTIONS = {
    Verdict.FAIL: "Correct this before filing.",
    Verdict.NOT_FOUND: "Verify manually in a commercial database.",
    Verdict.NOT_CHECKABLE: "Verify this dimension by hand.",
    Verdict.AMBIGUOUS: "Disambiguate before relying on this authority.",
    Verdict.OUT_OF_SCOPE: "Verify manually; outside this tool's coverage.",
}


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _wrap(text: str, width: int = 78, indent: str = "") -> str:
    clean = " ".join((text or "").split())
    return "\n".join(textwrap.wrap(clean, width=width, initial_indent=indent,
                                   subsequent_indent=indent)) or ""


def _action_for(dimension: str, check: CheckResult) -> Optional[str]:
    if check.verdict in (Verdict.PASS, Verdict.NA, Verdict.PENDING):
        return None
    return ACTIONS.get((dimension, check.verdict)) or GENERIC_ACTIONS.get(check.verdict)


def _location(entry: LedgerEntry) -> str:
    loc = entry.citation.location
    where = f"page {loc.page}" if loc.page else f"character {loc.char_start}"
    if loc.in_footnote:
        where += ", in a footnote"
    return where


def _citation_heading(entry: LedgerEntry) -> str:
    """The citation as written, made safe to put in a Markdown heading.

    Document text is attacker-controlled. Uncleaned, a citation carrying a
    newline and a '#' writes its own section into the report.
    """
    return flatten_for_output(entry.citation.raw_text, limit=110) or "(unnamed citation)"


#: Set on any check the grounded reading produced. Mirrors
#: verify_proposition.PROPOSITION_SOURCE; duplicated rather than imported so
#: the renderer does not pull in the reading stage.
MODEL_SOURCE = "model_grounded_reading"


def _is_model_derived(check: CheckResult) -> bool:
    return MODEL_SOURCE in (check.sources_consulted or ())


def _render_entry(entry: LedgerEntry) -> list[str]:
    lines = [f"#### {_citation_heading(entry)}", ""]
    lines.append(f"`{entry.citation_id}` &middot; {_location(entry)} &middot; "
                 f"**{entry.overall.value}**")
    # Several findings can share one citation string. Without the quoted
    # language a reader cannot tell which is the one they already fixed.
    quoted = entry.citation.quoted_language
    if quoted:
        snippet = flatten_for_output(quoted[0], limit=90)
        lines.append("")
        lines.append(f'> Quoting: "{snippet}"')
    lines.append("")

    problems = [
        (name, check)
        for name, check in entry.checks.items()
        if check.verdict not in (Verdict.PASS, Verdict.NA, Verdict.PENDING)
    ]
    # A citation whose reporter is invented cannot have its case name, court,
    # or year compared either, and listing those consequential rows buries the
    # one row the reader acts on. But collapsing is only ever driven by an
    # explicit suppressed_by set at the point the cascade happened -- never by
    # matching on wording, which would eventually hide a row that failed for an
    # independent reason that happened to co-occur. The ledger keeps every row.
    consequential = [(n, c) for n, c in problems if c.suppressed_by]
    shown = [(n, c) for n, c in problems if not c.suppressed_by]
    if not shown:
        shown, consequential = consequential, []

    if shown:
        lines.append("| Check | Verdict | Evidence |")
        lines.append("|---|---|---|")
        for name, check in shown:
            detail = flatten_for_output(check.evidence or check.reason or "", limit=600)
            label = DIMENSION_LABELS.get(name, name)
            if _is_model_derived(check):
                label += " &dagger;"
            lines.append(f"| {label} | `{check.verdict.value}` | {detail} |")
        lines.append("")
        if any(_is_model_derived(c) for _, c in shown):
            lines.append(
                "&dagger; *Reached by a model reading the retrieved opinion, not by "
                "the reproducible layer. It is bounded -- a finding required the "
                "model to quote the opinion verbatim -- but it is a reading, and a "
                "different model may read differently.*"
            )
            lines.append("")

    if consequential:
        by_cause: dict[str, list[str]] = {}
        for name, check in consequential:
            by_cause.setdefault(check.suppressed_by or "", []).append(
                DIMENSION_LABELS.get(name, name)
            )
        for cause, names in by_cause.items():
            label = DIMENSION_LABELS.get(cause, cause)
            lines.append(
                f"*Not checkable while {label} is unresolved:* {', '.join(names)}."
            )
        lines.append("")

    passed = [
        DIMENSION_LABELS.get(name, name) + (" &dagger;" if _is_model_derived(check) else "")
        for name, check in entry.checks.items()
        if check.verdict is Verdict.PASS
    ]
    if passed:
        lines.append(f"*Confirmed:* {', '.join(passed)}.")
        lines.append("")

    actions = []
    for name, check in shown:
        action = _action_for(name, check)
        if action and action not in actions:
            actions.append(action)
    if actions:
        lines.append("**What to do:**")
        for action in actions:
            lines.append(f"- {action}")
        lines.append("")

    if entry.resolved_to and entry.resolved_to.url:
        resolution = entry.resolved_to
        stamp = f" (retrieved {resolution.retrieved_at})" if resolution.retrieved_at else ""
        lines.append(f"*Matched to:* [{resolution.case_name or 'record'}]({resolution.url}){stamp}")
        lines.append("")
    return lines


def _reading_section(reading: dict) -> list[str]:
    """Provenance for the one stage that is not reproducible.

    Printed in full whenever a model read anything, because a reader who cannot
    tell which verdicts came from a reading cannot weigh them. The cost lines
    are here rather than in a log because reading opinions is the expensive
    part of a run and the person paying should see what it cost.
    """
    lines = ["**Grounded reading**", ""]
    lines.append(
        f"- Backend `{reading.get('backend')}`, model `{reading.get('model')}`"
        + (f", endpoint `{reading.get('endpoint')}`" if reading.get("endpoint") else "")
    )
    if reading.get("leaves_this_machine") is False:
        lines.append(
            "- **No citation, proposition, or opinion text was sent off this "
            "machine for reading.**"
        )
    else:
        lines.append(
            "- Citation text, the proposition asserted, and retrieved opinion text "
            "were sent to the endpoint named above."
        )
    lines.append(
        f"- {_plural(reading.get('read', 0), 'citation')} read of "
        f"{reading.get('candidates', 0)} eligible"
    )
    if reading.get("deferred_by_cap"):
        lines.append(
            f"- **{reading['deferred_by_cap']} were not read** because the run hit "
            "its reading limit. They are unexamined, not disputed."
        )
    if reading.get("opinion_text_unavailable"):
        lines.append(
            f"- {reading['opinion_text_unavailable']} could not be read because no "
            "opinion text was retrievable."
        )
    if reading.get("failures"):
        lines.append(
            f"- {_plural(reading['failures'], 'reading')} failed at the backend. "
            "A failure is recorded, never converted into a verdict."
        )
    latency = reading.get("total_latency_s")
    if latency:
        lines.append(
            f"- {latency}s spent reading, {reading.get('mean_latency_s')}s per "
            f"citation on average, slowest {reading.get('slowest_read_s')}s"
        )
    if "input_tokens" in reading:
        lines.append(
            f"- {reading['input_tokens']:,} input and {reading['output_tokens']:,} "
            "output tokens"
        )
    elif reading.get("tokens"):
        lines.append(f"- Token use: {reading['tokens']}")
    if reading.get("estimated_cost"):
        lines.append(
            f"- Estimated cost {reading['estimated_cost']}, at the rates supplied "
            "on the command line"
        )
    lines.append("")
    lines.append(f"> {reading.get('caveat', '')}")
    lines.append("")
    return lines


def _scope_section(ledger: Ledger) -> list[str]:
    meta = ledger.run_meta
    lines = ["## What was checked", ""]

    lines.append("**Sources consulted**")
    lines.append("")
    for source in ledger.sources_available or ["(none)"]:
        lines.append(f"- `{source}`")
    lines.append("")

    lookup = meta.get("existence_lookup")
    if isinstance(lookup, dict):
        lines.append(
            f"Citation lookup covered {_plural(lookup.get('distinct_citations', 0), 'distinct citation')} "
            f"in {_plural(lookup.get('requests', 0), 'request')}, "
            f"{lookup.get('cache_hits', 0)} served from cache."
        )
        lines.append("")
        oldest = lookup.get("oldest_evidence")
        if not lookup.get("fetched_now"):
            lines.append(
                "> **Every result in this report came from a local cache; nothing "
                "was fetched during this run.** The findings are as current as the "
                + (f"evidence recorded on {oldest}." if oldest else "cache.")
            )
            lines.append("")
        elif oldest:
            lines.append(f"Oldest evidence relied on was retrieved {oldest}.")
            lines.append("")
    elif isinstance(lookup, str):
        lines.append(f"**No citation lookup was performed.** {lookup}")
        lines.append("")

    reading = meta.get("grounded_reading") or {}

    lines.append("**What was not checked**")
    lines.append("")
    not_checked = [
        "Whether an authority remains good law. This tool is not a citator; it "
        "does not detect overruled, reversed, vacated, abrogated, or superseded "
        "decisions.",
    ]
    if not reading:
        not_checked.append(
            "Whether a cited authority actually supports the proposition it is "
            "cited for. **No model was configured for this run, so no citation's "
            "substance was examined.** Content misrepresentation -- a real case "
            "cited for something it does not hold -- cannot be detected without "
            "one, and it is the largest single class of citation defect."
        )
    not_checked.append("Pincite accuracy beyond the page a quotation was found on.")
    statutes = meta.get("statutes") or {}
    if statutes:
        not_checked.append(
            "Whether a cited statute or regulation read the same way on the date "
            "it matters. The statutory answers in this report describe the text "
            "currently in force; a provision amended or repealed since the events "
            "in issue is still reported as existing."
        )
        if statutes.get("subsection_note"):
            not_checked.append(statutes["subsection_note"].capitalize() + ".")
    else:
        not_checked.append(
            "Statutes and regulations, which were not checked in this run."
        )
    not_checked += [
        "Legislative materials and secondary sources, which are routed to "
        "manual verification.",
        "Anything about non-US authority.",
    ]
    for item in not_checked:
        lines.append(f"- {item}")
    lines.append("")

    if reading:
        lines += _reading_section(reading)

    # Printed even when everything is zero. Silence here lets a reader assume
    # quotations were checked and cleared.
    quotes = meta.get("quotes") or {}
    if quotes:
        lines.append("**Quotations**")
        lines.append("")
        lines.append(
            f"- {_plural(quotes.get('found_in_document', 0), 'quoted passage')} found "
            "in the document"
        )
        lines.append(
            f"- {quotes.get('attributed_to_a_citation', 0)} attached to a citation"
        )
        lines.append(
            f"- {quotes.get('checked_against_source', 0)} compared against retrieved "
            "opinion text"
        )
        unattributed = quotes.get("unattributed", 0)
        if unattributed:
            lines.append(
                f"- **{unattributed} could not be attached to any citation and were "
                "therefore not checked.** A quotation is only checked when the "
                "citation it belongs to is unambiguous; guessing risks reporting a "
                "correct quotation as a misquote."
            )
        lines.append("")

    lines.append("**Citations**")
    lines.append("")
    unparsed = meta.get("unparsed_spans") or []
    lines.append(f"- {_plural(meta.get('citations_found', 0), 'citation')} found")
    lines.append(f"- {meta.get('distinct_authorities', 0)} distinct authorities")
    if unparsed:
        lines.append(
            f"- **{len(unparsed)} citation-shaped passage(s) could not be parsed** "
            "and were not checked: "
            + ", ".join(flatten_for_output(u, limit=80) for u in unparsed[:5])
        )
    lines.append("")

    warnings = meta.get("document_warnings") or []
    if warnings:
        lines.append("**Notes on this document**")
        lines.append("")
        for item in warnings:
            lines.append(f"- {flatten_for_output(str(item), limit=400)}")
        lines.append("")
    return lines


def render_report(ledger: Ledger, generated_at: Optional[str] = None) -> str:
    counts = ledger.counts()
    stamp = generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds")

    lines: list[str] = []
    lines.append("# Citation audit")
    lines.append("")
    lines.append(f"**Document:** `{flatten_for_output(ledger.document_path, limit=200)}`  ")
    lines.append(f"**Generated:** {stamp}  ")
    authorities = ledger.run_meta.get("distinct_authorities", 0)
    lines.append(
        f"**Citations examined:** {_plural(len(ledger), 'citation')} across "
        f"{authorities} distinct "
        f"{'authority' if authorities == 1 else 'authorities'}"
    )
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(DISCLOSURE)
    lines.append("---")
    lines.append("")

    lines.append("## Summary")
    lines.append("")
    lines.append("| Result | Count | Meaning |")
    lines.append("|---|---:|---|")
    for overall in sorted(Overall, key=lambda o: SEVERITY_ORDER[o]):
        if counts[overall.value] or overall in (Overall.FLAGGED, Overall.VERIFIED):
            lines.append(
                f"| **{overall.value}** | {counts[overall.value]} | "
                f"{VERDICT_MEANING[overall]} |"
            )
    lines.append("")

    if counts[Overall.FLAGGED.value]:
        lines.append(
            f"> {_plural(counts[Overall.FLAGGED.value], 'citation')} contradicted by a "
            "source that was actually retrieved. Those are listed first."
        )
        lines.append("")

    lines.extend(_scope_section(ledger))

    lines.append("## Findings")
    lines.append("")
    ordered = sorted(
        (e for e in ledger if e.overall is not Overall.VERIFIED),
        key=lambda e: (SEVERITY_ORDER[e.overall], e.citation.location.char_start),
    )
    if not ordered:
        lines.append("No citation required attention.")
        lines.append("")
    else:
        current: Optional[Overall] = None
        for entry in ordered:
            if entry.overall is not current:
                current = entry.overall
                lines.append(f"### {current.value}")
                lines.append("")
                lines.append(f"*{VERDICT_MEANING[current]}*")
                lines.append("")
            lines.extend(_render_entry(entry))

    verified = ledger.by_overall(Overall.VERIFIED)
    lines.append("## Confirmed citations")
    lines.append("")
    if not verified:
        lines.append("None.")
    else:
        lines.append(
            f"{_plural(len(verified), 'citation')} confirmed on every dimension that "
            "was checked. This is not a statement that they are good law."
        )
        lines.append("")
        for entry in sorted(verified, key=lambda e: e.citation.location.char_start):
            checked = ", ".join(
                DIMENSION_LABELS.get(name, name)
                for name, check in entry.checks.items()
                if check.verdict is Verdict.PASS
            )
            lines.append(f"- {_citation_heading(entry)} &mdash; *{checked}*")
    lines.append("")

    thresholds = ledger.run_meta.get("thresholds")
    if thresholds:
        lines.append("## Reproducibility")
        lines.append("")
        lines.append(
            f"Deterministic checks ran with threshold set version "
            f"`{thresholds.get('version')}`. The same document and the same "
            "sources produce the same verdicts."
        )
        lines.append("")
        lines.append("```json")
        import json
        lines.append(json.dumps(thresholds, indent=2))
        lines.append("```")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_report(ledger: Ledger, path) -> None:
    from pathlib import Path

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_report(ledger), encoding="utf-8")
