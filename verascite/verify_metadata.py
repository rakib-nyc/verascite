"""Stage 4b: does the retrieved record match what the document asserts?

This catches failure mode 3 -- case name mismatch -- which the spec singles
out as the mode most likely to survive a lazy check, because "the citation
exists" is true. The canonical example: a filing citing "Tinch v. Video
Indus. Servs., Inc., 2019 WL 1396975" where that identifier actually belongs
to *Dolberry v. Jakob*.

Name matching therefore has to be genuinely normalized rather than compared
raw, and it must never auto-pass on a name that merely looks similar.
"""

from __future__ import annotations

import difflib
import re
from typing import Callable, Optional

from courts_db import find_court_by_id

from .config import NAME_PASS_THRESHOLD as PASS_THRESHOLD
from .config import NAME_REVIEW_THRESHOLD as REVIEW_THRESHOLD
from .ledger import Ledger
from .models import CiteKind, LedgerEntry
from .names import name_similarity, party_tokens
from .verdicts import CheckResult, Verdict

#: Retained name for the token normaliser, which moved to verascite.names.
normalize_party_tokens = party_tokens

CL_SOURCE = "courtlistener_cluster"


def _check_case_name(entry: LedgerEntry, corroborating_names: tuple = ()) -> CheckResult:
    asserted = entry.citation.case_name
    resolution = entry.resolved_to
    # CourtListener carries three names per cluster -- case_name,
    # case_name_short, case_name_full -- and they are genuinely different
    # strings. A consolidated case, a caption renamed on appeal, or a party
    # substitution can leave the brief matching one and not the others. All
    # three are legitimate names for the same case, so the best match wins.
    # Every name any source holds for this citation. A caption renamed on
    # appeal, a consolidated case, or a party substitution leaves the brief
    # matching one rendering and not another; all of them are legitimate.
    #
    # Names from a second database matter most. The literature is emphatic
    # that a single-source verifier is capped on precision, and that
    # disagreement between two independent databases about one citation is
    # evidence the citation is *unverifiable* rather than fabricated.
    candidates = [
        n for n in (
            getattr(resolution, "case_name", None) if resolution else None,
            getattr(resolution, "case_name_short", None) if resolution else None,
            getattr(resolution, "case_name_full", None) if resolution else None,
            *corroborating_names,
        )
        if isinstance(n, str) and n.strip()
    ]
    actual = max(
        candidates, key=lambda n: name_similarity(asserted, n), default=None
    ) if (candidates and asserted) else (candidates[0] if candidates else None)

    if not asserted:
        return CheckResult(Verdict.NA, reason="no case name asserted in the document")
    if not actual:
        return CheckResult(
            Verdict.NOT_CHECKABLE,
            reason="the retrieved record carries no case name to compare against",
            sources_consulted=[CL_SOURCE],
        )

    # A single distinctive token -- a bare surname in a short form, "Grigsby"
    # against "In re Miracle Church of God in Christ" -- is weak evidence in
    # both directions. Agreement on a common surname proves little and
    # disagreement can simply mean the brief named the other party, or a party
    # the caption drops. It may inform a review; it may not carry a FAIL.
    asserted_tokens = party_tokens(asserted)
    actual_tokens = party_tokens(actual)
    thin = min(len(asserted_tokens), len(actual_tokens)) < 2

    score = name_similarity(asserted, actual)
    detail = {
        "asserted": asserted,
        "retrieved": actual,
        "score": round(score, 3),
        "asserted_tokens": sorted(normalize_party_tokens(asserted)),
        "retrieved_tokens": sorted(normalize_party_tokens(actual)),
    }

    if score >= PASS_THRESHOLD:
        return CheckResult(
            Verdict.PASS,
            evidence=f"case name matches after normalization (score {score:.2f})",
            sources_consulted=[CL_SOURCE],
            detail=detail,
        )
    if thin:
        return CheckResult(
            Verdict.NOT_CHECKABLE,
            reason=(
                f"the citation names too little to compare: {asserted!r} against "
                f"{actual!r}. A single party name cannot establish that this is a "
                "different case. Confirm by hand."
            ),
            sources_consulted=[CL_SOURCE],
            detail=detail,
        )

    if score >= REVIEW_THRESHOLD:
        return CheckResult(
            Verdict.NOT_CHECKABLE,
            reason=(
                f"case name is a partial match (score {score:.2f}). Document says "
                f"{asserted!r}; the retrieved record is {actual!r}. Confirm by hand."
            ),
            sources_consulted=[CL_SOURCE],
            detail=detail,
        )
    return CheckResult(
        Verdict.FAIL,
        evidence=(
            f"case name does not match the authority at this citation. The "
            f"document cites {asserted!r}, but {entry.citation.normalized} is "
            f"{actual!r} (normalized overlap {score:.2f})."
        ),
        sources_consulted=[CL_SOURCE],
        detail=detail,
    )


