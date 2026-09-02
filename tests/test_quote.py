"""Quote verification: normalization, near-miss detection, attribution.

Runs against a small hand-built opinion rather than the network, so the
thresholds and the diff machinery are exercised without a token. The
calibration numbers in verify_quote.py come from real CourtListener text --
see tests/test_quote_live.py, which is skipped without a token.
"""

from __future__ import annotations

import pytest

from verascite.clients.opinions import OpinionText, PageMark, parse_opinion, strip_markup
from verascite.extract import assign_quotes, extract, quote_spans
from verascite.models import CiteKind, Document
from verascite.resolve import resolve
from verascite.verdicts import Overall, Verdict
from verascite.verify_quote import (
    MATERIAL_ALTERATION_FLOOR,
    NEAR_MISS_FLOOR,
    check_quote,
    normalize_quote,
    verify_quote,
)

REAL = (
    "Factual allegations must be enough to raise a right to relief above the "
    "speculative level, and a formulaic recitation of the elements of a cause of "
    "action will not do, see Papasan v. Allain, 478 U.S. 265, 286 (1986). The "
    "court also observed that “the borrower bears the risk” in such "
    "arrangements, a principle drawn from earlier cases."
)
DISSENT = (
    "The transparent policy concern that drives the decision is the interest in "
    "protecting antitrust defendants from discovery costs."
)


def majority(text: str = REAL) -> OpinionText:
    return OpinionText(
        opinion_id=1, text=text, source_field="xml_harvard", type_label="majority",
        is_court_holding=True, author="Souter",
        page_marks=[PageMark("555", 0), PageMark("556", 200)],
    )


def dissent(text: str = DISSENT) -> OpinionText:
    return OpinionText(
        opinion_id=2, text=text, source_field="xml_harvard", type_label="dissent",
        is_court_holding=False, author="Stevens",
    )


def doc_of(text: str) -> Document:
    return Document(text=text, source_path="t", source_format="txt", page_map=[(0, len(text), 1)])


# --- normalization ------------------------------------------------------------


@pytest.mark.parametrize(
    "written",
    [
        "a formulaic recitation of the elements of a cause of action will not do",
        "A formulaic recitation of the elements of a cause of action will not do.",
        "[A] formulaic recitation of the elements of a cause of action will not do.",
        "a formulaic recitation of the elements of a cause of action will not do [emphasis added]",
        "a formulaic recitation of the elements of a cause of action will not do [sic]",
        "a formulaic recitation . . . of a cause of action will not do",
        "a formulaic recitation of the elements of a cause of action will not do",
    ],
)
def test_bracketed_alterations_do_not_trip_a_misquote(written):
    """Correct quoting practice must never be reported as a misquote."""
    finding = check_quote(written, [majority()])
    assert finding.found, f"{written!r} scored {finding.similarity}"


def test_terminal_punctuation_may_differ_from_the_source():
    """Twombly runs on with a comma; a brief ending the sentence is correct."""
    assert normalize_quote("will not do.") == normalize_quote("will not do,")


def test_curly_quotes_and_dashes_normalize():
    assert normalize_quote("“the rule—clear”") == normalize_quote('"the rule-clear"')


# --- the case the benchmark is built around -----------------------------------


def test_one_word_synonym_swap_is_caught_as_an_alteration():
    """LePhantomCite builds misquotes this way precisely to defeat detection."""
    finding = check_quote(
        "a formulaic recitation of the elements of a cause of action will not suffice",
        [majority()],
    )
    assert not finding.found
    assert finding.similarity >= MATERIAL_ALTERATION_FLOOR
    assert "suffice" in finding.diff


def test_two_word_swap_is_caught_as_an_alteration():
    finding = check_quote(
        "a formulaic listing of the elements of a legal claim will not do", [majority()]
    )
    assert not finding.found
    assert finding.similarity >= MATERIAL_ALTERATION_FLOOR


def test_an_unlocatable_quotation_is_reported_for_review_not_as_a_finding():
    """Measured: this verdict produced 45 false alarms on LePhantomCite."""
    ledger = entry_for('The Court said "wholly invented language appearing nowhere." Twombly, 550 U.S. 544 (2007).')
    entry = next(iter(ledger))
    verify_quote(entry, [majority()])
    assert entry.checks["quote"].verdict is Verdict.NOT_CHECKABLE
    assert entry.overall is not Overall.FLAGGED


def test_wholly_invented_language_scores_far_below_a_near_miss():
    finding = check_quote(
        "the pleading standard requires demonstrable evidentiary support at the outset",
        [majority()],
    )
    assert not finding.found
    assert finding.similarity < NEAR_MISS_FLOOR


def test_page_is_reported_when_pagination_exists():
    finding = check_quote("Factual allegations must be enough to raise a right", [majority()])
    assert finding.found and finding.page == "555"


def test_no_pagination_means_no_page_claim():
    unpaginated = OpinionText(opinion_id=3, text=REAL, source_field="plain_text")
    finding = check_quote("Factual allegations must be enough to raise a right", [unpaginated])
    assert finding.found and finding.page is None
    assert not finding.pagination_available


