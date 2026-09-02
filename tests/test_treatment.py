"""Treatment triage: what it reports, and what it refuses to.

The design decision under test is a negative one. Keyword triage over citing
opinions was measured before being built and rejected: 2,489 of the 14,623
opinions citing Twombly contain negative-treatment language somewhere in their
text. A check that fires on one citation in six, unable to say which, would
teach a reader to ignore it.
"""

from __future__ import annotations

from verascite.models import CiteKind, LedgerEntry, Location, RawCitation, Resolution
from verascite.verdicts import CheckResult, Overall, Verdict
from verascite.verify_treatment import citing_opinions_url, verify_treatment


def entry_with(resolution=None) -> LedgerEntry:
    return LedgerEntry(
        citation_id="cite_0000",
        citation=RawCitation(
            kind=CiteKind.FULL_CASE, raw_text="A v. B, 550 U.S. 544 (2007)",
            location=Location(0, 27),
        ),
        resolved_to=resolution,
    )


RESOLVED = Resolution(
    source="courtlistener", cluster_id=145730,
    case_name="Bell Atlantic Corp. v. Twombly", precedential_status="Published",
)


def test_treatment_is_never_asserted_as_good_law():
    entry = entry_with(RESOLVED)
    verify_treatment(entry, citation_count=14623)
    check = entry.checks["treatment"]
    assert check.verdict is Verdict.NOT_CHECKABLE
    assert "good law" in check.reason
    assert "not a citator" in check.reason


def test_treatment_never_passes_and_never_fails():
    """There is no evidence available that could justify either."""
    for count in (0, 1, 14623):
        entry = entry_with(RESOLVED)
        verify_treatment(entry, citation_count=count)
        assert entry.checks["treatment"].verdict is Verdict.NOT_CHECKABLE


def test_treatment_alone_never_flags_a_citation():
    entry = entry_with(RESOLVED)
    verify_treatment(entry, citation_count=14623)
    assert entry.overall is not Overall.FLAGGED


def test_the_reader_is_given_somewhere_to_look():
    entry = entry_with(RESOLVED)
    verify_treatment(entry, citation_count=14623)
    check = entry.checks["treatment"]
    assert "courtlistener.com" in check.reason
    assert check.detail["citing_opinions_url"]
    assert "14,623" in check.reason


def test_named_citators_are_pointed_at():
    entry = entry_with(RESOLVED)
    verify_treatment(entry, citation_count=1)
    reason = entry.checks["treatment"].reason
    for citator in ("Shepard's", "KeyCite", "BCite"):
        assert citator in reason


def test_an_unresolved_citation_says_treatment_is_unknown():
    entry = entry_with(None)
    verify_treatment(entry)
    check = entry.checks["treatment"]
    assert check.verdict is Verdict.NOT_CHECKABLE
    assert "unknown" in check.reason


def test_citing_url_is_well_formed():
    assert citing_opinions_url(145730).endswith("type=o&order_by=dateFiled+desc")
    assert citing_opinions_url(None) is None


def test_treatment_cannot_overwrite_an_earlier_verdict():
    entry = entry_with(RESOLVED)
    entry.set_check("treatment", CheckResult(Verdict.FAIL, evidence="overruled by X"))
    verify_treatment(entry, citation_count=5)
    assert entry.checks["treatment"].verdict is Verdict.FAIL