def _court_label(court_id: Optional[str]) -> Optional[str]:
    if not court_id:
        return None
    try:
        matches = find_court_by_id(court_id)
    except Exception:  # pragma: no cover - courts-db lookup guard
        return None
    if not matches:
        return None
    return matches[0].get("citation_string") or matches[0].get("name")


def _check_court(entry: LedgerEntry) -> CheckResult:
    asserted = entry.citation.court
    actual = entry.resolved_to.court_id if entry.resolved_to else None

    if not asserted:
        return CheckResult(Verdict.NA, reason="no court asserted in the document")
    if not actual:
        return CheckResult(
            Verdict.NOT_CHECKABLE,
            reason="the retrieved record carries no court identifier",
            sources_consulted=[CL_SOURCE],
        )

    if asserted == actual:
        return CheckResult(
            Verdict.PASS,
            evidence=f"court {actual} matches ({_court_label(actual) or actual})",
            sources_consulted=[CL_SOURCE],
        )
    return CheckResult(
        Verdict.FAIL,
        evidence=(
            f"court mismatch: the document attributes this decision to "
            f"{_court_label(asserted) or asserted} ({asserted}), but the retrieved "
            f"record is from {_court_label(actual) or actual} ({actual}). This "
            "changes the precedential weight of the authority."
        ),
        sources_consulted=[CL_SOURCE],
        detail={"asserted": asserted, "retrieved": actual},
    )


def _check_year(entry: LedgerEntry) -> CheckResult:
    asserted = entry.citation.year
    filed = entry.resolved_to.date_filed if entry.resolved_to else None

    if not asserted:
        return CheckResult(Verdict.NA, reason="no year asserted in the document")
    if not filed:
        return CheckResult(
            Verdict.NOT_CHECKABLE,
            reason="the retrieved record carries no filing date",
            sources_consulted=[CL_SOURCE],
        )

    actual = str(filed)[:4]
    try:
        delta = abs(int(asserted) - int(actual))
    except ValueError:
        return CheckResult(
            Verdict.NOT_CHECKABLE,
            reason=f"could not parse years {asserted!r} / {actual!r}",
            sources_consulted=[CL_SOURCE],
        )

    if delta == 0:
        return CheckResult(
            Verdict.PASS,
            evidence=f"year {asserted} matches filing date {filed}",
            sources_consulted=[CL_SOURCE],
        )
    if delta == 1:
        # Decision year and reporter publication year genuinely differ, and
        # rehearing complicates it further. Not a failure on its own.
        return CheckResult(
            Verdict.NOT_CHECKABLE,
            reason=(
                f"year is off by one: document says {asserted}, filing date is "
                f"{filed}. Decision year and reporter publication year commonly "
                "differ by a year; confirm by hand."
            ),
            sources_consulted=[CL_SOURCE],
            detail={"asserted": asserted, "retrieved": actual},
        )
    return CheckResult(
        Verdict.FAIL,
        evidence=(
            f"year mismatch: the document says {asserted}, the retrieved record "
            f"was filed {filed} (a {delta}-year difference)."
        ),
        sources_consulted=[CL_SOURCE],
        detail={"asserted": asserted, "retrieved": actual},
    )


