"""Reading a statutory citation into its parts.

The parse is the conservative half of statutory checking. A parse that guesses
sends the lookup after the wrong provision, and a lookup on the wrong provision
produces a confident finding against a citation that was fine -- the same
inversion the governing rule forbids elsewhere. So most of these tests are
about what the parser *refuses* to do.
"""

import pytest

from verascite.extract import extract
from verascite.models import CiteKind, Document, Location, RawCitation
from verascite.verdicts import Overall, Verdict
from verascite.statutes import (
    CFR,
    PUBLIC_LAW,
    STATUTES_AT_LARGE,
    USC,
    insert_section_symbols,
    parse_statute,
    parse_subsection,
)


def only(text: str):
    """The single statutory citation in a fragment, as the pipeline sees it."""
    cites, _ = extract(Document(text=text, source_path="x", source_format="md"))
    laws = [c for c in cites if c.kind is CiteKind.LAW]
    assert len(laws) == 1, f"expected one law citation in {text!r}, got {len(laws)}"
    return parse_statute(laws[0])


# --- the codes ----------------------------------------------------------------

def test_us_code_section():
    ref = only("The claim arises under 42 U.S.C. § 1983 in this circuit.")
    assert (ref.code, ref.title, ref.section) == (USC, "42", "1983")
    assert ref.subsection == []
    assert ref.checkable


def test_us_code_subsection_becomes_a_path():
    """The subsection is the fabrication target, so it must become structure."""
    ref = only("See 42 U.S.C. § 12112(b)(3)(A) for the definition.")
    assert ref.subsection == ["b", "3", "A"]
    assert ref.checkable


def test_regulation_recovers_its_title_and_part():
    """A C.F.R. title arrives in a field named for something else."""
    ref = only("Employers must comply with 29 C.F.R. § 1910.132(d)(1) at all times.")
    assert (ref.code, ref.title, ref.part) == (CFR, "29", "1910")
    assert ref.section == "1910.132"
    assert ref.subsection == ["d", "1"]


def test_public_law_splits_congress_from_number():
    ref = only("Congress responded in Pub. L. No. 116-136 that spring.")
    assert (ref.code, ref.congress, ref.law_number) == (PUBLIC_LAW, "116", "136")


def test_statutes_at_large():
    ref = only("The provision appears at 134 Stat. 281 in the bound volume.")
    assert (ref.code, ref.volume, ref.page) == (STATUTES_AT_LARGE, "134", "281")


def test_a_year_in_the_citation_is_kept():
    ref = only("Exemption five is at 5 U.S.C. § 552(b)(5) (2018) of the Act.")
    assert ref.year == "2018"


# --- what it refuses to check -------------------------------------------------

def test_a_section_range_is_declined_not_truncated():
    """Checking the first of several and reporting for all is the failure here."""
    ref = only("The claims arise under 42 U.S.C. §§ 1983-1985 as pleaded.")
    assert not ref.checkable
    assert "range or list" in ref.declined


def test_et_seq_is_declined():
    ref = only("Relief is available under 42 U.S.C. § 1983 et seq. in this case.")
    assert not ref.checkable
    assert ref.subsection == []


def test_a_non_case_citation_is_not_parsed_as_a_statute():
    cites, _ = extract(Document(
        text="See Bell Atlantic Corp. v. Twombly, 550 U.S. 544, 555 (2007).",
        source_path="x", source_format="md"))
    assert all(parse_statute(c) is None for c in cites)


def test_an_unsupported_code_says_which_one():
    """A report must name the authority type, not fall silent on it."""
    ref = parse_statute(RawCitation(
        kind=CiteKind.LAW, raw_text="Cal. Civ. Code § 1542",
        location=Location(char_start=0, char_end=21),
        reporter="Cal. Civ. Code", section="1542"))
    assert not ref.checkable
    assert "Cal. Civ. Code" in ref.declined


def test_a_missing_section_is_declined():
    ref = parse_statute(RawCitation(
        kind=CiteKind.LAW, raw_text="42 U.S.C.",
        location=Location(char_start=0, char_end=9),
        reporter="U.S.C.", title="42"))
    assert not ref.checkable
    assert "no section" in ref.declined


# --- the subsection path ------------------------------------------------------

@pytest.mark.parametrize("pin,path", [
    ("(a)", ["a"]),
    ("(b)(3)(A)", ["b", "3", "A"]),
    ("(d)(1)", ["d", "1"]),
    ("(iv)", ["iv"]),
    ("(12)", ["12"]),
])
def test_subsection_paths_parse(pin, path):
    assert parse_subsection(pin) == (path, None)


@pytest.mark.parametrize("pin", [
    "(b)(3), (c)",      # a list: checking (b)(3) alone answers a different question
    "(a)-(c)",          # a range
    "(a) note",
    "et seq.",
    "(b) and following",
])
def test_a_pincite_naming_more_than_one_provision_is_refused(pin):
    path, declined = parse_subsection(pin)
    assert path == []
    assert declined


