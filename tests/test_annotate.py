"""Annotating the original .docx with Word comments.

Built directly on the OOXML package because python-docx has no comments API.
Three parts must agree or Word silently drops every comment, so the tests check
the package, not just the text.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from verascite.annotate import AnnotationError, annotate_docx, comment_text
from verascite.extract import extract
from verascite.ingest import ingest
from verascite.ledger import Ledger
from verascite.resolve import resolve
from verascite.verdicts import CheckResult, Verdict

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'

CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.'
    'openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'
)
RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
    'relationships"></Relationships>'
)


def make_docx(path: Path, paragraphs: list[str]) -> Path:
    """A minimal but valid-shaped OOXML package."""
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("word/_rels/document.xml.rels", RELS)
        z.writestr("word/document.xml",
                   f'<?xml version="1.0"?><w:document {W}><w:body>{body}</w:body></w:document>')
    return path


def make_split_docx(path: Path) -> Path:
    """A citation broken across runs, as Word really stores an italic case name."""
    runs = (
        '<w:r><w:t xml:space="preserve">Accord </w:t></w:r>'
        '<w:r><w:rPr><w:i/></w:rPr><w:t>Bogus v. Nobody</w:t></w:r>'
        '<w:r><w:t xml:space="preserve">, 33 Umbrella 422 (2020)</w:t></w:r>'
        '<w:r><w:t xml:space="preserve"> is controlling.</w:t></w:r>'
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("word/_rels/document.xml.rels", RELS)
        z.writestr("word/document.xml",
                   f'<?xml version="1.0"?><w:document {W}><w:body><w:p>{runs}</w:p></w:body></w:document>')
    return path


def ledger_for(path: Path) -> Ledger:
    document = ingest(path)
    citations, notes = extract(document)
    ledger = resolve(document, citations, notes)
    for entry in ledger:
        if entry.citation.kind.value == "unrecognized":
            entry.set_check(
                "reporter_valid",
                CheckResult(Verdict.FAIL, evidence="reporter not in reporters-db"),
            )
    return ledger


def read(path: Path, name: str) -> str:
    with zipfile.ZipFile(path) as z:
        return z.read(name).decode("utf-8")


# --- the package must stay coherent -------------------------------------------


def test_all_three_parts_are_updated(tmp_path):
    source = make_docx(tmp_path / "brief.docx",
                       ["Accord Bogus v. Nobody, 33 Umbrella 422 (2020)."])
    out = tmp_path / "annotated.docx"
    assert annotate_docx(source, ledger_for(source), out) == 1

    with zipfile.ZipFile(out) as z:
        names = z.namelist()
    assert "word/comments.xml" in names
    assert "comments+xml" in read(out, "[Content_Types].xml")
    assert "comments.xml" in read(out, "word/_rels/document.xml.rels")


def test_the_comment_anchors_around_the_citation(tmp_path):
    source = make_docx(tmp_path / "brief.docx",
                       ["Accord Bogus v. Nobody, 33 Umbrella 422 (2020)."])
    out = tmp_path / "annotated.docx"
    annotate_docx(source, ledger_for(source), out)
    document = read(out, "word/document.xml")
    assert '<w:commentRangeStart w:id="0"/>' in document
    assert '<w:commentRangeEnd w:id="0"/>' in document
    assert 'w:commentReference w:id="0"' in document
    assert document.index("commentRangeStart") < document.index("commentRangeEnd")


def test_a_citation_split_across_runs_is_still_anchored(tmp_path):
    """Word splits runs at every formatting change; case names are italic."""
    source = make_split_docx(tmp_path / "split.docx")
    out = tmp_path / "annotated.docx"
    assert annotate_docx(source, ledger_for(source), out) == 1
    document = read(out, "word/document.xml")
    assert "commentRangeStart" in document and "commentRangeEnd" in document
    # The italic formatting of the case name survives the split.
    assert "<w:i/>" in document


def test_original_text_is_preserved_exactly(tmp_path):
    source = make_split_docx(tmp_path / "split.docx")
    out = tmp_path / "annotated.docx"
    annotate_docx(source, ledger_for(source), out)
    import re

    text = "".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", read(out, "word/document.xml")))
    assert text == "Accord Bogus v. Nobody, 33 Umbrella 422 (2020) is controlling."


# --- what gets a comment, and what it says ------------------------------------


def test_verified_citations_are_left_alone(tmp_path):
    """A comment on every citation is a document nobody reads."""
    source = make_docx(tmp_path / "clean.docx", ["Harlow v. Fitzgerald, 457 U.S. 800 (1982)."])
    ledger = ledger_for(source)
    for entry in ledger:
        for dimension in ("existence", "case_name", "court", "year"):
            entry.checks.pop(dimension, None)
            entry.set_check(dimension, CheckResult(Verdict.PASS, evidence="confirmed"))
    out = tmp_path / "annotated.docx"
    assert annotate_docx(source, ledger, out) == 0
    with zipfile.ZipFile(out) as z:
        assert "word/comments.xml" not in z.namelist()


def test_unverified_comments_say_it_is_not_a_fabrication_finding():
    from verascite.models import CiteKind, LedgerEntry, Location, RawCitation

    entry = LedgerEntry(
        citation_id="c",
        citation=RawCitation(kind=CiteKind.FULL_CASE, raw_text="A v. B, 1 WL 2",
                             location=Location(0, 14)),
    )
    entry.set_check("existence", CheckResult(Verdict.OUT_OF_SCOPE, reason="Westlaw-only"))
    text = comment_text(entry)
    assert "NOT a finding" in text
    assert "Check manually" in text


def test_suppressed_rows_are_left_out_of_the_margin(tmp_path):
    """A 2cm margin column is not the place for knock-on effects."""
    from verascite.models import CiteKind, LedgerEntry, Location, RawCitation

    entry = LedgerEntry(
        citation_id="c",
        citation=RawCitation(kind=CiteKind.FULL_CASE, raw_text="A v. B, 1 F.2d 2",
                             location=Location(0, 16)),
    )
    entry.set_check("reporter_valid", CheckResult(Verdict.FAIL, evidence="not a reporter"))
    entry.set_check("case_name", CheckResult(Verdict.NOT_CHECKABLE, reason="blocked",
                                             suppressed_by="reporter_valid"))
    text = comment_text(entry)
    assert "reporter_valid" in text
    assert "case_name" not in text


# --- refusals -----------------------------------------------------------------


def test_a_non_docx_source_is_refused_with_a_pointer_to_the_report(tmp_path):
    source = tmp_path / "brief.md"
    source.write_text("Bogus v. Nobody, 33 Umbrella 422 (2020).")
    with pytest.raises(AnnotationError, match="report.md"):
        annotate_docx(source, Ledger(), tmp_path / "out.docx")
