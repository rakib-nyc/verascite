"""Core data structures: Document, RawCitation, LedgerEntry, Ledger."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Optional

from .verdicts import (
    STANDING,
    CheckResult,
    Overall,
    Verdict,
    VerdictRegression,
    rollup,
)


class CiteKind(str, Enum):
    FULL_CASE = "full_case"
    SHORT_CASE = "short_case"
    SUPRA = "supra"
    ID = "id"
    LAW = "law"           # statutes / regulations -- routed to the statute path
    JOURNAL = "journal"
    UNRECOGNIZED = "unrecognized"  # citation-shaped, reporter not in reporters-db


@dataclass
class Location:
    """Where a citation sits in the source document."""

    char_start: int
    char_end: int
    page: Optional[int] = None
    paragraph: Optional[int] = None
    in_footnote: bool = False

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class Document:
    """Normalized text plus an offset map back into the original."""

    text: str
    source_path: str
    source_format: str
    #: (char_start, char_end, page_number) spans, ascending.
    page_map: list[tuple[int, int, int]] = field(default_factory=list)
    #: char offsets that fall inside footnotes, as (start, end) spans.
    footnote_spans: list[tuple[int, int]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def page_for(self, offset: int) -> Optional[int]:
        for start, end, page in self.page_map:
            if start <= offset < end:
                return page
        return None

    def in_footnote(self, offset: int) -> bool:
        return any(s <= offset < e for s, e in self.footnote_spans)

    def context(self, start: int, end: int, window: int = 320) -> str:
        """Verbatim surrounding text. Never paraphrased (spec 5.3)."""
        return self.text[max(0, start - window) : min(len(self.text), end + window)]


@dataclass
class RawCitation:
    """One citation as extracted, before any lookup."""

    kind: CiteKind
    raw_text: str
    location: Location
    volume: Optional[str] = None
    reporter: Optional[str] = None
    page: Optional[str] = None
    normalized: Optional[str] = None
    pin_cite: Optional[str] = None
    plaintiff: Optional[str] = None
    defendant: Optional[str] = None
    case_name: Optional[str] = None
    court: Optional[str] = None      # eyecite court id, e.g. "ca10"
    year: Optional[str] = None
    antecedent_guess: Optional[str] = None
    #: statute fields
    title: Optional[str] = None
    section: Optional[str] = None
    #: verbatim document context (spec 5.3 -- substrings only, never a paraphrase)
    sentence: Optional[str] = None
    quoted_language: list[str] = field(default_factory=list)
    signal: Optional[str] = None     # introductory signal: see, see also, cf., but see, contra...

    def to_dict(self) -> dict:
        d = asdict(self)
        d["kind"] = self.kind.value
        d["location"] = self.location.to_dict()
        return {k: v for k, v in d.items() if v not in (None, [], {})}


@dataclass
class Resolution:
    """What the citation was resolved to in an external source."""

    source: str
    cluster_id: Optional[int] = None
    docket_id: Optional[int] = None
    url: Optional[str] = None
    case_name: Optional[str] = None
    #: CourtListener stores three different names per cluster and they are
    #: genuinely different strings. A brief may match any of them.
    case_name_short: Optional[str] = None
    case_name_full: Optional[str] = None
    court_id: Optional[str] = None
    date_filed: Optional[str] = None
    retrieved_at: Optional[str] = None
    precedential_status: Optional[str] = None
    #: Every reporter the same decision is printed in. A citation to one
    #: reporter can be read from an archive that only holds another: CAP holds
    #: U.S. but not S. Ct., so "137 S. Ct. 1045" is reachable as "581 U.S. 1".
    parallel_citations: list[str] = field(default_factory=list)
    candidates: list[dict] = field(default_factory=list)  # populated when AMBIGUOUS

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v not in (None, [], {})}


@dataclass
class LedgerEntry:
    """A citation plus every verdict rendered against it (P5)."""

    citation_id: str
    citation: RawCitation
    checks: dict[str, CheckResult] = field(default_factory=dict)
    resolved_to: Optional[Resolution] = None
    #: citation_id of the full citation this short form resolves to
    antecedent_id: Optional[str] = None
    #: normalized key shared by every citation pointing at the same authority
    authority_key: Optional[str] = None
    sources_consulted: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def overall(self) -> Overall:
        return rollup(self.checks)

    def set_check(
        self, dimension: str, result: CheckResult, override: Optional[str] = None
    ) -> None:
        """Record a verdict, refusing late improvements.

        Three times now a later stage has written into a row an earlier stage
        had already decided, each time making a citation look better than the
        evidence supported: metadata confirming a case name against a candidate
        picked from an ambiguous set, and so on. Rather than fix the fourth
        instance, the ledger enforces the rule.

        A row may go from PENDING to anything, and may be downgraded. Anything
        else -- an upgrade, or a write over a suppressed or failed row --
        requires an explicit ``override`` naming the reason, which is recorded.
        """
        existing = self.checks.get(dimension)
        if existing is not None and not override:
            problem = self._regression_reason(dimension, existing, result)
            if problem:
                raise VerdictRegression(problem)

        if override:
            result.detail = dict(result.detail)
            result.detail["override"] = override
            if existing is not None:
                result.detail["overrode"] = existing.verdict.value
            self.notes.append(
                f"{dimension}: verdict overridden "
                f"({existing.verdict.value if existing else 'unset'} -> "
                f"{result.verdict.value}) because {override}"
            )

        self.checks[dimension] = result
        for src in result.sources_consulted:
            if src not in self.sources_consulted:
                self.sources_consulted.append(src)

    @staticmethod
    def _regression_reason(
        dimension: str, existing: CheckResult, result: CheckResult
    ) -> Optional[str]:
        if existing.verdict is Verdict.PENDING:
            return None
        if existing.suppressed_by:
            return (
                f"{dimension} was suppressed by {existing.suppressed_by!r} and may "
                f"not be re-decided as {result.verdict.value} by a later stage. "
                "Pass override= with a reason if this is genuinely intended."
            )
        if existing.verdict is Verdict.FAIL and result.verdict is not Verdict.FAIL:
            return (
                f"{dimension} is already FAIL with evidence and may not be raised to "
                f"{result.verdict.value}. Pass override= with a reason if this is "
                "genuinely intended."
            )
        if STANDING[result.verdict] > STANDING[existing.verdict]:
            return (
                f"{dimension} would be upgraded from {existing.verdict.value} to "
                f"{result.verdict.value} by a later stage. Verdicts may be "
                "downgraded, never improved. Pass override= with a reason if this "
                "is genuinely intended."
            )
        return None

    def try_set_check(self, dimension: str, result: CheckResult) -> bool:
        """Set the verdict if permitted; report whether it was taken."""
        try:
            self.set_check(dimension, result)
            return True
        except VerdictRegression:
            return False

    def to_dict(self) -> dict:
        d = {
            "citation_id": self.citation_id,
            "raw_text": self.citation.raw_text,
            "normalized": self.citation.normalized,
            "type": self.citation.kind.value,
            "location": self.citation.location.to_dict(),
            "citation": self.citation.to_dict(),
            "checks": {k: v.to_dict() for k, v in self.checks.items()},
            "overall": self.overall.value,
            "sources_consulted": list(self.sources_consulted),
        }
        if self.resolved_to:
            d["resolved_to"] = self.resolved_to.to_dict()
        if self.antecedent_id:
            d["antecedent_id"] = self.antecedent_id
        if self.authority_key:
            d["authority_key"] = self.authority_key
        if self.notes:
            d["notes"] = list(self.notes)
        return d
