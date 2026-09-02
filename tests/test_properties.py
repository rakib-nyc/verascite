"""Property-based tests.

Example-based tests confirm what I already suspected. These look for what I
did not: every invariant here is a claim about *all* inputs, checked against
inputs nobody chose.
"""

from __future__ import annotations

import json
import pathlib

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from verascite.extract import collapse_whitespace, extract, quote_spans
from verascite.ledger import Ledger
from verascite.models import Document
from verascite.report import render_report
from verascite.resolve import resolve
from verascite.safety import flatten_for_output, strip_xml_illegal
from verascite.verdicts import DIMENSIONS, CheckResult, Overall, Verdict, rollup
from verascite.verify_existence import verify_existence
from verascite.verify_metadata import verify_metadata

SLOW = settings(
    max_examples=150, deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)

#: Text built from fragments that actually occur in briefs, so the generator
#: spends its budget near the citation grammar rather than on random noise.
FRAGMENTS = st.sampled_from([
    "Harlow v. Fitzgerald, 457 U.S. 800, 818 (1982)", "See ", "id. at 4", "supra",
    "33 Umbrella 422", "42 U.S.C. § 1983", '"a quoted passage of some length"',
    " v. ", ", ", ". ", "\n\n", "*", "In re ", "2019 WL 1396975", "“", "”",
    "Fed. Reg.", "84 Or. L. Rev. 227", "(9th Cir. 2019)", "|", "#", "\x00", "'",
])
documents = st.lists(FRAGMENTS, max_size=40).map("".join)


def audit(text: str) -> Ledger:
    document = Document(text=text, source_path="p", source_format="txt",
                        page_map=[(0, len(text), 1)])
    citations, notes = extract(document)
    ledger = resolve(document, citations, notes)
    verify_existence(ledger, client=None, offline=True)
    verify_metadata(ledger)
    return ledger


# --- extraction ---------------------------------------------------------------


@given(documents)
@SLOW
def test_extraction_never_raises(text):
    audit(text)


@given(documents)
@SLOW
def test_every_citation_span_addresses_the_document(text):
    """A verdict points at a location a reader can find. Always."""
    document = Document(text=text, source_path="p", source_format="txt",
                        page_map=[(0, len(text), 1)])
    for citation in extract(document)[0]:
        start, end = citation.location.char_start, citation.location.char_end
        assert 0 <= start <= end <= len(text)
        assert text[start:end] == citation.raw_text


@given(st.text(max_size=400))
@SLOW
def test_whitespace_collapse_maps_back_exactly(text):
    clean, index_map = collapse_whitespace(text)
    assert len(index_map) == len(clean) + 1
    for i, char in enumerate(clean):
        if not char.isspace():
            assert text[index_map[i]] == char


@given(st.text(max_size=300))
@SLOW
def test_quote_spans_are_well_formed(text):
    for start, end, body in quote_spans(text):
        assert 0 <= start < end <= len(text)
        assert body == text[start + 1 : end - 1]


# --- the P1 firewall, over generated verdict combinations ---------------------


def result(verdict: Verdict) -> CheckResult:
    if verdict is Verdict.FAIL:
        return CheckResult(verdict, evidence="contradicted")
    if verdict is Verdict.NOT_FOUND:
        return CheckResult(verdict, sources_consulted=["courtlistener"])
    if verdict in (Verdict.NOT_CHECKABLE, Verdict.OUT_OF_SCOPE):
        return CheckResult(verdict, reason="stated")
    return CheckResult(verdict)


@given(st.dictionaries(st.sampled_from(DIMENSIONS), st.sampled_from(list(Verdict)),
                       max_size=len(DIMENSIONS)))
@SLOW
def test_flagged_requires_a_fail_for_every_combination(verdicts):
    checks = {name: result(v) for name, v in verdicts.items()}
    flagged = rollup(checks) is Overall.FLAGGED
    assert flagged == any(v is Verdict.FAIL for v in verdicts.values())


@given(st.dictionaries(st.sampled_from(DIMENSIONS),
                       st.sampled_from([Verdict.NOT_FOUND, Verdict.NOT_CHECKABLE,
                                        Verdict.OUT_OF_SCOPE, Verdict.AMBIGUOUS,
                                        Verdict.NA, Verdict.PENDING, Verdict.PASS]),
                       min_size=1, max_size=len(DIMENSIONS)))
@SLOW
def test_absence_alone_can_never_flag(verdicts):
    """No amount of not-knowing adds up to an accusation."""
    assert rollup({n: result(v) for n, v in verdicts.items()}) is not Overall.FLAGGED


@given(documents)
@SLOW
def test_a_flagged_citation_always_carries_evidence(text):
    for entry in audit(text):
        if entry.overall is Overall.FLAGGED:
            failures = [c for c in entry.checks.values() if c.verdict is Verdict.FAIL]
            assert failures
            assert all((c.evidence or "").strip() for c in failures)


# --- ledger and report --------------------------------------------------------


@given(documents)
@SLOW
def test_ledger_round_trips(text):
    import tempfile

    ledger = audit(text)
    with tempfile.TemporaryDirectory() as directory:
        path = pathlib.Path(directory) / "ledger.json"
        reloaded = Ledger.load(ledger.checkpoint(path))
        assert [e.citation_id for e in reloaded] == [e.citation_id for e in ledger]
        assert [e.overall for e in reloaded] == [e.overall for e in ledger]


@given(documents)
@SLOW
def test_the_report_always_renders_and_keeps_its_disclosure(text):
    report = render_report(audit(text), generated_at="fixed")
    assert "evidence package" in report
    assert "does not mean fabricated" in report
    assert "FABRICATED" not in report


@given(documents)
@SLOW
def test_report_tables_are_never_broken_by_document_text(text):
    for row in render_report(audit(text), generated_at="fixed").splitlines():
        if row.startswith("|") and not set(row) <= set("|-: "):
            assert row.count("|") == 4


# --- sanitisers ---------------------------------------------------------------


@given(st.text(max_size=500))
@SLOW
def test_flattened_output_is_always_safe_to_interpolate(text):
    out = flatten_for_output(text)
    assert "|" not in out and "\n" not in out and "\r" not in out
    assert not any(ord(c) < 32 or ord(c) == 127 for c in out)


@given(st.text(max_size=500))
@SLOW
def test_stripping_leaves_only_xml_representable_characters(text):
    out = strip_xml_illegal(text)
    for char in out:
        code = ord(char)
        assert code in (0x9, 0xA, 0xD) or 0x20 <= code <= 0xD7FF or code >= 0xE000