# --- attribution --------------------------------------------------------------


def entry_for(text: str):
    document = doc_of(text)
    citations, notes = extract(document)
    return resolve(document, citations, notes)


def test_dissent_language_attributed_to_the_court_fails():
    ledger = entry_for(
        'The Court held that "The transparent policy concern that drives the '
        'decision is the interest in protecting antitrust defendants." '
        "Twombly, 550 U.S. 544 (2007)."
    )
    entry = next(iter(ledger))
    verify_quote(entry, [majority(), dissent()])
    assert entry.checks["quote"].verdict is Verdict.PASS
    assert entry.checks["quote_source"].verdict is Verdict.FAIL
    assert "dissent" in entry.checks["quote_source"].evidence
    assert entry.overall is Overall.FLAGGED


def test_dissent_language_passes_when_the_citation_says_so():
    ledger = entry_for(
        'As noted, "The transparent policy concern that drives the decision is the '
        'interest in protecting antitrust defendants." Twombly, 550 U.S. 544, 570 '
        "(2007) (Stevens, J., dissenting)."
    )
    entry = next(iter(ledger))
    verify_quote(entry, [majority(), dissent()])
    assert entry.checks["quote_source"].verdict is Verdict.PASS


def test_quote_within_a_quotation_is_flagged_for_review():
    """The cited court was quoting someone else; the words are not its own."""
    ledger = entry_for('The court said "the borrower bears the risk" here. Smith v. Jones, 12 F.4th 99 (2021).')
    entry = next(iter(ledger))
    verify_quote(entry, [majority()])
    assert entry.checks["quote"].verdict is Verdict.PASS
    assert entry.checks["quote_source"].verdict is Verdict.NOT_CHECKABLE
    assert "quoting another source" in entry.checks["quote_source"].reason


def test_quotes_are_assigned_to_the_adjacent_citation_only():
    text = (
        'The Court held that "a formulaic recitation of the elements" is required. '
        "Twombly, 550 U.S. 544 (2007).\n\n"
        'The panel wrote "qualified immunity protects all but the plainly incompetent." '
        "Malley v. Briggs, 475 U.S. 335, 341 (1986)."
    )
    citations, _ = extract(doc_of(text))
    quotes = {c.normalized: c.quoted_language for c in citations}
    assert quotes["550 U.S. 544"] == ["a formulaic recitation of the elements"]
    assert quotes["475 U.S. 335"] == [
        "qualified immunity protects all but the plainly incompetent."
    ]


def test_a_quote_far_from_any_citation_is_left_unassigned():
    """Guessing an attribution risks checking a quote against the wrong opinion."""
    text = (
        'The brief opens by asserting "something entirely unrelated to any authority '
        'cited anywhere in this document at all." ' + "Filler sentence. " * 12
        + "Twombly, 550 U.S. 544 (2007)."
    )
    citations, _ = extract(doc_of(text))
    assert citations[0].quoted_language == []


def test_quote_pairing_is_global_not_windowed():
    """A window starting mid-quotation would pair a closing mark with an opening one."""
    text = 'He said "first quoted passage here." Then later she said "second quoted passage."'
    bodies = [body for _, _, body in quote_spans(text)]
    assert bodies == ["first quoted passage here.", "second quoted passage."]


def test_no_quote_means_the_dimension_is_not_applicable():
    ledger = entry_for("Twombly, 550 U.S. 544 (2007) is controlling.")
    entry = next(iter(ledger))
    verify_quote(entry, [majority()])
    assert entry.checks["quote"].verdict is Verdict.NA


def test_missing_opinion_text_is_not_checkable_rather_than_a_misquote():
    ledger = entry_for('The Court said "a formulaic recitation of the elements of a cause of action." Twombly, 550 U.S. 544 (2007).')
    entry = next(iter(ledger))
    verify_quote(entry, [])
    assert entry.checks["quote"].verdict is Verdict.NOT_CHECKABLE
    assert entry.overall is not Overall.FLAGGED


def test_ocr_text_downgrades_a_failed_match():
    """An OCR artifact must not be reported as a fabricated quote."""
    scanned = OpinionText(
        opinion_id=4, text=REAL, source_field="plain_text", extracted_by_ocr=True
    )
    ledger = entry_for('The Court said "wholly invented language appearing nowhere at all." Twombly, 550 U.S. 544 (2007).')
    entry = next(iter(ledger))
    verify_quote(entry, [scanned])
    assert entry.checks["quote"].verdict is Verdict.NOT_CHECKABLE


# --- opinion parsing ----------------------------------------------------------


def test_star_pagination_is_captured_with_offsets():
    markup = (
        '<opinion type="majority"><p>Before the break.'
        '<page-number citation-index="1" label="549">*549</page-number>'
        "After the break.</p></opinion>"
    )
    text, marks = strip_markup(markup)
    assert [m.label for m in marks] == ["549"]
    assert text[marks[0].offset :].startswith("After")


