"""The reading stage inside the pipeline.

Where test_readers.py covers the backend seam, this covers the stage that
decides *which* citations get read and what happens to the ones that do not.
Both halves matter: a citation that was never read must never be reported as
though it were examined and found wanting.
"""

from pathlib import Path

import pytest

from verascite.run_audit import audit_document, build_parser, _reading_candidates
from verascite.verdicts import Overall, Verdict

BRIEF = """\
A pleading must contain more than labels and conclusions, and a formulaic
recitation of the elements of a cause of action will not do. Bell Atlantic
Corp. v. Twombly, 550 U.S. 544, 555 (2007).

The court should also consider Smith v. Fictional Reporter Co., 88
Jurisprudentia 100 (9th Cir. 2018), which is directly on point and holds
that a plaintiff need never plead facts at all.
"""


def _entry(raw: str, sentence: str):
    """A resolved ledger entry carrying a proposition, built by hand."""
    from verascite.models import CiteKind, LedgerEntry, Location, RawCitation

    return LedgerEntry(
        citation_id="c1",
        citation=RawCitation(
            kind=CiteKind.FULL_CASE, raw_text=raw,
            location=Location(char_start=0, char_end=len(raw)),
            sentence=sentence,
        ),
    )


@pytest.fixture
def brief(tmp_path):
    path = tmp_path / "brief.md"
    path.write_text(BRIEF)
    return path


def _run(brief, tmp_path, **kw):
    return audit_document(brief, out_dir=tmp_path / "out", offline=True,
                          annotate=False, quiet=True, **kw)


# --- without a reader nothing changes ----------------------------------------

def test_without_a_reader_proposition_is_unchecked_not_passed(brief, tmp_path):
    ledger = _run(brief, tmp_path)
    for entry in ledger:
        check = entry.checks.get("proposition")
        if check is not None:
            assert check.verdict is not Verdict.PASS
            assert check.verdict is not Verdict.FAIL


def test_without_a_reader_the_report_says_substance_was_not_examined(brief, tmp_path):
    _run(brief, tmp_path)
    report = (tmp_path / "out" / "report.md").read_text()
    assert "No model was configured for this run" in report


def test_a_reader_is_never_built_unless_asked_for():
    args = build_parser().parse_args(["x.md"])
    assert args.model == "none"


# --- the reader is offered only grounded input -------------------------------

def test_offline_and_model_conflict_is_refused(brief, tmp_path):
    """Reading needs the opinion text, and offline means no opinion text."""
    with pytest.raises(RuntimeError, match="conflict"):
        _run(brief, tmp_path, ask=lambda prompt: "{}")


# --- a reader cannot manufacture a finding -----------------------------------

def _reader_returning(payload):
    def ask(prompt):
        return payload
    return ask


def test_a_reader_inventing_a_span_produces_no_finding(tmp_path):
    """The interlock, exercised through the whole stage rather than in isolation."""
    from verascite.clients.opinions import OpinionText
    from verascite.verify_proposition import verify_proposition

    entry = _entry(
        "Twombly, 550 U.S. 544",
        "A plaintiff need never plead any facts whatsoever in a complaint.",
    )
    opinion = OpinionText(opinion_id=1, source_field="html",
                          text="The complaint must contain sufficient factual matter.")
    verify_proposition(
        entry, [opinion],
        ask=_reader_returning('{"verdict":"CONTRADICTED","confidence":"high",'
                              '"supporting_span":"words never printed in this opinion"}'),
    )
    check = entry.checks["proposition"]
    assert check.verdict is Verdict.NOT_CHECKABLE
    assert check.verdict is not Verdict.FAIL
    assert "discarded" in (check.reason or "")


def test_a_reader_returning_rubbish_produces_no_finding(tmp_path):
    from verascite.clients.opinions import OpinionText
    from verascite.verify_proposition import verify_proposition

    entry = _entry("X v. Y, 1 U.S. 1",
                   "This case settles the question of federal jurisdiction here.")
    opinion = OpinionText(opinion_id=1, source_field="html", text="Some opinion text.")
    verify_proposition(entry, [opinion], ask=_reader_returning("I am not JSON."))
    assert entry.checks["proposition"].verdict is Verdict.NOT_CHECKABLE


def test_a_reading_cannot_raise_a_verdict_another_stage_set(tmp_path):
    """Monotonicity, at the one seam where a model could violate it."""
    from verascite.clients.opinions import OpinionText
    from verascite.verdicts import CheckResult
    from verascite.verify_proposition import verify_proposition

    entry = _entry("X v. Y, 1 U.S. 1",
                   "The opinion squarely holds that the statute applies here.")
    entry.set_check("proposition", CheckResult(
        Verdict.FAIL, evidence="an earlier, better-evidenced stage said so"))
    opinion = OpinionText(opinion_id=1, source_field="html",
                          text="The statute applies here, we hold.")
    verify_proposition(entry, [opinion], ask=_reader_returning(
        '{"verdict":"SUPPORTED","confidence":"high",'
        '"supporting_span":"The statute applies here, we hold."}'))
    assert entry.checks["proposition"].verdict is Verdict.FAIL


# --- candidate selection ------------------------------------------------------

def test_candidates_exclude_citations_with_no_proposition(brief, tmp_path):
    ledger = _run(brief, tmp_path)
    for entry in _reading_candidates(ledger):
        assert len((entry.citation.sentence or "").strip()) >= 40


def test_candidates_exclude_unresolved_citations(brief, tmp_path):
    """An unresolved citation has no opinion to read. Fetching for it is waste."""
    ledger = _run(brief, tmp_path)
    for entry in _reading_candidates(ledger):
        assert entry.resolved_to and entry.resolved_to.cluster_id


def test_candidates_exclude_a_name_mismatch(tmp_path):
    """Reading a proposition against a different case accuses a sound citation."""
    from verascite.ledger import Ledger
    from verascite.models import Resolution
    from verascite.verdicts import CheckResult

    ledger = Ledger(document_path="x", document_format="md")
    entry = _entry("X v. Y, 1 U.S. 1",
                   "This authority squarely supports the proposition asserted.")
    entry.resolved_to = Resolution(source="cl", cluster_id=99,
                                   case_name="Wholly Other v. Case")
    entry.set_check("case_name", CheckResult(
        Verdict.FAIL, evidence="the retrieved record names a different case"))
    ledger.add(entry)
    assert _reading_candidates(ledger) == []
