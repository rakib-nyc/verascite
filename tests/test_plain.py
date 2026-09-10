"""The plain-language report.

Written for self-represented litigants, who file the majority of documents in
which courts have found fabricated citations. The risk in this file is not that
the prose is clumsy -- it is that a frightened non-lawyer reads "could not find"
as "you have been caught". Every test here guards that reading.
"""

import re

import pytest

from verascite.ledger import Ledger
from verascite.models import CiteKind, LedgerEntry, Location, RawCitation
from verascite.plain import FLAGGED_BY_CHECK, render_plain
from verascite.verdicts import CheckResult, Overall, Verdict


def make(raw, check=None, cid="c1"):
    e = LedgerEntry(
        citation_id=cid,
        citation=RawCitation(kind=CiteKind.FULL_CASE, raw_text=raw,
                             location=Location(char_start=0, char_end=len(raw))),
    )
    if check:
        e.set_check(*check)
    return e


def ledger_of(*entries):
    led = Ledger(document_path="b.md", document_format="md")
    for i, e in enumerate(entries):
        e.citation_id = f"c{i}"
        led.add(e)
    return led


@pytest.fixture
def report(tmp_path):
    led = ledger_of(
        make("Smith v. Fictional Rep. Co., 88 Jurisprudentia 100", (
            "reporter_valid", CheckResult(Verdict.FAIL,
                evidence="reporter 'Jurisprudentia' does not appear in reporters-db"))),
        make("Tinch v. Video Indus. Servs., 2019 WL 1396975", (
            "existence", CheckResult(Verdict.NOT_FOUND, reason="not in the sources",
                sources_consulted=["courtlistener_citation_lookup"]))),
        make("Bell Atlantic Corp. v. Twombly, 550 U.S. 544", (
            "existence", CheckResult(Verdict.PASS, evidence="one cluster returned"))),
    )
    return render_plain(led, tmp_path / "b.md")


# --- the governing rule, in language a non-lawyer will read correctly ---------

def test_it_says_outright_that_it_never_calls_a_citation_fake(report):
    assert "We never say a citation is fake" in report


def test_could_not_find_is_explained_as_the_archive_not_the_citation(report):
    assert "This does not mean it is fake" in report
    assert "not in free archives" in report


def test_no_sentence_calls_a_citation_fabricated(report):
    for line in report.splitlines():
        low = line.lower()
        if "fake" in low or "fabricat" in low or "invent" in low:
            assert ("not mean it is fake" in low or "never say" in low
                    or "invent court cases" in low or "was invented" in low), line


def test_it_warns_about_the_real_risk_without_accusing_the_reader(report):
    assert "invent court cases that do not exist" in report


# --- it must not read as advice ----------------------------------------------

def test_it_disclaims_legal_advice_at_the_top(report):
    assert "not legal advice" in report[:600]


def test_it_points_to_human_help(report):
    assert "self-help" in report or "legal aid" in report


def test_it_says_the_reader_is_responsible(report):
    assert "responsible for what you file" in report


def test_it_certifies_nothing(report):
    assert "certifies nothing" in report


# --- accuracy of the explanation ---------------------------------------------

def test_an_invented_reporter_is_not_described_as_a_retrieved_mismatch(report):
    """Saying 'we found the case and it differs' when no such reporter exists
    is a small lie that costs the reader's trust."""
    assert "The books this case claims to be published in do not exist." in report
    section = report.split("Jurisprudentia 100")[1].split("###")[0]
    assert "We found the real document" not in section


def test_each_flagged_check_has_its_own_explanation():
    for name, (headline, advice) in FLAGGED_BY_CHECK.items():
        assert headline and advice
        assert not headline.endswith(("NOT_CHECKABLE", "FAIL"))


def test_verified_does_not_promise_good_law(tmp_path):
    """A confirmed citation must not read as 'this authority is safe to rely on'."""
    led = ledger_of(make("Bell Atlantic Corp. v. Twombly, 550 U.S. 544"))
    for name in ("existence", "reporter_valid", "case_name"):
        list(led)[0].set_check(name, CheckResult(Verdict.PASS, evidence="confirmed"))
    out = render_plain(led, tmp_path / "b.md")
    assert "Citations that checked out" in out
    assert "does not mean the case is still good law" in out


def test_it_lists_what_it_did_not_do(report):
    assert "still good law" in report
    assert "does not give legal advice" in report


# --- readability --------------------------------------------------------------

def test_no_internal_verdict_vocabulary_leaks_into_the_prose(report):
    for token in ("NOT_CHECKABLE", "OUT_OF_SCOPE", "AMBIGUOUS", "PENDING",
                  "pincite", "precedential"):
        assert token not in report, f"{token!r} leaked into the plain report"


def test_every_citation_appears(report):
    for fragment in ("Jurisprudentia", "2019 WL 1396975", "Twombly"):
        assert fragment in report


def test_an_empty_ledger_still_renders(tmp_path):
    out = render_plain(ledger_of(), tmp_path / "b.md")
    assert "not legal advice" in out
