"""How much of this document the sources could actually speak to.

**The gap this closes.** Every report already says, per citation, whether the
sources consulted contained it. What it never said is the shape of that across
the document: how much of the brief the tool could see at all. A reader who
skims a report showing two findings and eighteen confirmations draws a very
different conclusion than one who learns that eleven of the twenty citations
were never in the corpus to begin with.

**Why it belongs in the product rather than in a footnote.** Roughly one
citation in ten in a real appellate brief is absent from the free archives --
not defective, simply not held. That is the single largest determinant of what
a verification run is worth, and it is invisible unless it is counted. Stating
it converts "the tool found nothing wrong" into "the tool could examine nine of
your fourteen citations and found nothing wrong in those" -- which is the true
statement and the one a reviewing attorney can act on.

**What this must never become.** A coverage figure is a statement about the
sources, not about the document. A low figure means the archives are thin for
this brief's authorities -- recent decisions, unpublished dispositions, state
trial courts, vendor-only identifiers -- and says nothing whatever about whether
those citations are sound. Every string this module produces is written so that
it cannot be read the other way.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .ledger import Ledger
from .models import CiteKind
from .verdicts import Overall, Verdict

#: Citation kinds that are looked up against an archive at all. A statute is
#: checked, but against different sources, and folding the two together would
#: report a coverage figure that describes neither.
_CASE_KINDS = {CiteKind.FULL_CASE, CiteKind.SHORT_CASE, CiteKind.ID, CiteKind.SUPRA}


@dataclass
class Coverage:
    """What the run could and could not see."""

    citations: int = 0
    #: Case citations the sources consulted contained.
    reachable: int = 0
    #: Case citations absent from every source consulted.
    absent: int = 0
    #: Looked up, but the answer was ambiguous or the source lacked the data.
    indeterminate: int = 0
    #: Never looked up: offline, no credential, or an authority type not covered.
    not_consulted: int = 0
    #: Statutory and regulatory citations, counted separately.
    statutory: int = 0
    sources: list[str] = field(default_factory=list)

    @property
    def examined(self) -> int:
        """Citations a source actually spoke to."""
        return self.reachable

    @property
    def rate(self) -> float | None:
        """Share of case citations the sources contained."""
        denominator = self.reachable + self.absent + self.indeterminate
        return (self.reachable / denominator) if denominator else None

    def to_dict(self) -> dict:
        out = {
            "citations": self.citations,
            "reachable": self.reachable,
            "absent_from_sources": self.absent,
            "indeterminate": self.indeterminate,
            "not_consulted": self.not_consulted,
            "statutory": self.statutory,
            "sources_consulted": list(self.sources),
        }
        if self.rate is not None:
            out["reachable_share"] = round(self.rate, 4)
        out["statement"] = self.statement()
        out["caveat"] = (
            "Coverage describes the sources, not the document. A citation absent "
            "from every archive consulted is not thereby doubtful: recent "
            "decisions, unpublished dispositions, state trial court orders and "
            "vendor-only identifiers are absent as a matter of routine."
        )
        return out

    def statement(self) -> str:
        """One sentence a reviewing attorney can rely on."""
        if not self.citations:
            return "No citations were found in this document."
        if self.not_consulted and not (self.reachable or self.absent):
            return (
                f"No source was consulted for any of the {self.citations} citation(s) "
                "in this document, so nothing here reflects a comparison against "
                "the published record."
            )
        parts = [
            f"Of {self.citations} citation(s), {self.reachable} were found in the "
            f"sources consulted and examined against them."
        ]
        if self.absent:
            parts.append(
                f"{self.absent} were not in those sources and could not be examined "
                "— that is a limit of the archives, not a finding about the citation."
            )
        if self.indeterminate:
            parts.append(
                f"{self.indeterminate} were reached but could not be resolved to a "
                "single authority."
            )
        if self.not_consulted:
            parts.append(f"{self.not_consulted} were not looked up in this run.")
        if self.statutory:
            parts.append(
                f"{self.statutory} statutory or regulatory citation(s) were checked "
                "against the government's published text instead."
            )
        return " ".join(parts)

    def headline(self) -> str:
        """The line that goes at the top of a report, or empty when unremarkable."""
        if self.rate is None or not self.absent:
            return ""
        share = self.absent / (self.reachable + self.absent + self.indeterminate)
        if share < 0.15:
            return ""
        return (
            f"**{self.absent} of {self.reachable + self.absent + self.indeterminate} "
            f"case citations in this document are absent from the sources consulted "
            f"({share:.0%}).** Those were not examined. This reflects what the free "
            "archives hold, not the quality of the citations, and it is normal for "
            "briefs relying on recent, unpublished, or vendor-reported decisions."
        )


def measure(ledger: Ledger) -> Coverage:
    """Count what the sources could speak to."""
    coverage = Coverage(sources=list(ledger.sources_available or []))
    for entry in ledger:
        coverage.citations += 1
        kind = entry.citation.kind
        if kind is CiteKind.LAW:
            coverage.statutory += 1
            continue
        if kind not in _CASE_KINDS:
            coverage.not_consulted += 1
            continue

        existence = entry.checks.get("existence")
        if existence is None or existence.verdict is Verdict.PENDING:
            coverage.not_consulted += 1
        elif existence.verdict is Verdict.PASS:
            coverage.reachable += 1
        elif existence.verdict is Verdict.NOT_FOUND:
            coverage.absent += 1
        elif existence.verdict is Verdict.AMBIGUOUS:
            coverage.indeterminate += 1
        elif existence.verdict is Verdict.FAIL:
            # Contradicted by a retrieved source: the source spoke to it.
            coverage.reachable += 1
        else:
            coverage.not_consulted += 1
    return coverage


def unexamined(ledger: Ledger) -> list:
    """Entries no source could speak to, for the report's own list.

    Kept separate from the findings, because a citation nobody could look up is
    not a result and must never be printed among them.
    """
    out = []
    for entry in ledger:
        if entry.overall is Overall.UNVERIFIED:
            out.append(entry)
    return out
