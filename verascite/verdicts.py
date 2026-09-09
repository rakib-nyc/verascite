"""Verdict vocabulary and rollup rules.

This module is the enforcement point for design principle P1:

    Absence of evidence is never evidence of fabrication.

There is deliberately no ``FABRICATED`` verdict anywhere in this system.
The strongest claim the pipeline can make about a citation is ``FLAGGED``,
and a citation can only reach ``FLAGGED`` through a dimension verdict of
``FAIL``, which cannot be constructed without affirmative contradicting
evidence (see ``CheckResult.__post_init__``).

``NOT_FOUND`` is a separate verdict with a separate rollup path
(``UNVERIFIED``) and it cannot be constructed without naming the sources
that were consulted. "We looked in these places and did not find it" is a
statement the data model can represent. "This citation is fake" is not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Verdict(str, Enum):
    """Per-dimension verdict. Small and unambiguous by design (spec 4.3)."""

    PASS = "PASS"                    # affirmatively confirmed against a retrieved source
    FAIL = "FAIL"                    # affirmatively contradicted by a retrieved source
    NOT_FOUND = "NOT_FOUND"          # not present in any source consulted -- NOT fabricated
    NOT_CHECKABLE = "NOT_CHECKABLE"  # source retrieved but lacks the data this check needs
    AMBIGUOUS = "AMBIGUOUS"          # multiple candidate matches (CL status 300)
    OUT_OF_SCOPE = "OUT_OF_SCOPE"    # authority type this pipeline does not cover
    NA = "N/A"                       # dimension inapplicable to this citation
    PENDING = "PENDING"              # not yet checked -- the default state (P6)


class Overall(str, Enum):
    """Document-facing rollup verdict."""

    VERIFIED = "VERIFIED"        # every applicable dimension PASS
    REVIEW = "REVIEW"            # something needs a human read
    FLAGGED = "FLAGGED"          # affirmative contradiction -- do not file without fixing
    UNVERIFIED = "UNVERIFIED"    # absent from consulted sources -- NOT a fabrication finding
    PENDING = "PENDING"          # nothing checked yet


# --- P1 structural guarantees -------------------------------------------------
#
# These frozensets are consumed by rollup() and asserted over in the test suite.
# They exist so that the NOT_FOUND / fabrication distinction is a property of
# the type system and a checked invariant, not a naming convention.

#: The only verdict that may escalate a citation to FLAGGED.
FABRICATION_SIGNALS = frozenset({Verdict.FAIL})

#: Verdicts that describe the *limits of our search*, never the citation itself.
#: No combination of these may ever produce a FLAGGED rollup.
ABSENCE_VERDICTS = frozenset(
    {Verdict.NOT_FOUND, Verdict.NOT_CHECKABLE, Verdict.OUT_OF_SCOPE, Verdict.AMBIGUOUS}
)

assert not (FABRICATION_SIGNALS & ABSENCE_VERDICTS), "P1 violated: verdict sets overlap"


#: Human-facing gloss for each verdict. Used by the report layer so that the
#: NOT_FOUND wording is defined once, in code, and cannot drift per-template.
REMEDIATION = {
    Verdict.PASS: "No action needed for this dimension.",
    Verdict.FAIL: "Contradicted by the retrieved source. Correct or remove before filing.",
    Verdict.NOT_FOUND: (
        "Not present in the sources consulted. This is NOT a finding that the "
        "authority does not exist -- free databases routinely lack recent "
        "decisions, unpublished dispositions, state trial courts, and "
        "Westlaw/Lexis-only identifiers. Verify manually in a commercial database."
    ),
    Verdict.NOT_CHECKABLE: (
        "The source was retrieved but does not carry the data this check needs. "
        "Verify this dimension manually."
    ),
    Verdict.AMBIGUOUS: "Multiple candidate authorities matched. Disambiguate manually.",
    Verdict.OUT_OF_SCOPE: "This authority type is outside the pipeline's coverage. Verify manually.",
    Verdict.NA: "Dimension does not apply to this citation.",
    Verdict.PENDING: "Not yet checked.",
}


@dataclass
class CheckResult:
    """The result of one dimension check against one citation.

    Two constructor-time interlocks enforce P1 and P3:

    * A ``FAIL`` requires ``evidence`` -- you cannot contradict a citation
      without saying what contradicts it.
    * A ``NOT_FOUND`` requires ``sources_consulted`` -- you cannot report an
      absence without saying where you looked.

    Both raise ``ValueError`` at construction. It is not possible to build a
    bare accusation.
    """

    verdict: Verdict
    evidence: Optional[str] = None
    reason: Optional[str] = None
    sources_consulted: list[str] = field(default_factory=list)
    confidence: Optional[str] = None  # "high" | "medium" | "low" (model-assisted checks)
    #: Name of the check whose failure made this one unanswerable. Set only
    #: when the causal link is known, never inferred from co-occurrence. The
    #: report collapses these rows; the ledger always keeps them (P3).
    suppressed_by: Optional[str] = None
    detail: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.verdict, str) and not isinstance(self.verdict, Verdict):
            self.verdict = Verdict(self.verdict)

        if self.verdict is Verdict.FAIL and not (self.evidence or "").strip():
            raise ValueError(
                "P1/P3 interlock: a FAIL verdict requires affirmative evidence. "
                "If you have no contradicting source, the verdict is NOT_FOUND or "
                "NOT_CHECKABLE, not FAIL."
            )

        if self.verdict is Verdict.NOT_FOUND and not self.sources_consulted:
            raise ValueError(
                "P1 interlock: a NOT_FOUND verdict requires a non-empty "
                "sources_consulted list. An absence finding is only meaningful "
                "alongside the set of places that were searched."
            )

        if self.verdict in (Verdict.NOT_CHECKABLE, Verdict.OUT_OF_SCOPE) and not (
            self.reason or ""
        ).strip():
            raise ValueError(
                f"P7/P8 interlock: a {self.verdict.value} verdict requires a stated reason."
            )

    @property
    def is_fabrication_signal(self) -> bool:
        return self.verdict in FABRICATION_SIGNALS

    @property
    def remediation(self) -> str:
        return REMEDIATION[self.verdict]

    def to_dict(self) -> dict:
        out: dict = {"verdict": self.verdict.value}
        for key in ("evidence", "reason", "confidence", "suppressed_by"):
            val = getattr(self, key)
            if val:
                out[key] = val
        if self.sources_consulted:
            out["sources_consulted"] = list(self.sources_consulted)
        if self.detail:
            out["detail"] = self.detail
        return out

    @classmethod
    def from_dict(cls, d: dict) -> "CheckResult":
        return cls(
            verdict=Verdict(d["verdict"]),
            evidence=d.get("evidence"),
            reason=d.get("reason"),
            sources_consulted=list(d.get("sources_consulted", [])),
            confidence=d.get("confidence"),
            suppressed_by=d.get("suppressed_by"),
            detail=dict(d.get("detail", {})),
        )


#: Dimensions considered by the rollup, in report order.
DIMENSIONS = (
    "existence",
    "reporter_valid",
    "case_name",
    "court",
    "year",
    "pincite",
    "quote",
    "quote_source",
    #: Calibrated against the document's own agreement with its sources rather
    #: than against a constant. Only ever REVIEW; see verify_agreement.py.
    "quote_agreement",
    "proposition",
    "treatment",
    "precedential",
    "jurisdiction",
    # Statutory citations carry their own dimensions rather than borrowing the
    # case-shaped ones, which do not apply to them.
    "statute_parts",
    "statute_currency",
)


def rollup(checks: dict[str, CheckResult]) -> Overall:
    """Collapse per-dimension verdicts into one overall verdict (spec 4.3).

    Ordering is deliberate and load-bearing:

    1. Any ``FAIL`` -> ``FLAGGED``. A ``FAIL`` always carries evidence, so this
       branch is never reachable from mere absence.
    2. Existence absent -> ``UNVERIFIED``. This is the P1 firewall: a citation
       we could not find is routed away from ``FLAGGED`` entirely.
    3. Anything unresolved -> ``REVIEW``.
    4. All applicable dimensions ``PASS`` -> ``VERIFIED``.
    """
    verdicts = {name: c.verdict for name, c in checks.items()}

    if not verdicts or all(v is Verdict.PENDING for v in verdicts.values()):
        return Overall.PENDING

    if any(v in FABRICATION_SIGNALS for v in verdicts.values()):
        return Overall.FLAGGED

    existence = verdicts.get("existence", Verdict.PENDING)
    if existence in (Verdict.NOT_FOUND, Verdict.OUT_OF_SCOPE):
        return Overall.UNVERIFIED

    if any(
        v in (Verdict.NOT_CHECKABLE, Verdict.AMBIGUOUS, Verdict.PENDING)
        for v in verdicts.values()
    ):
        return Overall.REVIEW

    if any(
        c.confidence == "medium" or c.confidence == "low"
        for c in checks.values()
        if c.confidence
    ):
        return Overall.REVIEW

    applicable = [v for v in verdicts.values() if v is not Verdict.NA]
    if applicable and all(v is Verdict.PASS for v in applicable):
        return Overall.VERIFIED

    return Overall.REVIEW


#: How favourable each verdict is to the citation. A later stage may move a
#: row DOWN this scale (it learned something worse) or leave it alone, but may
#: never move it UP. Raising a verdict late means a stage with less context
#: overwrote a decision made by a stage with more -- which is how a suppressed
#: row came back as "confirmed" against a candidate the tool had guessed.
STANDING = {
    Verdict.PASS: 5,
    Verdict.NA: 4,
    Verdict.NOT_CHECKABLE: 3,
    Verdict.AMBIGUOUS: 3,
    Verdict.OUT_OF_SCOPE: 3,
    Verdict.NOT_FOUND: 2,
    Verdict.FAIL: 1,
    Verdict.PENDING: 0,
}


class VerdictRegression(RuntimeError):
    """Raised when a stage tries to improve a verdict an earlier stage set."""


#: Severity order for report sorting.
#:
#: This deliberately departs from spec 5.11, which orders FLAGGED, REVIEW,
#: UNVERIFIED. Severity should track the work the reader must do, not how bad
#: the finding sounds. A FLAGGED citation arrives with its contradicting
#: evidence attached and is usually the fastest row to clear. An UNVERIFIED
#: one means leaving this tool and opening a commercial database -- the row
#: most likely to be skipped, and per spec 7.3 the one where a fabrication
#: hides from the free sources. It therefore ranks above REVIEW.
SEVERITY_ORDER = {
    Overall.FLAGGED: 0,
    Overall.UNVERIFIED: 1,
    Overall.REVIEW: 2,
    Overall.PENDING: 3,
    Overall.VERIFIED: 4,
}