def test_an_empty_pincite_is_not_an_error():
    assert parse_subsection(None) == ([], None)
    assert parse_subsection("") == ([], None)


def test_a_page_pincite_is_not_a_subsection():
    path, declined = parse_subsection("1033")
    assert path == []
    assert declined


# --- forms eyecite does not see ----------------------------------------------

def test_bare_forms_are_invisible_to_extraction_without_normalising():
    """The motivating fact: these produce no citation at all, so they are not
    merely unchecked -- nothing in the report says they were present."""
    cites, _ = extract(Document(text="See 42 USC 1983 and 29 CFR 1910.132.",
                                source_path="x", source_format="md"))
    assert [c for c in cites if c.kind is CiteKind.LAW] == []


def test_normalising_makes_bare_forms_visible():
    text, count = insert_section_symbols("See 42 USC 1983 and 29 CFR 1910.132.")
    assert count == 2
    cites, _ = extract(Document(text=text, source_path="x", source_format="md"))
    refs = [parse_statute(c) for c in cites if c.kind is CiteKind.LAW]
    assert [(r.code, r.title, r.section) for r in refs] == [
        (USC, "42", "1983"), (CFR, "29", "1910.132"),
    ]


def test_normalising_leaves_a_proper_citation_alone():
    text, count = insert_section_symbols("See 42 U.S.C. § 1983 today.")
    assert count == 0
    assert text == "See 42 U.S.C. § 1983 today."


def test_normalising_does_not_touch_case_citations():
    original = "See Twombly, 550 U.S. 544, 555 (2007)."
    assert insert_section_symbols(original) == (original, 0)


# --- stage ordering, which was got wrong once ---------------------------------

def test_a_sound_statute_can_come_back_confirmed(tmp_path, monkeypatch):
    """The bug this guards against reported every good statute as UNVERIFIED.

    ``verify_existence`` records statutory citations OUT_OF_SCOPE, and verdicts
    are monotonic. Running the statute stage *after* it therefore left a sound
    statutory citation stuck at OUT_OF_SCOPE, which rolls up to UNVERIFIED --
    telling a reader that a perfectly good citation could not be found.
    """
    from verascite.clients import statutes as client_module
    from verascite.run_audit import audit_document

    class Confirming:
        """Stands in for the government sources, always confirming."""
        stats = {"requests": 0, "failures": 0}

        def usc_section(self, *a, **k):
            return client_module.SourceResult(
                client_module.EXISTS, "confirmed", ["govinfo_link_service"])

        def usc_subsection(self, *a, **k):
            return client_module.SourceResult(
                client_module.EXISTS, "confirmed", ["uscode_house_uslm"])

        cfr_section = usc_section
        cfr_subsection = usc_subsection
        public_law = usc_section
        statute_at_large = usc_section

    monkeypatch.setattr("verascite.run_audit.StatuteClient",
                        lambda *a, **k: Confirming())
    document = tmp_path / "b.md"
    document.write_text(
        "Liability attaches under 42 U.S.C. § 1983 for deprivations of rights.\n")
    ledger = audit_document(document, out_dir=tmp_path / "out", annotate=False,
                            quiet=True)
    entry = next(iter(ledger))
    assert entry.checks["existence"].verdict is Verdict.PASS, (
        "a confirmed statute must not be left at OUT_OF_SCOPE")
    assert entry.overall is not Overall.UNVERIFIED


def test_the_metadata_placeholder_does_not_overwrite_a_real_finding(tmp_path, monkeypatch):
    """A restatement of the input must never land on top of a finding."""
    from verascite.clients import statutes as client_module
    from verascite.run_audit import audit_document

    class Contradicting:
        stats = {"requests": 0, "failures": 0}

        def usc_section(self, *a, **k):
            return client_module.SourceResult(
                client_module.EXISTS, "the section exists", ["govinfo_link_service"])

        def usc_subsection(self, *a, **k):
            return client_module.SourceResult(
                client_module.ABSENT,
                "the official text contains no such subsection",
                ["uscode_house_uslm"])

        cfr_section = usc_section
        cfr_subsection = usc_subsection
        public_law = usc_section
        statute_at_large = usc_section

    monkeypatch.setattr("verascite.run_audit.StatuteClient",
                        lambda *a, **k: Contradicting())
    document = tmp_path / "b.md"
    document.write_text(
        "Plaintiff also invokes 42 U.S.C. § 1983(a)(2) as a private right of action.\n")
    ledger = audit_document(document, out_dir=tmp_path / "out", annotate=False,
                            quiet=True)
    entry = next(iter(ledger))
    assert entry.checks["statute_parts"].verdict is Verdict.FAIL
    assert entry.overall is Overall.FLAGGED
