"""The case-name matcher, and the defect external validation exposed.

Found by running the tool against citations courts had adjudicated to be
fabricated: `State v. Pune, 94 Hawaii 200` came back VERIFIED because
`name_similarity` scored it 1.00 against `State v. Ing`. Containment plus a
four-character distinctive-token filter meant `{state, ing}` reduced to
`{state}`, which is contained in `{state, pune}`.

Criminal and government captions are an enormous share of American case law, so
this could confirm a fabricated case name against any unrelated decision printed
at the same page. Protocol: evals/names/V9_PROTOCOL.md.

These tests pull in both directions on purpose. Half of them insist a shared
generic party is not a match; the other half insist the fix did not break short
forms, misspellings, or identical names -- because turning either of those into
a mismatch would be a false accusation, which costs more here than a miss.
"""

import pytest

from verascite.config import NAME_PASS_THRESHOLD, NAME_REVIEW_THRESHOLD
from verascite.names import GENERIC_ONLY_CEILING, GENERIC_PARTIES, name_similarity


# --- a shared generic party is not a match -----------------------------------

@pytest.mark.parametrize("asserted,actual", [
    ("State v. Pune", "State v. Ing"),                  # the case that exposed it
    ("State v. Pune", "Yee v. VSL Prestressing, Inc."),
    ("People v. Smith", "People v. Rodriguez"),
    ("Commonwealth v. Reid", "Commonwealth v. Burton"),
    ("United States v. Braswell", "United States v. Petrofac"),
    ("City of Houston v. Vela", "City of Dallas v. Nguyen"),
    ("In re Marriage of Smith", "In re Estate of Nguyen"),
])
def test_a_shared_generic_party_is_not_a_match(asserted, actual):
    score = name_similarity(asserted, actual)
    assert score <= GENERIC_ONLY_CEILING, f"{asserted!r} vs {actual!r} scored {score}"


def test_a_generic_mismatch_reads_as_affirmative_not_borderline():
    """0.30 sits below the review floor, so it is a mismatch, not a maybe."""
    assert GENERIC_ONLY_CEILING < NAME_REVIEW_THRESHOLD < NAME_PASS_THRESHOLD
    assert name_similarity("State v. Pune", "State v. Ing") < NAME_REVIEW_THRESHOLD


def test_a_bare_caption_abbreviation_is_not_an_identifier():
    """Archives store `case_name_short` as things like 'Com.' and 'State'."""
    for short in ("Com.", "State", "People", "In re"):
        assert name_similarity("Com v. Reid", short) <= GENERIC_ONLY_CEILING


def test_the_generic_set_covers_the_common_captions():
    for token in ("state", "people", "commonwealth", "united", "com", "doe"):
        assert token in GENERIC_PARTIES


# --- and the fix did not break a real match ----------------------------------

@pytest.mark.parametrize("asserted,actual", [
    ("Twombly", "Bell Atlantic Corp. v. Twombly"),      # short form
    ("Iqbal", "Ashcroft v. Iqbal"),
    ("State v. Smith", "Smith"),                        # generic party + surname
    ("United States v. Ing", "United States v. Ing"),   # short surname, same case
    ("In re Doe", "In re Doe"),                         # no distinctive token at all
    ("Ziglar v. Abbasi", "Zigler v. Abbasi"),           # misspelling
    ("Bell Atlantic Corp. v. Twombly", "Bell Atlantic Corp. v. Twombly"),
])
def test_a_real_match_still_matches(asserted, actual):
    score = name_similarity(asserted, actual)
    assert score >= NAME_PASS_THRESHOLD, f"{asserted!r} vs {actual!r} scored {score}"


def test_a_misspelling_is_never_turned_into_an_accusation():
    """A spelling difference must not become a finding against the document."""
    assert name_similarity("Ziglar v. Abbasi", "Zigler v. Abbasi") >= NAME_PASS_THRESHOLD


def test_identical_names_short_circuit_even_when_wholly_generic():
    """'In re Doe' against itself is not a mismatch for lacking distinctiveness."""
    assert name_similarity("In re Doe", "In re Doe") == 1.0
    assert name_similarity("State", "State") == 1.0


def test_a_distinctive_surname_still_carries_the_match():
    """The generic party is ignored; the surname decides."""
    assert name_similarity("State v. Kowalczyk", "State v. Kowalczyk") == 1.0
    assert name_similarity("State v. Kowalczyk", "State v. Petrofac") <= GENERIC_ONLY_CEILING


# --- the end-to-end consequence ----------------------------------------------

def test_a_fabricated_name_at_a_real_page_is_flagged():
    """The behaviour the sanctioned corpus said was wrong.

    A citation naming a case that does not exist, at a page where some other
    case does, must not come back confirmed.
    """
    from verascite.clients.courtlistener import LookupResult
    from verascite.extract import extract
    from verascite.models import Document
    from verascite.resolve import resolve
    from verascite.verdicts import Overall, Verdict
    from verascite.verify_existence import _apply_lookup
    from verascite.verify_metadata import verify_metadata

    text = "The court considered State v. Pune, 94 Hawaii 200 in reaching its decision."
    document = Document(text=text, source_path="x", source_format="md")
    cites, notes = extract(document)
    ledger = resolve(document, cites, notes)
    entry = next(iter(ledger))

    # What CourtListener actually returns for 94 Hawaii 200: status 300, and
    # not one of the candidates is named Pune.
    result = LookupResult(
        citation="94 Hawaii 200",
        status=300,
        clusters=[
            {"id": 6612924, "case_name": "State v. Ing", "date_filed": "2000-09-22",
             "precedential_status": "Published"},
            {"id": 6612923, "case_name": "State v. Chee", "date_filed": "2000-09-22",
             "precedential_status": "Published"},
            {"id": 6612927, "case_name": "Yee v. VSL Prestressing (Guam), Inc.",
             "date_filed": "2000-09-29", "precedential_status": "Published"},
        ],
    )
    _apply_lookup(entry, result)
    verify_metadata(ledger)

    assert entry.checks["case_name"].verdict is Verdict.FAIL
    assert entry.checks["existence"].verdict is not Verdict.PASS
    assert entry.overall is Overall.FLAGGED
