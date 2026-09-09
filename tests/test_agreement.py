"""Document-calibrated quotation agreement.

This check replaces one that was measured and rejected at 17.2% precision. It
earns its place only if it declines whenever the document cannot support it, so
that is what most of these test.
"""

import pytest

from verascite.ledger import Ledger
from verascite.models import CiteKind, LedgerEntry, Location, RawCitation
from verascite.verdicts import CheckResult, Verdict
from verascite.verify_agreement import (
    MIN_DOCUMENT_AGREEMENT,
    MIN_QUOTES_TO_CALIBRATE,
    OUTLIER_MARGIN,
    verify_agreement,
)


def ledger_with(ratios: list[float]) -> Ledger:
    """A ledger whose quotations were located at the given similarities."""
    ledger = Ledger(document_path="b.md", document_format="md")
    for index, ratio in enumerate(ratios):
        entry = LedgerEntry(
            citation_id=f"c{index}",
            citation=RawCitation(kind=CiteKind.FULL_CASE, raw_text=f"X v. Y, {index} U.S. 1",
                                 location=Location(char_start=index, char_end=index + 5)),
        )
        entry.set_check("quote", CheckResult(
            Verdict.PASS, evidence="located",
            detail={"quotes": [{"found": True, "similarity": ratio, "quote": "..."}]}))
        ledger.add(entry)
    return ledger


def flagged(ledger) -> list[str]:
    return [e.citation_id for e in ledger if "quote_agreement" in e.checks]


# --- it declines rather than guessing ----------------------------------------

def test_it_declines_on_too_few_quotations():
    """Two ratios are not a distribution."""
    ledger = ledger_with([1.0, 0.90])
    reading = verify_agreement(ledger)
    assert reading.declined
    assert flagged(ledger) == []


def test_it_declines_on_a_noisy_document():
    """Below the floor, a one-word change is indistinguishable from noise."""
    ledger = ledger_with([0.95, 0.94, 0.96, 0.95, 0.93, 0.95, 0.90])
    reading = verify_agreement(ledger)
    assert reading.declined
    assert "below the" in reading.declined
    assert flagged(ledger) == []


def test_a_clean_document_with_no_outlier_flags_nothing():
    ledger = ledger_with([1.0] * 8)
    reading = verify_agreement(ledger)
    assert not reading.declined
    assert reading.flagged == 0


def test_no_quotations_at_all_is_not_an_error():
    reading = verify_agreement(Ledger(document_path="x", document_format="md"))
    assert reading.quotations == 0
    assert reading.declined


# --- it finds the outlier it is for ------------------------------------------

def test_it_flags_the_quotation_that_stands_out():
    ledger = ledger_with([1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0.96])
    reading = verify_agreement(ledger)
    assert reading.flagged == 1
    assert flagged(ledger) == ["c6"]


def test_a_quotation_just_inside_the_margin_is_left_alone():
    inside = 1.0 - OUTLIER_MARGIN + 0.001
    ledger = ledger_with([1.0] * 6 + [inside])
    assert verify_agreement(ledger).flagged == 0


def test_the_baseline_is_the_median_so_one_bad_quote_cannot_move_it():
    """A document containing a real misquote must not recalibrate around it."""
    ledger = ledger_with([1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0.5])
    reading = verify_agreement(ledger)
    assert reading.baseline == 1.0
    assert reading.flagged == 1


# --- it never produces a finding ---------------------------------------------

def test_it_never_produces_a_fail():
    """An anomaly in agreement is a prompt to read, not contradiction."""
    ledger = ledger_with([1.0] * 6 + [0.80])
    verify_agreement(ledger)
    for entry in ledger:
        check = entry.checks.get("quote_agreement")
        if check is not None:
            assert check.verdict is Verdict.NOT_CHECKABLE
            assert check.verdict is not Verdict.FAIL


def test_a_flagged_quotation_does_not_make_the_document_flagged():
    from verascite.verdicts import Overall
    ledger = ledger_with([1.0] * 6 + [0.80])
    verify_agreement(ledger)
    assert all(e.overall is not Overall.FLAGGED for e in ledger)


def test_it_says_plainly_that_it_is_not_a_finding():
    ledger = ledger_with([1.0] * 6 + [0.80])
    verify_agreement(ledger)
    reason = [e.checks["quote_agreement"].reason for e in ledger
              if "quote_agreement" in e.checks][0]
    assert "not a finding" in reason


def test_it_defers_to_the_quotation_check_when_that_already_failed():
    """The quotation check has retrieved text; this has only a distribution."""
    ledger = ledger_with([1.0] * 6 + [0.80])
    last = list(ledger)[-1]
    last.set_check("quote", CheckResult(
        Verdict.FAIL, evidence="altered quotation: 'do' -> 'suffice'",
        detail={"quotes": [{"found": True, "similarity": 0.80, "quote": "..."}]}),
        override="test setup")
    verify_agreement(ledger)
    assert "quote_agreement" not in last.checks


# --- only located quotations calibrate ---------------------------------------

def test_unlocated_quotations_do_not_drag_the_baseline_down():
    """A quotation never found says nothing about how this document transcribes."""
    ledger = ledger_with([1.0] * 7)
    stray = LedgerEntry(
        citation_id="stray",
        citation=RawCitation(kind=CiteKind.FULL_CASE, raw_text="Z v. Q, 9 U.S. 9",
                             location=Location(char_start=99, char_end=110)))
    stray.set_check("quote", CheckResult(
        Verdict.NOT_FOUND, reason="not located",
        sources_consulted=["courtlistener_opinion_text"],
        detail={"quotes": [{"found": False, "similarity": 0.2, "quote": "..."}]}))
    ledger.add(stray)
    reading = verify_agreement(ledger)
    assert reading.quotations == 7
    assert reading.baseline == 1.0


def test_the_reading_is_recorded_for_the_report():
    ledger = ledger_with([1.0] * 6 + [0.90])
    data = verify_agreement(ledger).to_dict()
    assert data["quotations_located"] == 7
    assert data["margin"] == OUTLIER_MARGIN
    assert "not a finding" in data["note"]
