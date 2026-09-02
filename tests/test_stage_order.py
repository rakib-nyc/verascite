"""Stage ordering must not change verdicts.

Three separate bugs in this codebase were a later stage writing into a row an
earlier stage had already decided, each one making a citation look better than
the evidence supported. Fixing the fourth instance is not the goal; making the
class impossible is. The ledger refuses late improvements, and these tests run
the stages in the wrong order to prove it.
"""

from __future__ import annotations

import pytest

from verascite.clients.courtlistener import LookupResult
from verascite.extract import extract
from verascite.models import Document, LedgerEntry
from verascite.resolve import resolve
from verascite.verdicts import CheckResult, Overall, Verdict, VerdictRegression
from verascite.verify_existence import _apply_lookup, verify_existence
from verascite.verify_metadata import verify_metadata

BRIEF = "Harlow v. Fitzgerald, 457 U.S. 800, 818 (1982) controls here."


def ledger_for(text: str = BRIEF):
    document = Document(
        text=text, source_path="t", source_format="txt", page_map=[(0, len(text), 1)]
    )
    citations, notes = extract(document)
    return resolve(document, citations, notes)


# --- the invariant ------------------------------------------------------------


def entry() -> LedgerEntry:
    return next(iter(ledger_for()))


def test_pending_may_become_anything():
    e = entry()
    e.set_check("case_name", CheckResult(Verdict.FAIL, evidence="wrong case"))
    assert e.checks["case_name"].verdict is Verdict.FAIL


def test_a_verdict_may_be_downgraded():
    e = entry()
    e.set_check("case_name", CheckResult(Verdict.PASS, evidence="matched"))
    e.set_check("case_name", CheckResult(Verdict.FAIL, evidence="actually wrong"))
    assert e.checks["case_name"].verdict is Verdict.FAIL


def test_a_verdict_may_not_be_upgraded():
    e = entry()
    e.set_check("case_name", CheckResult(Verdict.NOT_FOUND, sources_consulted=["cl"]))
    with pytest.raises(VerdictRegression, match="would be upgraded"):
        e.set_check("case_name", CheckResult(Verdict.PASS, evidence="looks fine"))


def test_a_failed_row_may_not_be_raised():
    e = entry()
    e.set_check("case_name", CheckResult(Verdict.FAIL, evidence="different case"))
    with pytest.raises(VerdictRegression, match="already FAIL"):
        e.set_check("case_name", CheckResult(Verdict.NOT_CHECKABLE, reason="unsure"))


def test_a_suppressed_row_may_not_be_re_decided():
    e = entry()
    e.set_check(
        "case_name",
        CheckResult(Verdict.NOT_CHECKABLE, reason="blocked", suppressed_by="existence"),
    )
    with pytest.raises(VerdictRegression, match="suppressed by"):
        e.set_check("case_name", CheckResult(Verdict.PASS, evidence="matched"))


def test_override_is_permitted_but_recorded():
    e = entry()
    e.set_check("case_name", CheckResult(Verdict.FAIL, evidence="different case"))
    e.set_check(
        "case_name",
        CheckResult(Verdict.PASS, evidence="matched after manual review"),
        override="operator confirmed the name by hand",
    )
    assert e.checks["case_name"].verdict is Verdict.PASS
    assert e.checks["case_name"].detail["override"]
    assert e.checks["case_name"].detail["overrode"] == "FAIL"
    assert any("overridden" in note for note in e.notes)


def test_try_set_reports_refusal_instead_of_raising():
    e = entry()
    e.set_check("case_name", CheckResult(Verdict.FAIL, evidence="different case"))
    assert e.try_set_check("case_name", CheckResult(Verdict.PASS, evidence="x")) is False
    assert e.checks["case_name"].verdict is Verdict.FAIL


# --- the stages, run wrong ----------------------------------------------------


def _verdicts(ledger) -> dict:
    return {
        e.citation_id: {n: c.verdict for n, c in sorted(e.checks.items())}
        for e in ledger
    }


def _with_lookup(ledger, status: int, clusters: list[dict]):
    verify_existence(ledger, client=None, offline=True)
    for e in ledger:
        e.checks.pop("existence", None)
        _apply_lookup(
            e,
            LookupResult(
                citation=e.citation.normalized, status=status, clusters=clusters
            ),
        )
    return ledger


AMBIGUOUS_CLUSTERS = [
    {"id": 1, "case_name": "Alpha v. Beta", "date_filed": "1982-06-24"},
    {"id": 2, "case_name": "Gamma v. Delta", "date_filed": "1982-06-24"},
]


def test_metadata_cannot_undo_an_ambiguity_name_mismatch():
    """The live instance this guard was written for.

    A 300 whose candidates all bear different names is a name mismatch. The
    metadata stage would then compare the asserted name against the first
    candidate and, if it happened to match, quietly raise FAIL to PASS.
    """
    ledger = _with_lookup(ledger_for(), 300, AMBIGUOUS_CLUSTERS)
    e = next(iter(ledger))
    assert e.checks["case_name"].verdict is Verdict.FAIL

    verify_metadata(ledger)
    assert e.checks["case_name"].verdict is Verdict.FAIL
    assert e.overall is Overall.FLAGGED


def test_running_metadata_twice_changes_nothing():
    ledger = _with_lookup(ledger_for(), 300, AMBIGUOUS_CLUSTERS)
    verify_metadata(ledger)
    once = _verdicts(ledger)
    verify_metadata(ledger)
    verify_metadata(ledger)
    assert _verdicts(ledger) == once


def test_metadata_before_existence_does_not_change_the_outcome():
    """Wrong order must produce the same verdicts, or fail loudly."""
    correct = ledger_for()
    verify_existence(correct, client=None, offline=True)
    verify_metadata(correct)

    reversed_order = ledger_for()
    verify_metadata(reversed_order)
    verify_existence(reversed_order, client=None, offline=True)
    verify_metadata(reversed_order)

    assert _verdicts(reversed_order) == _verdicts(correct)


def test_existence_rerun_does_not_resurrect_a_reporter_failure():
    ledger = ledger_for("Bogus v. Nobody, 33 Umbrella 422 (2020).")
    verify_existence(ledger, client=None, offline=True)
    before = _verdicts(ledger)
    verify_existence(ledger, client=None, offline=True)
    verify_metadata(ledger)
    e = next(iter(ledger))
    assert e.checks["reporter_valid"].verdict is Verdict.FAIL
    assert _verdicts(ledger)["cite_0000"]["reporter_valid"] == before["cite_0000"]["reporter_valid"]
