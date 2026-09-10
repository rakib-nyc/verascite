"""The same findings, written for someone who is not a lawyer.

**Why this exists.** Of the US court filings in which a judge has identified a
fabricated citation, roughly six in ten were filed by people representing
themselves. They have no law firm, no research subscription, and in most cases
no idea that the tool which drafted their filing can invent authority that does
not exist. They are the largest affected population by a wide margin and the one
least served by anything in this field.

The standard report is written for a reviewing attorney. It says `NOT_CHECKABLE`
and `precedential status` and `pincite`, and it assumes the reader knows why a
citation absent from an archive is unremarkable. To a self-represented litigant
that report is close to unreadable, and the parts they *can* read are the parts
most likely to be misunderstood in the dangerous direction -- reading
"unverified" as "you have been caught filing something fake."

**What this does not do.** It does not soften a finding, change a verdict, or
give advice about what to file. It restates the same ledger in ordinary English,
keeps the governing rule intact in every sentence, and says plainly at both ends
that it is not legal advice and that a person is responsible for what they file.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .coverage import measure as measure_coverage
from .ledger import Ledger
from .safety import flatten_for_output
from .verdicts import Overall

#: A FLAGGED citation can fail in several different ways, and telling a reader
#: "we found the case and it does not match" when in fact the *reporter* does not
#: exist is a small lie that costs trust. The explanation follows the check that
#: actually failed.
FLAGGED_BY_CHECK = {
    "reporter_valid": (
        "The books this case claims to be published in do not exist.",
        "Every published American case appears in a numbered series of books called "
        "a reporter. The series named here is not one of them. That usually means "
        "the citation was invented. Take it out unless you can produce the case "
        "itself.",
    ),
    "existence": (
        "The official source says there is nothing at this citation.",
        "The government's own published text was checked and it has no such "
        "provision. Check the number carefully, and remove it if you cannot "
        "confirm it.",
    ),
    "statute_parts": (
        "The law exists, but the specific subsection you cite is not in it.",
        "The section is real; the lettered or numbered part after it is not in the "
        "official text. Re-read the statute and cite the part that actually says "
        "what you need.",
    ),
    "case_name": (
        "There is a real case at this citation, but it has a different name.",
        "Look up the citation and see which case is actually there. Either the "
        "name or the numbers in your filing are wrong.",
    ),
    "quote": (
        "The case is real, but it does not contain the words your filing quotes.",
        "Open the case and copy the quotation exactly, or remove the quotation "
        "marks and describe the case in your own words instead.",
    ),
    "proposition": (
        "The case is real, but it does not say what your filing says it says.",
        "Read the passage yourself. If it does not support your point, do not "
        "cite it for that point.",
    ),
}

#: What each result means, said once, without vocabulary a reader has to look up.
MEANING = {
    Overall.FLAGGED: (
        "Something in this citation is contradicted by the source we retrieved.",
        "Check this one before you file. Open the case and read it. If your filing "
        "describes it wrongly, fix the description or take the citation out.",
    ),
    Overall.UNVERIFIED: (
        "We could not find this case in the free public archives we searched.",
        "**This does not mean it is fake.** Many real cases are not in free "
        "archives — recent ones, unpublished ones, state trial courts, and cases "
        "that only paid services like Westlaw or Lexis carry. It does mean nobody "
        "has confirmed it for you, so confirm it yourself before you rely on it.",
    ),
    Overall.REVIEW: (
        "Something about this citation needs a person to look at it.",
        "Read the case and check that it says what your filing says it says.",
    ),
    Overall.VERIFIED: (
        "We found the case and the details we could check matched.",
        "Nothing to do. Note this does not tell you whether the case is still good "
        "law, or whether it helps your argument.",
    ),
    Overall.PENDING: ("This citation was not checked.", "Check it yourself."),
}

ORDER = [Overall.FLAGGED, Overall.REVIEW, Overall.UNVERIFIED, Overall.VERIFIED, Overall.PENDING]


def render_plain(ledger: Ledger, document: Path, generated_at: str = "") -> str:
    """Render the plain-language version of a run."""
    stamp = generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    coverage = ledger.run_meta.get("coverage") or measure_coverage(ledger).to_dict()
    counts = ledger.counts()

    lines: list[str] = []
    lines.append("# What we checked in your document")
    lines.append("")
    lines.append(
        "> **This is not legal advice, and it is not a guarantee.** It is a list of "
        "what an automated check could and could not confirm about the cases your "
        "document cites. You are responsible for what you file. If you can get help "
        "from a lawyer, a court self-help centre, or a legal aid office, do."
    )
    lines.append("")
    lines.append(f"Document: `{flatten_for_output(Path(document).name, limit=120)}`  ")
    lines.append(f"Checked: {stamp}")
    lines.append("")

    # --- the thing that matters most, first ---
    lines.append("## The most important thing to understand")
    lines.append("")
    lines.append(
        "Some AI writing tools invent court cases that do not exist. They look "
        "completely real — correct format, plausible names, believable page numbers. "
        "Filing one can get your case thrown out or get you fined."
    )
    lines.append("")
    lines.append(
        "**But the opposite mistake matters too.** A great many real cases are not in "
        "the free public archives. If we say we could not find something, that is a "
        "statement about the archives we searched — not about your case. **We never "
        "say a citation is fake.** We only ever say what we found and where we looked."
    )
    lines.append("")

    lines.append("## What we found")
    lines.append("")
    lines.append("| | How many | What it means |")
    lines.append("|---|---:|---|")
    for overall in ORDER:
        count = counts.get(overall.value, 0)
        if not count:
            continue
        headline, _ = MEANING[overall]
        lines.append(f"| **{_label(overall)}** | {count} | {headline} |")
    lines.append("")
    if coverage.get("statement"):
        lines.append(coverage["statement"])
        lines.append("")

    # --- per citation, most severe first ---
    ordered = ledger.sorted_by_severity()
    shown = [e for e in ordered if e.overall is not Overall.VERIFIED]
    if shown:
        lines.append("## Citations to look at")
        lines.append("")
        for entry in shown:
            lines += _entry(entry)

    confirmed = [e for e in ordered if e.overall is Overall.VERIFIED]
    if confirmed:
        lines.append("## Citations that checked out")
        lines.append("")
        for entry in confirmed:
            lines.append(f"- {flatten_for_output(entry.citation.raw_text, limit=160)}")
        lines.append("")
        lines.append(
            "*Checking out means the case exists and the details we could compare "
            "matched. It does not mean the case is still good law, and it does not "
            "mean it helps your argument.*"
        )
        lines.append("")

    lines.append("## What this check did not do")
    lines.append("")
    for item in (
        "It did not check whether a case is **still good law**. Courts overturn "
        "earlier decisions, and this cannot tell you when that has happened.",
        "It did not check whether a case actually **helps your argument**.",
        "It did not read your whole document, only the citations in it.",
        "It is not a lawyer and it does not give legal advice.",
    ):
        lines.append(f"- {item}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        "*Produced by VeraScite, free and open-source software. It is experimental, "
        "it comes with no warranty of any kind, and it certifies nothing.*"
    )
    lines.append("")
    return "\n".join(lines)


def _label(overall: Overall) -> str:
    return {
        Overall.FLAGGED: "Does not match",
        Overall.UNVERIFIED: "Could not find",
        Overall.REVIEW: "Needs a look",
        Overall.VERIFIED: "Checked out",
        Overall.PENDING: "Not checked",
    }[overall]


def _entry(entry) -> list[str]:
    from .verdicts import Verdict

    headline, advice = MEANING[entry.overall]
    # Prefer the explanation matching the check that actually failed.
    failed_names = [n for n, c in (entry.checks or {}).items() if c.verdict is Verdict.FAIL]
    for name in failed_names:
        if name in FLAGGED_BY_CHECK:
            headline, advice = FLAGGED_BY_CHECK[name]
            break

    lines = [f"### {flatten_for_output(entry.citation.raw_text, limit=160)}", ""]
    lines.append(f"**{_label(entry.overall)}.** {headline}")
    lines.append("")

    # The strongest piece of evidence, in plain terms.
    failing = [c for c in entry.checks.values() if c.verdict is Verdict.FAIL and c.evidence]
    if failing:
        lines.append(f"> {flatten_for_output(failing[0].evidence, limit=420)}")
        lines.append("")
    elif entry.overall is Overall.UNVERIFIED:
        existence = entry.checks.get("existence")
        named = ", ".join((existence.sources_consulted or [])) if existence else ""
        if named:
            lines.append(f"We searched: {named}.")
            lines.append("")

    lines.append(f"**What to do:** {advice}")
    lines.append("")
    return lines


def write_plain(ledger: Ledger, document: Path, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_plain(ledger, Path(document)), encoding="utf-8")
    return path
