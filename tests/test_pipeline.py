"""Deterministic pipeline tests: extraction, offsets, resolution, verdicts."""

from __future__ import annotations

import json

import pytest

from verascite.extract import collapse_whitespace, extract, reporter_is_known
from verascite.ingest import ingest
from verascite.ledger import Ledger
from verascite.models import CiteKind, Document
from verascite.resolve import resolve
from verascite.verdicts import CheckResult, Overall, Verdict
from verascite.verify_existence import check_reporter_locally, verify_existence
from verascite.verify_metadata import name_similarity, normalize_party_tokens, verify_metadata


def doc_of(text: str) -> Document:
    return Document(
        text=text, source_path="test", source_format="txt", page_map=[(0, len(text), 1)]
    )


def audit(text: str) -> Ledger:
    document = doc_of(text)
    citations, notes = extract(document)
    ledger = resolve(document, citations, notes)
    verify_existence(ledger, client=None, offline=True)
    verify_metadata(ledger)
    return ledger


# --- offsets ------------------------------------------------------------------


def test_collapse_whitespace_maps_back_to_original():
    original = "a\n\n  b\tc"
    clean, index_map = collapse_whitespace(original)
    assert clean == "a b c"
    for i, char in enumerate(clean):
        if char != " ":
            assert original[index_map[i]] == char


def test_raw_text_is_an_exact_substring_of_the_document():
    """Every verdict points at a real location, so spans must be exact."""
    text = (
        "Harlow v. Fitzgerald, 457 U.S. 800, 818 (1982).\n\nSee also\n"
        "Ashcroft v. Iqbal, 556\nU.S. 662 (2009). Bell Atlantic Corp. v.\n"
        "Twombly, 550 U.S. 544, 570 (2007)."
    )
    document = doc_of(text)
    citations, _ = extract(document)
    assert citations
    for cite in citations:
        span = text[cite.location.char_start : cite.location.char_end]
        assert span == cite.raw_text
        assert cite.normalized in collapse_whitespace(span)[0] or cite.kind is CiteKind.ID


def test_citation_split_across_a_line_break_is_still_extracted():
    """A citation wrapped mid-line is ordinary in a real brief."""
    ledger = audit("Ashcroft v. Iqbal, 556\nU.S. 662 (2009) controls here.")
    entry = next(iter(ledger))
    assert entry.citation.normalized == "556 U.S. 662"
    assert entry.citation.kind is CiteKind.FULL_CASE
    assert entry.overall is not Overall.FLAGGED


def test_parallel_citation_span_does_not_swallow_the_next_case():
    text = "Roe v. Wade, 410 U.S. 113, 93 S. Ct. 705 (1973). Aves v. Shah, 997 F.2d 762 (10th Cir. 1993)."
    citations, _ = extract(doc_of(text))
    first = next(c for c in citations if c.normalized == "410 U.S. 113")
    assert "Aves" not in first.raw_text
    assert first.raw_text == "Roe v. Wade, 410 U.S. 113"


# --- fabricated reporters (the gap eyecite leaves) ----------------------------


def test_invented_reporter_is_recovered_by_the_shadow_scan():
    """eyecite returns [] for an unknown reporter; the citation must not vanish."""
    from eyecite import get_citations

    assert get_citations("Bogus v. Nobody, 33 Umbrella 422 (2020).") == []

    ledger = audit("Bogus v. Nobody, 33 Umbrella 422 (2020) is controlling.")
    entry = next(iter(ledger))
    assert entry.citation.kind is CiteKind.UNRECOGNIZED
    assert entry.checks["reporter_valid"].verdict is Verdict.FAIL
    assert entry.overall is Overall.FLAGGED


def test_shadow_scan_does_not_sweep_up_prose_numbers():
    ledger = audit(
        "See Doe v. Roe, 999 F.3d 1 (9th Cir. 2019). The plaintiff paid 5 Main Street 200 dollars."
    )
    assert not [e for e in ledger if e.citation.kind is CiteKind.UNRECOGNIZED]


@pytest.mark.parametrize(
    "reporter", ["U.S.", "U.S", "F.2d", "F. Supp. 3d", "F.Supp.3d", "S. Ct.", "N.E.2d"]
)
def test_real_reporters_are_recognized(reporter):
    assert reporter_is_known(reporter)


@pytest.mark.parametrize("reporter", ["Umbrella", "Jurisprudentia", "Fake Rep"])
def test_invented_reporters_are_not_recognized(reporter):
    assert not reporter_is_known(reporter)


# --- the false-positive guards that P1 exists for -----------------------------


