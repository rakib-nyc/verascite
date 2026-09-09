"""The verification record.

The record exists to be handed to a court, so the tests that matter are the
ones that stop it from claiming more than it can. Three properties are load
bearing: it never certifies, it always states what it did not examine, and it
never lets UNVERIFIED read as a fabrication finding. Each is asserted directly
rather than left to the wording surviving an edit.
"""

import re
from pathlib import Path

import pytest

from verascite.record import (
    NOT_EXAMINED,
    content_hash,
    render_record,
    write_record,
)
from verascite.run_audit import audit_document
from verascite.verdicts import Overall

BRIEF = """\
A pleading must contain more than "labels and conclusions." Bell Atlantic Corp.
v. Twombly, 550 U.S. 544, 555 (2007).

The court should also consider Smith v. Fictional Reporter Co., 88
Jurisprudentia 100 (9th Cir. 2018), which is directly on point.

See also Tinch v. Video Indus. Servs., Inc., 2019 WL 1396975 (E.D. Mich. 2019).
"""


@pytest.fixture
def run(tmp_path):
    document = tmp_path / "brief.md"
    document.write_text(BRIEF)
    ledger = audit_document(document, out_dir=tmp_path / "out", offline=True,
                            annotate=False, quiet=True)
    return document, ledger, tmp_path / "out"


@pytest.fixture
def record(run):
    document, ledger, _ = run
    return render_record(ledger, document)


# --- it is written, and it is bound to one file ------------------------------

def test_the_record_is_written_by_default(run):
    _, _, out = run
    assert (out / "verification-record.md").exists()


def test_the_record_can_be_switched_off(tmp_path):
    document = tmp_path / "b.md"
    document.write_text(BRIEF)
    audit_document(document, out_dir=tmp_path / "o", offline=True,
                   annotate=False, record=False, quiet=True)
    assert not (tmp_path / "o" / "verification-record.md").exists()


def test_the_hash_is_of_the_file_bytes(tmp_path):
    import hashlib
    path = tmp_path / "f.md"
    path.write_bytes(b"exact bytes")
    assert content_hash(path) == hashlib.sha256(b"exact bytes").hexdigest()


def test_the_hash_appears_in_the_record(run, record):
    document, _, _ = run
    assert content_hash(document) in record


def test_editing_the_document_changes_the_identity(tmp_path):
    """A record must not be re-attachable to a later draft."""
    path = tmp_path / "f.md"
    path.write_text(BRIEF)
    before = content_hash(path)
    path.write_text(BRIEF + "\nOne more sentence.\n")
    assert content_hash(path) != before


# --- it never certifies -------------------------------------------------------

def test_the_record_disclaims_certification_in_its_first_lines(record):
    head = record[:1200]
    assert "not a certification" in head
    assert "Rule 11" in head


def test_the_record_never_claims_to_certify(record):
    """No sentence may read as the tool certifying anything."""
    forbidden = re.compile(
        r"\b(we|this tool|verascite)\s+(certif|verif)\w*\b", re.IGNORECASE)
    assert not forbidden.search(record)


def test_the_attestation_block_is_left_empty_for_a_human(record):
    assert "Reviewing attorney |" in record
    assert "Bar number" in record


# --- it always says what it did not examine ----------------------------------

def test_every_unexamined_item_is_stated(record):
    for heading, _ in NOT_EXAMINED:
        assert heading in record


def test_good_law_is_the_first_unexamined_item_listed(record):
    """The omission most likely to be assumed away goes first."""
    assert NOT_EXAMINED[0][0] == "Whether any authority remains good law"
    positions = [record.index(heading) for heading, _ in NOT_EXAMINED]
    assert positions == sorted(positions)


def test_the_limits_are_stated_before_the_results(record):
    """A list of confirmations read before its caveats is a misleading record."""
    assert record.index("did not examine") < record.index(
        "Every citation, and what was done about it")


def test_the_record_says_it_is_not_a_citator(record):
    assert "not a citator" in record


# --- absence is never fabrication --------------------------------------------

def test_unverified_is_described_as_absence(record):
    meaning = RESULT_LINE(record, "UNVERIFIED")
    assert "not a finding that the authority does not exist" in meaning
    assert "Not found in the sources consulted" in meaning


def test_no_fabrication_language_anywhere_in_the_record(record):
    for word in ("fabricated", "fake", "hallucinated", "does not exist"):
        for line in record.splitlines():
            if word in line.lower() and "not a finding" not in line.lower():
                pytest.fail(f"{word!r} appears outside a disclaimer: {line[:120]}")


def test_verified_does_not_claim_good_law(record):
    assert "not a statement that the authority is still good law" in RESULT_LINE(
        record, "VERIFIED").lower()


def RESULT_LINE(record: str, result: str) -> str:
    for line in record.splitlines():
        if line.startswith(f"| `{result}`"):
            return line
    raise AssertionError(f"no row for {result}")


# --- the citation-level record ------------------------------------------------

def test_every_citation_appears_in_the_record(run, record):
    """A citation spanning a line break is flattened, so compare flattened."""
    _, ledger, _ = run
    assert len(ledger) > 0
    for entry in ledger:
        flat = " ".join(entry.citation.raw_text.split())
        assert flat[:30] in record


def test_each_citation_names_the_sources_consulted_for_it(run, record):
    _, ledger, _ = run
    assert "`reporters_db`" in record


def test_a_model_reading_is_marked_in_the_record(tmp_path):
    """A record must not present a reading as though it were a fixed check."""
    from verascite.ledger import Ledger
    from verascite.models import CiteKind, LedgerEntry, Location, RawCitation
    from verascite.verdicts import CheckResult, Verdict
    from verascite.verify_proposition import PROPOSITION_SOURCE

    ledger = Ledger(document_path="b.md", document_format="md")
    entry = LedgerEntry(
        citation_id="c1",
        citation=RawCitation(kind=CiteKind.FULL_CASE, raw_text="X v. Y, 1 U.S. 1",
                             location=Location(char_start=0, char_end=16)),
    )
    entry.set_check("proposition", CheckResult(
        Verdict.FAIL, evidence="the opinion says the opposite",
        sources_consulted=[PROPOSITION_SOURCE]))
    ledger.add(entry)
    out = render_record(ledger, tmp_path / "absent.md")
    assert "(model reading)" in out


def test_a_missing_document_does_not_break_the_record(tmp_path):
    from verascite.ledger import Ledger
    out = render_record(Ledger(document_path="gone.md", document_format="md"),
                        tmp_path / "gone.md")
    assert "unavailable" in out


def test_write_record_creates_the_directory(tmp_path):
    from verascite.ledger import Ledger
    target = tmp_path / "deep" / "nested" / "verification-record.md"
    write_record(Ledger(document_path="x", document_format="md"),
                 tmp_path / "x.md", target)
    assert target.exists()
