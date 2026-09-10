"""How much of a document the sources could speak to.

Coverage is a statement about the archives, never about the document. Most of
these tests exist to hold that line: a low figure must not be expressible as a
doubt about the citations, and a run that consulted nothing must not report
coverage at all.
"""

import pytest

from verascite.coverage import Coverage, measure
from verascite.ledger import Ledger
from verascite.models import CiteKind, LedgerEntry, Location, RawCitation
from verascite.verdicts import CheckResult, Verdict


def entry(kind=CiteKind.FULL_CASE, existence=None, cid="c"):
    e = LedgerEntry(
        citation_id=cid,
        citation=RawCitation(kind=kind, raw_text="X v. Y, 1 U.S. 1",
                             location=Location(char_start=0, char_end=16)),
    )
    if existence is not None:
        e.set_check("existence", existence)
    return e


def ledger_of(*entries, sources=("courtlistener_citation_lookup",)):
    led = Ledger(document_path="b.md", document_format="md")
    led.sources_available = list(sources)
    for i, e in enumerate(entries):
        e.citation_id = f"c{i}"
        led.add(e)
    return led


def found():
    return CheckResult(Verdict.PASS, evidence="one cluster returned")


def absent():
    return CheckResult(Verdict.NOT_FOUND, reason="not in the sources",
                       sources_consulted=["courtlistener_citation_lookup"])


# --- counting -----------------------------------------------------------------

def test_it_separates_reachable_from_absent():
    c = measure(ledger_of(entry(existence=found()), entry(existence=absent()),
                          entry(existence=absent())))
    assert (c.reachable, c.absent) == (1, 2)
    assert c.rate == pytest.approx(1 / 3)


def test_a_contradicted_citation_counts_as_reachable():
    """A source that contradicts the document is a source that spoke to it."""
    c = measure(ledger_of(entry(existence=CheckResult(
        Verdict.FAIL, evidence="the record names a different case"))))
    assert c.reachable == 1
    assert c.absent == 0


def test_ambiguous_is_neither_reachable_nor_absent():
    c = measure(ledger_of(entry(existence=CheckResult(
        Verdict.AMBIGUOUS, evidence="14 candidates"))))
    assert c.indeterminate == 1
    assert c.reachable == 0 and c.absent == 0


def test_statutory_citations_are_counted_apart():
    """They are checked against different sources; pooling describes neither."""
    c = measure(ledger_of(entry(kind=CiteKind.LAW, existence=found()),
                          entry(existence=found())))
    assert c.statutory == 1
    assert c.reachable == 1


def test_an_unchecked_citation_is_not_counted_as_absent():
    c = measure(ledger_of(entry(existence=None)))
    assert c.not_consulted == 1
    assert c.absent == 0


def test_an_empty_document_reports_no_coverage():
    c = measure(ledger_of())
    assert c.rate is None
    assert "No citations" in c.statement()


# --- the line that must not be crossed ----------------------------------------

def test_coverage_never_describes_the_citations_as_doubtful():
    """The words may appear only where they are being denied."""
    c = measure(ledger_of(*[entry(existence=absent()) for _ in range(5)]))
    for text in (c.statement(), c.headline(), c.to_dict()["caveat"]):
        for sentence in text.replace(";", ".").split("."):
            low = sentence.lower()
            if any(w in low for w in ("fabricated", "fake", "suspicious",
                                      "doubtful", "invalid")):
                assert " not " in low, f"undenied doubt cast on citations: {sentence!r}"


def test_the_caveat_says_absence_is_about_the_archives():
    c = measure(ledger_of(entry(existence=absent())))
    assert "describes the sources, not the document" in c.to_dict()["caveat"]


def test_absence_is_described_as_a_limit_not_a_finding():
    c = measure(ledger_of(entry(existence=found()), entry(existence=absent())))
    assert "not a finding about the citation" in c.statement()


def test_a_run_that_consulted_nothing_says_so_rather_than_claiming_coverage():
    c = measure(ledger_of(entry(existence=None), entry(existence=None)))
    assert "No source was consulted" in c.statement()
    assert c.rate is None


# --- the headline -------------------------------------------------------------

def test_no_headline_when_coverage_is_good():
    c = measure(ledger_of(*[entry(existence=found()) for _ in range(9)],
                          entry(existence=absent())))
    assert c.headline() == ""


def test_headline_fires_when_a_large_share_is_absent():
    c = measure(ledger_of(*[entry(existence=absent()) for _ in range(5)],
                          entry(existence=found())))
    head = c.headline()
    assert head
    assert "not examined" in head
    assert "not the quality of the citations" in head


def test_the_dict_is_machine_readable_three_state():
    c = measure(ledger_of(entry(existence=found()), entry(existence=absent())))
    d = c.to_dict()
    for key in ("reachable", "absent_from_sources", "indeterminate", "sources_consulted"):
        assert key in d