def test_a_statute_is_never_flagged_as_a_bad_reporter():
    """reporters-db holds case reporters; U.S.C. is not one, and that is fine."""
    ledger = audit("Plaintiff sues under 42 U.S.C. § 1983 for damages.")
    entry = next(e for e in ledger if e.citation.kind is CiteKind.LAW)
    assert entry.checks["reporter_valid"].verdict is Verdict.NA
    assert entry.checks["existence"].verdict is Verdict.OUT_OF_SCOPE
    assert entry.overall is Overall.UNVERIFIED


def test_westlaw_identifier_is_out_of_scope_not_not_found():
    """A known coverage gap must not consume a lookup or imply fabrication."""
    ledger = audit("See Tinch v. Video Indus. Servs., Inc., 2019 WL 1396975 (E.D. Mich. 2019).")
    entry = next(iter(ledger))
    check = entry.checks["existence"]
    assert check.verdict is Verdict.OUT_OF_SCOPE
    assert "not a fabrication signal" in check.reason
    assert entry.overall is Overall.UNVERIFIED


def test_offline_run_never_reports_a_citation_as_found_or_missing():
    """With no lookup performed, absence claims are unfounded (P6)."""
    ledger = audit("Harlow v. Fitzgerald, 457 U.S. 800, 818 (1982).")
    entry = next(iter(ledger))
    assert entry.checks["existence"].verdict is Verdict.NOT_CHECKABLE
    assert entry.overall is Overall.REVIEW


# --- resolution ---------------------------------------------------------------


def test_short_forms_resolve_to_their_antecedent():
    ledger = audit(
        "Aves v. Shah, 997 F.2d 762, 767 (10th Cir. 1993) held X. See id. at 768. "
        "Aves, 997 F.2d at 770."
    )
    entries = list(ledger)
    full = entries[0]
    assert [e.antecedent_id for e in entries[1:]] == [full.citation_id, full.citation_id]
    assert all(e.authority_key == full.authority_key for e in entries[1:])


def test_supra_resolves_by_party_name():
    ledger = audit(
        "Roe v. Wade, 410 U.S. 113 (1973). Aves v. Shah, 997 F.2d 762 (10th Cir. 1993). "
        "Wade, supra, at 120."
    )
    supra = next(e for e in ledger if e.citation.kind is CiteKind.SUPRA)
    target = ledger.get(supra.antecedent_id)
    assert target.citation.normalized == "410 U.S. 113"


def test_dangling_short_form_is_not_checkable_rather_than_missing():
    ledger = audit("The court so held. See 511 F.3d at 22.")
    entry = next(iter(ledger))
    assert entry.antecedent_id is None
    assert entry.checks["existence"].verdict is Verdict.NOT_CHECKABLE


def test_deduplication_counts_distinct_authorities():
    ledger = audit(
        "Aves v. Shah, 997 F.2d 762 (10th Cir. 1993). Id. at 764. Aves, 997 F.2d at 770. "
        "Roe v. Wade, 410 U.S. 113 (1973)."
    )
    assert len(ledger) == 4
    assert ledger.run_meta["distinct_authorities"] == 2


# --- metadata -----------------------------------------------------------------


@pytest.mark.parametrize(
    "asserted,actual",
    [
        ("United States v. Smith", "U.S. v. Smith"),
        ("Nat'l Ass'n of Mfrs. v. EPA", "National Association of Manufacturers v. EPA"),
        ("Mata v. Avianca, Inc.", "Mata v. Avianca, Incorporated"),
        ("In re Grand Jury Subpoena", "Grand Jury Subpoena"),
        ("Aves", "Aves v. Shah"),
    ],
)
def test_equivalent_case_names_match(asserted, actual):
    assert name_similarity(asserted, actual) >= 0.75


def test_the_tinch_dolberry_mismatch_is_caught():
    """The documented failure: right citation, entirely different case."""
    assert name_similarity("Tinch v. Video Indus. Servs., Inc.", "Dolberry v. Jakob") == 0.0


def test_normalization_drops_noise_but_keeps_identity():
    assert normalize_party_tokens("The Estate of John Doe, et al.") == frozenset(
        {"john", "doe"}
    )


# --- ledger -------------------------------------------------------------------


def test_ledger_round_trips_through_disk(tmp_path):
    ledger = audit(
        "Aves v. Shah, 997 F.2d 762, 767 (10th Cir. 1993). Id. at 768. "
        "Bogus v. Nobody, 33 Umbrella 422 (2020)."
    )
    path = ledger.checkpoint(tmp_path / "ledger.json")
    reloaded = Ledger.load(path)

    assert len(reloaded) == len(ledger)
    for before, after in zip(ledger, reloaded):
        assert after.citation_id == before.citation_id
        assert after.citation.raw_text == before.citation.raw_text
        assert after.overall is before.overall
        assert after.antecedent_id == before.antecedent_id