def _check_precedential(entry: LedgerEntry) -> CheckResult:
    status = entry.resolved_to.precedential_status if entry.resolved_to else None
    if not status:
        return CheckResult(
            Verdict.NOT_CHECKABLE,
            reason="the retrieved record does not state a precedential status",
            sources_consulted=[CL_SOURCE],
        )
    if status.lower() in ("published", "precedential"):
        return CheckResult(
            Verdict.PASS,
            evidence=f"CourtListener precedential_status = {status!r}",
            sources_consulted=[CL_SOURCE],
        )
    return CheckResult(
        Verdict.NOT_CHECKABLE,
        reason=(
            f"CourtListener precedential_status = {status!r}. Local rules may "
            "restrict citation of this disposition; check the forum's rules."
        ),
        sources_consulted=[CL_SOURCE],
    )


DIMENSION_NOUNS = {
    "case_name": "case name",
    "court": "deciding court",
    "year": "decision year",
    "precedential": "precedential status",
}

#: Checks whose failure explains why no record could be retrieved. Suppression
#: is only ever asserted from this table -- never inferred because two checks
#: happened to fail together (P3).
BLOCKING_CHECKS = ("reporter_valid", "existence")


def _blocking_check(entry: LedgerEntry) -> Optional[str]:
    """Name the upstream check that made metadata unanswerable, if any."""
    for name in BLOCKING_CHECKS:
        check = entry.checks.get(name)
        if check is None:
            continue
        if check.verdict in (
            Verdict.FAIL,
            Verdict.NOT_FOUND,
            Verdict.OUT_OF_SCOPE,
            Verdict.AMBIGUOUS,
            Verdict.NOT_CHECKABLE,
        ):
            return name
    return None


def _check_statute_parts(entry: LedgerEntry) -> None:
    """Record what was parsed out of a statutory citation.

    The fallback, not the check. ``verify_statute`` verifies the citation
    against the published text and runs first; where it has already decided a
    dimension this leaves it alone, because saying "parsed as title 42,
    section 1983" over the top of "that subsection is not in the official
    text" would replace a finding with a restatement of the input.

    It still runs when the statute stage did not -- offline, or with
    ``--no-statutes`` -- so a statutory citation is never simply silent.
    """
    citation = entry.citation
    if "statute_parts" in entry.checks and "statute_currency" in entry.checks:
        return
    parts = []
    if citation.title:
        parts.append(f"title {citation.title}")
    if citation.reporter:
        parts.append(citation.reporter)
    if citation.section:
        parts.append(f"section {citation.section}")
    entry.try_set_check(
        "statute_parts",
        CheckResult(
            Verdict.PASS if parts else Verdict.NOT_CHECKABLE,
            evidence=f"parsed as {', '.join(parts)}" if parts else None,
            reason=None if parts else "could not parse a title and section",
            sources_consulted=["eyecite"],
        ),
    )
    entry.try_set_check(
        "statute_currency",
        CheckResult(
            Verdict.OUT_OF_SCOPE,
            reason=(
                "statutes are amended, and whether the quoted text is current was "
                "not checked. Compare against the current text on GovInfo or eCFR."
            ),
        ),
    )


