"""Batch review across a directory.

The property that matters here is that one bad file cannot end the run. A
lawyer who points this at a matter folder and discovers at the end that nothing
was checked is worse off than one who never ran it.
"""

import pytest

from verascite.batch import (
    DocumentOutcome,
    find_documents,
    render_summary,
    run_batch,
    write_summary,
)
from verascite.ledger import Ledger
from verascite.models import CiteKind, LedgerEntry, Location, RawCitation
from verascite.verdicts import CheckResult, Overall, Verdict


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "sub").mkdir()
    for name in ("a.md", "b.docx", "sub/c.txt", "d.pdf"):
        (tmp_path / name).write_text("x")
    (tmp_path / "notes.rtf").write_text("x")       # unsupported format
    (tmp_path / "~$lock.docx").write_text("x")     # a word processor lock file
    (tmp_path / ".hidden.md").write_text("x")      # a dotfile
    return tmp_path


# --- discovery ----------------------------------------------------------------

def test_it_finds_every_supported_format(tree):
    names = {p.name for p in find_documents(tree)}
    assert names == {"a.md", "b.docx", "c.txt", "d.pdf"}


def test_it_skips_lock_files_and_dotfiles(tree):
    """Auditing a Word lock file produces a confusing error and no findings."""
    names = {p.name for p in find_documents(tree)}
    assert "~$lock.docx" not in names
    assert ".hidden.md" not in names
    assert "notes.rtf" not in names


def test_no_recurse_stays_at_the_top_level(tree):
    names = {p.name for p in find_documents(tree, recursive=False)}
    assert "c.txt" not in names


def test_a_single_file_is_its_own_batch(tree):
    assert find_documents(tree / "a.md") == [tree / "a.md"]


def test_discovery_is_ordered(tree):
    assert find_documents(tree) == sorted(find_documents(tree))


# --- running ------------------------------------------------------------------

def _ledger(flagged=0, verified=0):
    ledger = Ledger(document_path="x", document_format="md")
    for index in range(flagged + verified):
        entry = LedgerEntry(
            citation_id=f"c{index}",
            citation=RawCitation(kind=CiteKind.FULL_CASE, raw_text=f"X v. Y, {index} U.S. 1",
                                 location=Location(char_start=index, char_end=index + 4)))
        if index < flagged:
            entry.set_check("quote", CheckResult(
                Verdict.FAIL, evidence="the opinion says otherwise"))
        else:
            entry.set_check("existence", CheckResult(Verdict.PASS, evidence="found"))
        ledger.add(entry)
    return ledger


def test_one_unreadable_document_does_not_end_the_run(tmp_path):
    """The property this module exists to guarantee."""
    documents = [tmp_path / f"{n}.md" for n in ("a", "b", "c")]

    def runner(document, out_dir):
        if document.name == "b.md":
            raise OSError("that file is a directory")
        return _ledger(flagged=1)

    outcomes = run_batch(documents, tmp_path / "out", runner, quiet=True)
    assert len(outcomes) == 3
    assert [o.ok for o in outcomes] == [True, False, True]
    assert "that file is a directory" in outcomes[1].error


def test_a_failed_document_reports_no_counts(tmp_path):
    """A document that could not be read is unexamined, never clean."""
    def runner(document, out_dir):
        raise ValueError("unreadable")

    outcome = run_batch([tmp_path / "a.md"], tmp_path / "out", runner, quiet=True)[0]
    assert outcome.counts == {}
    assert outcome.flagged == 0
    assert not outcome.ok


def test_keyboard_interrupt_is_not_swallowed(tmp_path):
    def runner(document, out_dir):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_batch([tmp_path / "a.md"], tmp_path / "out", runner, quiet=True)


def test_documents_with_the_same_name_get_separate_directories(tmp_path):
    """Two matters routinely both contain a file called brief.docx."""
    seen = []

    def runner(document, out_dir):
        seen.append(out_dir)
        return _ledger()

    run_batch([tmp_path / "one" / "brief.md", tmp_path / "two" / "brief.md"],
              tmp_path / "out", runner, quiet=True)
    assert len(set(seen)) == 2


# --- the summary --------------------------------------------------------------

def test_the_summary_orders_most_severe_first(tmp_path):
    outcomes = [
        DocumentOutcome(path=tmp_path / "clean.md", counts={"VERIFIED": 3}, citations=3),
        DocumentOutcome(path=tmp_path / "bad.md", counts={"FLAGGED": 2}, citations=2),
    ]
    body = render_summary(outcomes)
    assert body.index("bad.md") < body.index("clean.md")


def test_the_summary_carries_the_governing_rule(tmp_path):
    body = render_summary([DocumentOutcome(path=tmp_path / "a.md", counts={})])
    assert "does not mean fabricated" in body
    assert "certifies nothing" in body


def test_a_clean_batch_is_not_described_as_a_clearance(tmp_path):
    body = render_summary([
        DocumentOutcome(path=tmp_path / "a.md", counts={"VERIFIED": 2}, citations=2)])
    assert "not a clearance" in body


def test_unreadable_documents_are_named_as_unexamined(tmp_path):
    body = render_summary([
        DocumentOutcome(path=tmp_path / "broken.pdf", error="OSError: nope")])
    assert "broken.pdf" in body
    assert "not clean; they are unexamined" in body


def test_write_summary_emits_both_artifacts(tmp_path):
    outcomes = [DocumentOutcome(path=tmp_path / "a.md", counts={"FLAGGED": 1}, citations=1)]
    path = write_summary(outcomes, tmp_path / "out")
    assert path.exists()
    assert (tmp_path / "out" / "batch.json").exists()