def test_checkpoint_is_atomic(tmp_path):
    ledger = audit("Aves v. Shah, 997 F.2d 762 (10th Cir. 1993).")
    path = tmp_path / "nested" / "ledger.json"
    ledger.checkpoint(path)
    assert json.loads(path.read_text())["schema_version"]
    assert not list(path.parent.glob("*.tmp"))


def test_determinism_verdicts_are_identical_across_runs():
    """Spec 8.2: divergence in the deterministic layer is a bug."""
    text = (
        "Harlow v. Fitzgerald, 457 U.S. 800, 818 (1982). Id. at 819. "
        "Bogus v. Nobody, 33 Umbrella 422 (2020). See 42 U.S.C. § 1983."
    )
    runs = [json.dumps(audit(text).to_dict()["entries"], sort_keys=True) for _ in range(3)]
    assert runs[0] == runs[1] == runs[2]


# --- regressions found against live CourtListener data -------------------------
#
# eyecite derives citation metadata from full_span(), which over-extends across
# adjacent citations. On the sample brief its full_span() for 550 U.S. 544 ran
# 285 characters and covered four citations, so the metadata it reported was
# drawn partly from other cases. Each of these produced a FLAGGED verdict
# against a real, correctly-cited authority.


def test_year_is_read_from_the_citation_not_from_a_later_one():
    """Regression: Twombly and Iqbal were both reported as year 1993."""
    text = (
        "Bell Atlantic Corp. v. Twombly, 550 U.S. 544, 570 (2007); Ashcroft v. "
        "Iqbal, 556 U.S. 662 (2009). The Tenth Circuit has applied this framework. "
        "Aves v. Shah, 997 F.2d 762, 767 (10th Cir. 1993)."
    )
    by_cite = {c.normalized: c for c in extract(doc_of(text))[0]}
    assert by_cite["550 U.S. 544"].year == "2007"
    assert by_cite["556 U.S. 662"].year == "2009"
    assert by_cite["997 F.2d 762"].year == "1993"


def test_court_is_dropped_unless_the_citation_supports_it():
    text = "Aves v. Shah, 997 F.2d 762 (10th Cir. 1993). Harlow v. Fitzgerald, 457 U.S. 800 (1982)."
    by_cite = {c.normalized: c for c in extract(doc_of(text))[0]}
    # Named explicitly in the parenthetical.
    assert by_cite["997 F.2d 762"].court == "ca10"
    # Implied by a court-specific reporter, which is legitimate.
    assert by_cite["457 U.S. 800"].court == "scotus"


def test_uncorroborated_court_becomes_none_rather_than_a_guess():
    cite = next(c for c in extract(doc_of("Smith v. Jones, 12 F.4th 99 (2021)."))[0])
    assert cite.court is None


@pytest.mark.parametrize(
    "text,expected",
    [
        ("It failed. Bell Atlantic Corp. v. Twombly, 550 U.S. 544 (2007).",
         "Bell Atlantic Corp. v. Twombly, 550 U.S. 544 (2007)"),
        ("See Mata v. Avianca, Inc., 678 F. Supp. 3d 443 (S.D.N.Y. 2023).",
         "Mata v. Avianca, Inc., 678 F. Supp. 3d 443 (S.D.N.Y. 2023)"),
        ("The court agreed. Nat'l Ass'n of Mfrs. v. EPA, 750 F.3d 921 (D.C. Cir. 2014).",
         "Nat'l Ass'n of Mfrs. v. EPA, 750 F.3d 921 (D.C. Cir. 2014)"),
    ],
)
def test_party_name_abbreviations_do_not_truncate_the_span(text, expected):
    """Regression: "Corp." read as a sentence end, losing "Bell Atlantic"."""
    cite = next(c for c in extract(doc_of(text))[0])
    assert cite.raw_text == expected


def test_sentence_boundary_still_stops_the_walk_back():
    """The abbreviation fix must not swallow the preceding sentence."""
    cite = next(c for c in extract(doc_of("The motion was denied. Roe v. Wade, 410 U.S. 113 (1973)."))[0])
    assert cite.raw_text == "Roe v. Wade, 410 U.S. 113 (1973)"


def test_real_supreme_court_citations_are_never_flagged_offline():
    """The canonical false positive: a correct citation reported as fabricated."""
    text = (
        "See Harlow v. Fitzgerald, 457 U.S. 800, 818 (1982); Bell Atlantic Corp. "
        "v. Twombly, 550 U.S. 544, 570 (2007); Ashcroft v. Iqbal, 556\nU.S. 662 "
        "(2009); Pearson v. Callahan, 555 U.S. 223 (2009)."
    )
    ledger = audit(text)
    assert len(ledger) == 4
    assert not ledger.by_overall(Overall.FLAGGED)
    assert all(e.checks["reporter_valid"].verdict is Verdict.PASS for e in ledger)