def verify_metadata(ledger: Ledger, corroboration: Optional[dict] = None) -> Ledger:
    """Compare asserted case name / court / year against the resolved record.

    ``corroboration`` maps a citation id to extra names a second source holds
    for that authority.
    """
    corroboration = corroboration or {}
    for entry in ledger:
        existence = entry.checks.get("existence")
        resolved = entry.resolved_to is not None

        # Statutes and secondary sources have no case name and no court.
        # Reporting those dimensions as "could not be checked" describes a tool
        # that does not know what it is looking at.
        if entry.citation.kind in (CiteKind.LAW, CiteKind.JOURNAL):
            noun = "statutory" if entry.citation.kind is CiteKind.LAW else "secondary-source"
            for dimension in ("case_name", "court", "year", "precedential"):
                entry.try_set_check(
                    dimension,
                    CheckResult(
                        Verdict.NA,
                        reason=f"a {noun} citation has no {DIMENSION_NOUNS[dimension]}",
                    ),
                )
            _check_statute_parts(entry)
            continue

        if not resolved:
            reason = (
                "no record was retrieved for this citation, so its metadata "
                "cannot be compared against anything"
            )
            cause = _blocking_check(entry)
            for dimension in ("case_name", "court", "year", "precedential"):
                if dimension not in entry.checks:
                    entry.try_set_check(
                        dimension,
                        CheckResult(
                            Verdict.NOT_CHECKABLE, reason=reason, suppressed_by=cause
                        ),
                    )
            continue

        # Back-references assert no metadata of their own.
        if entry.citation.kind in (CiteKind.ID, CiteKind.SUPRA, CiteKind.SHORT_CASE):
            for dimension in ("court", "year"):
                entry.try_set_check(
                    dimension,
                    CheckResult(
                        Verdict.NA,
                        reason="short-form citations assert no court or year",
                    ),
                )
            # A back-reference names the same authority as its antecedent, so
            # it takes the antecedent's verdict rather than computing its own.
            #
            # Computing one independently is unreliable, and measurably so: on
            # the benchmark, 27 of 46 false case-name failures were short forms
            # against only 9 of 43 true ones -- three quarters of short-form
            # failures were wrong. The cause is antecedent binding. A short
            # form bound to the wrong full citation inherits the wrong name and
            # then "mismatches" a record it was never about. Inheriting keeps
            # the finding attached to the citation that actually carries the
            # evidence.
            if entry.antecedent_id:
                _inherit_case_name(ledger, entry)
            elif entry.citation.case_name:
                _set_unless_suppressed(entry, "case_name", _check_case_name)
            elif "case_name" not in entry.checks:
                entry.set_check("case_name", CheckResult(Verdict.NA))
            _set_unless_suppressed(entry, "precedential", _check_precedential)
            continue

        # A dimension already marked unanswerable by an upstream check must not
        # be recomputed here. Confirming a case name against a candidate the
        # tool picked from an ambiguous set is confidence it has not earned.
        extra = tuple(corroboration.get(entry.citation_id, ()))
        _set_unless_suppressed(
            entry, "case_name", lambda e: _check_case_name(e, extra)
        )
        _set_unless_suppressed(entry, "court", _check_court)
        _set_unless_suppressed(entry, "year", _check_year)
        _set_unless_suppressed(entry, "precedential", _check_precedential)

    return ledger


def _inherit_case_name(ledger: Ledger, entry: LedgerEntry) -> None:
    """Take the antecedent's case-name verdict, or decline to judge."""
    parent = ledger.get(entry.antecedent_id) if entry.antecedent_id else None
    parent_check = parent.checks.get("case_name") if parent else None

    if parent_check is None or parent_check.verdict in (Verdict.PENDING, Verdict.NA):
        entry.try_set_check(
            "case_name",
            CheckResult(
                Verdict.NA,
                reason=(
                    "a short-form citation names no case of its own; see the full "
                    "citation it refers to"
                ),
            ),
        )
        return

    prefix = f"inherited from {parent.citation_id}: "
    entry.try_set_check(
        "case_name",
        CheckResult(
            verdict=parent_check.verdict,
            evidence=prefix + parent_check.evidence if parent_check.evidence else None,
            reason=prefix + parent_check.reason if parent_check.reason else None,
            sources_consulted=list(parent_check.sources_consulted),
            detail=dict(parent_check.detail),
        ),
    )


def _set_unless_suppressed(entry: LedgerEntry, dimension: str, checker) -> None:
    """Offer a verdict; the ledger refuses it if an earlier stage decided better.

    This is the only place metadata writes, and it writes through the guarded
    setter deliberately. A row that existence already suppressed, or already
    failed, must not be re-decided here on the strength of a candidate record
    that the earlier stage did not consider settled.
    """
    entry.try_set_check(dimension, checker(entry))