def test_quotation_marks_survive_parsing():
    """The quote-within-quote check depends on them."""
    text, _ = strip_markup("<p>The court said “these words” plainly.</p>")
    assert "“these words”" in text


def test_parser_falls_back_through_the_text_fields():
    """plain_text and html are empty for much of the corpus."""
    parsed = parse_opinion(
        {"id": 9, "plain_text": "", "html": "", "html_with_citations": "<p>Body text.</p>",
         "type": "040dissent", "author_str": "Stevens"}
    )
    assert parsed.source_field == "html_with_citations"
    assert parsed.text == "Body text."
    assert parsed.type_label == "dissent" and parsed.is_court_holding is False


def test_opinion_with_no_text_anywhere_returns_none():
    assert parse_opinion({"id": 9, "plain_text": "", "html": ""}) is None


# --- pincite (M5, spec 7.1) ---------------------------------------------------


def paginated(pages, text=REAL, index=1):
    return OpinionText(
        opinion_id=7, text=text, source_field="xml_harvard",
        page_marks=[PageMark(str(p), i * 40, index) for i, p in enumerate(pages)],
    )


def cite_with_pincite(pincite: str, page: str = "800"):
    from verascite.models import CiteKind, LedgerEntry, Location, RawCitation

    return LedgerEntry(
        citation_id="cite_0000",
        citation=RawCitation(
            kind=CiteKind.FULL_CASE, raw_text=f"A v. B, 457 U.S. {page}, {pincite}",
            location=Location(0, 30), volume="457", reporter="U.S.", page=page,
            pin_cite=pincite,
        ),
    )


def test_pincite_outside_the_opinion_fails():
    from verascite.verify_pincite import verify_pincite

    entry = cite_with_pincite("999")
    verify_pincite(entry, [paginated([800, 801, 802, 803])])
    check = entry.checks["pincite"]
    assert check.verdict is Verdict.FAIL
    assert "not part of this opinion" in check.evidence


def test_pincite_inside_the_range_without_a_located_quote_is_review():
    from verascite.verify_pincite import verify_pincite

    entry = cite_with_pincite("802")
    verify_pincite(entry, [paginated([800, 801, 802, 803])])
    assert entry.checks["pincite"].verdict is Verdict.NOT_CHECKABLE


def test_a_located_quotation_confirms_the_pincite():
    from verascite.verify_pincite import verify_pincite

    entry = cite_with_pincite("802")
    verify_pincite(entry, [paginated([800, 801, 802, 803])], located_page="802")
    assert entry.checks["pincite"].verdict is Verdict.PASS


def test_a_page_estimate_off_by_more_than_the_tolerance_is_review_not_fail():
    """The located page is an estimate; it may not contradict a citation."""
    from verascite.verify_pincite import verify_pincite

    entry = cite_with_pincite("800")
    verify_pincite(entry, [paginated([800, 801, 802, 803])], located_page="803")
    check = entry.checks["pincite"]
    assert check.verdict is Verdict.NOT_CHECKABLE
    assert "approximate" in check.reason


def test_no_pagination_means_not_checkable_with_a_stated_reason():
    from verascite.verify_pincite import verify_pincite

    entry = cite_with_pincite("802")
    verify_pincite(entry, [OpinionText(opinion_id=8, text=REAL, source_field="plain_text")])
    check = entry.checks["pincite"]
    assert check.verdict is Verdict.NOT_CHECKABLE
    assert "star pagination" in check.reason


def test_no_pincite_is_not_applicable():
    from verascite.models import CiteKind, LedgerEntry, Location, RawCitation
    from verascite.verify_pincite import verify_pincite

    entry = LedgerEntry(
        citation_id="c", citation=RawCitation(
            kind=CiteKind.FULL_CASE, raw_text="A v. B, 457 U.S. 800",
            location=Location(0, 20), page="800"),
    )
    verify_pincite(entry, [paginated([800, 801])])
    assert entry.checks["pincite"].verdict is Verdict.NA


def test_the_right_reporter_sequence_is_chosen():
    """An opinion in three reporters carries three interleaved sequences."""
    from verascite.clients.opinions import PageMark
    from verascite.verify_pincite import _reporter_index, _forward_run

    marks = [PageMark("800", 0, 1), PageMark("801", 40, 1),
             PageMark("2100", 10, 2), PageMark("2101", 50, 2)]
    opinion = OpinionText(opinion_id=9, text=REAL, source_field="xml_harvard",
                          page_marks=marks)
    assert _reporter_index(opinion, 800) == 1
    assert _reporter_index(opinion, 2100) == 2
    assert _forward_run(marks, 2) == [2100, 2101]


def test_footnote_backreferences_do_not_break_the_range():
    from verascite.clients.opinions import PageMark
    from verascite.verify_pincite import _forward_run

    marks = [PageMark(str(p), i * 10, 1) for i, p in enumerate([800, 801, 802, 803])]
    marks += [PageMark("801", 500, 1)]  # a footnote pointing back
    assert _forward_run(marks, 1) == [800, 801, 802, 803]
