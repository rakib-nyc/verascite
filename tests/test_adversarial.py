"""Hostile and malformed input.

A citation checker is pointed at documents from opposing counsel, from the
internet, and from clients. The document is the attack surface. Every payload
here was run against the real ingest path before the guard that stops it was
written.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from verascite.annotate import annotate_docx
from verascite.extract import extract
from verascite.ingest import ingest
from verascite.ledger import Ledger
from verascite.models import Document
from verascite.resolve import resolve
from verascite.safety import UnsafeDocument, check_xml, safe_member
from verascite.verdicts import Overall
from verascite.verify_existence import verify_existence
from verascite.verify_metadata import verify_metadata

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def docx(path: Path, document_xml: str, extra: dict | None = None) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", document_xml)
        for name, blob in (extra or {}).items():
            z.writestr(name, blob)
    return path


def body(text: str) -> str:
    return f'<?xml version="1.0"?><w:document {W}><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>'


def audit(text: str):
    document = Document(text=text, source_path="t", source_format="txt",
                        page_map=[(0, len(text), 1)])
    citations, notes = extract(document)
    ledger = resolve(document, citations, notes)
    verify_existence(ledger, client=None, offline=True)
    verify_metadata(ledger)
    return ledger


# --- XML entity expansion -----------------------------------------------------


def entity_bomb(depth: int) -> str:
    entities = ['<!ENTITY a0 "aaaaaaaaaa">']
    for i in range(1, depth + 1):
        entities.append(f'<!ENTITY a{i} "{"&a%d;" % (i - 1) * 10}">')
    return (f'<?xml version="1.0"?><!DOCTYPE d [{"".join(entities)}]>'
            f'<w:document {W}><w:body><w:p><w:r><w:t>&a{depth};</w:t></w:r>'
            "</w:p></w:body></w:document>")


@pytest.mark.parametrize("depth", [3, 4, 5])
def test_entity_expansion_is_refused(tmp_path, depth):
    """Measured: 618 bytes expanded to 1,000,000 characters at depth 5."""
    path = docx(tmp_path / "bomb.docx", entity_bomb(depth))
    with pytest.raises(UnsafeDocument, match="entities"):
        ingest(path)


def test_external_entities_are_refused(tmp_path):
    payload = ('<?xml version="1.0"?><!DOCTYPE d [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
               f'<w:document {W}><w:body><w:p><w:r><w:t>&xxe;</w:t></w:r></w:p></w:body></w:document>')
    with pytest.raises(UnsafeDocument):
        ingest(docx(tmp_path / "xxe.docx", payload))


def test_a_doctype_alone_is_enough_to_refuse():
    with pytest.raises(UnsafeDocument, match="document type"):
        check_xml(b'<?xml version="1.0"?><!DOCTYPE x><root/>', "word/document.xml")


def test_ordinary_xml_still_parses():
    assert check_xml(b'<?xml version="1.0"?><root/>', "word/document.xml")


# --- compression bombs --------------------------------------------------------


def test_a_compression_bomb_is_refused(tmp_path):
    """Measured: 398 KB carrying 419 MB, read entirely into memory."""
    path = tmp_path / "bomb.docx"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", body("hi"))
        z.writestr("word/media/big.bin", b"\0" * (400 * 1024 * 1024))
    with pytest.raises(UnsafeDocument, match="compression bomb|beyond"):
        ingest(path)


def test_the_annotator_refuses_the_same_bomb(tmp_path):
    path = tmp_path / "bomb.docx"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", body("Bogus v. Nobody, 33 Umbrella 422 (2020)."))
        z.writestr("word/media/big.bin", b"\0" * (400 * 1024 * 1024))
    with pytest.raises(UnsafeDocument):
        annotate_docx(path, Ledger(), tmp_path / "out.docx")


def test_a_corrupt_archive_is_reported_not_crashed(tmp_path):
    path = tmp_path / "junk.docx"
    path.write_bytes(b"this is not a zip file at all")
    with pytest.raises(UnsafeDocument, match="not a readable"):
        ingest(path)


def test_a_docx_without_a_document_part_is_refused(tmp_path):
    path = tmp_path / "empty.docx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/settings.xml", "<settings/>")
    with pytest.raises(UnsafeDocument, match="not a Word document"):
        ingest(path)


# --- traversal member names ---------------------------------------------------


@pytest.mark.parametrize(
    "name,ok",
    [("word/document.xml", True), ("../../etc/passwd", False), ("/etc/passwd", False),
     ("C:\\Windows\\evil", False), ("word/../../escape", False), ("", False)],
)
def test_member_name_screening(name, ok):
    assert safe_member(name) is ok


def test_traversal_members_are_not_copied_into_the_output(tmp_path):
    """Nothing extracts to disk here, but the output must not carry the trap on."""
    source = docx(
        tmp_path / "slip.docx",
        body("Accord Bogus v. Nobody, 33 Umbrella 422 (2020)."),
        {"../../../../tmp/evil.txt": "pwned"},
    )
    document = ingest(source)
    citations, notes = extract(document)
    ledger = resolve(document, citations, notes)
    verify_existence(ledger, client=None, offline=True)

    out = tmp_path / "annotated.docx"
    annotate_docx(source, ledger, out)
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
    assert not [n for n in names if ".." in n]
    assert "word/document.xml" in names


# --- output injection ---------------------------------------------------------
#
# Document text is attacker-controlled in the ordinary case: it arrives from
# opposing counsel, or from whatever produced the PDF. It flows into a Markdown
# report and into OOXML, both of which are injection surfaces.


def hostile_ledger(raw: str) -> Ledger:
    from verascite.models import CiteKind, LedgerEntry, Location, RawCitation
    from verascite.verdicts import CheckResult, Verdict

    ledger = Ledger(document_path="t")
    entry = LedgerEntry(
        citation_id="cite_0000",
        citation=RawCitation(kind=CiteKind.FULL_CASE, raw_text=raw,
                             location=Location(0, 10)),
    )
    entry.set_check("quote", CheckResult(Verdict.FAIL, evidence=raw))
    ledger.add(entry)
    return ledger


HOSTILE_TEXT = [
    "A v. B | col | col, 1 F.2d 1",
    "A v. B\n\n# Forged Heading\n\n## Summary\n\n| x |, 1 F.2d 1",
    "<script>alert(1)</script> v. B, 1 F.2d 1",
    "A\x00\x01\x02 v. B, 1 F.2d 1",
    "A v. B, 1 F.2d 1" + "\u0007" * 50,
]


@pytest.mark.parametrize("raw", HOSTILE_TEXT)
def test_a_citation_cannot_forge_report_structure(raw):
    """A newline and a '#' in a citation must not write a section heading."""
    import re

    from verascite.report import render_report

    report = render_report(hostile_ledger(raw), generated_at="x")
    headings = [l for l in report.splitlines() if re.match(r"^#{1,6}\s", l)]
    assert not [h for h in headings if "Forged" in h and not h.startswith("####")]


@pytest.mark.parametrize("raw", HOSTILE_TEXT)
def test_a_citation_cannot_break_a_report_table(raw):
    from verascite.report import render_report

    report = render_report(hostile_ledger(raw), generated_at="x")
    for row in report.splitlines():
        if row.startswith("|") and not set(row) <= set("|-: "):
            assert row.count("|") == 4, row


@pytest.mark.parametrize("raw", HOSTILE_TEXT + ["</w:t></w:r></w:p><w:p>INJECTED", "A & B < C > D"])
def test_the_annotated_docx_is_always_valid_xml(tmp_path, raw):
    """Control characters are illegal in XML 1.0; Word refuses such a file."""
    import xml.etree.ElementTree as ET

    source = docx(tmp_path / "src.docx", body("some text"))
    out = tmp_path / "out.docx"
    annotate_docx(source, hostile_ledger(raw), out)
    with zipfile.ZipFile(out) as z:
        ET.fromstring(z.read("word/comments.xml").decode("utf-8"))
        ET.fromstring(z.read("word/document.xml").decode("utf-8"))


# --- malformed API responses --------------------------------------------------


def apply(payload: dict):
    from verascite.clients.courtlistener import LookupResult
    from verascite.verify_existence import _apply_lookup

    ledger = audit("Harlow v. Fitzgerald, 457 U.S. 800 (1982).")
    entry = next(iter(ledger))
    entry.checks.pop("existence", None)
    _apply_lookup(entry, LookupResult(citation="457 U.S. 800", **payload))
    verify_metadata(ledger)
    return entry


@pytest.mark.parametrize(
    "payload",
    [
        {"status": 200, "clusters": []},
        {"status": 200, "clusters": [{}]},
        {"status": 200, "clusters": [{"id": None, "case_name": None}]},
        {"status": 200, "clusters": [{"id": 1, "case_name": {"nested": "object"}}]},
        {"status": 200, "clusters": ["not a dict at all"]},
        {"status": 200, "clusters": [{"id": "not-an-int"}]},
        {"status": "two hundred"},
        {"status": -999},
        {"status": 200, "clusters": [{"id": 1, "date_filed": "not-a-date"}]},
    ],
)
def test_malformed_api_responses_never_crash(payload):
    """A response that is not the shape the docs promise must not become a crash."""
    assert apply(payload).overall in tuple(Overall)


def test_a_cluster_that_is_not_a_record_yields_no_metadata_verdicts():
    """Garbage in must not produce an accusation out."""
    entry = apply({"status": 200, "clusters": ["garbage"]})
    assert entry.overall is not Overall.FLAGGED


# --- empty and degenerate documents -------------------------------------------


@pytest.mark.parametrize("text", ["", "   ", "\n\n\t", "no citations here at all"])
def test_documents_without_citations_do_not_crash(text):
    """eyecite raises on empty input; a blank page is a legitimate thing to be given."""
    ledger = audit(text)
    assert len(ledger) == 0


def test_an_empty_document_still_produces_a_report():
    from verascite.report import render_report

    report = render_report(audit(""), generated_at="x")
    assert "evidence package" in report


# --- the command line -----------------------------------------------------
#
# A traceback is a reasonable thing to show a developer and an unreasonable
# thing to show a lawyer on a filing deadline.


def run_cli(*argv) -> tuple[int, str]:
    import io
    from contextlib import redirect_stderr

    from verascite.run_audit import main

    err = io.StringIO()
    with redirect_stderr(err):
        code = main(list(argv))
    return code, err.getvalue()


def test_a_missing_file_gives_one_line_not_a_traceback(tmp_path):
    code, err = run_cli(str(tmp_path / "absent.md"), "--out", str(tmp_path / "o"),
                        "--offline", "--quiet")
    assert code == 2
    assert "no such file" in err
    assert "Traceback" not in err


def test_a_hostile_document_gives_one_line_not_a_traceback(tmp_path):
    path = docx(tmp_path / "bomb.docx", entity_bomb(4))
    code, err = run_cli(str(path), "--out", str(tmp_path / "o"), "--offline", "--quiet")
    assert code == 2
    assert "Refusing to parse" in err
    assert "Traceback" not in err


def test_an_unsupported_format_explains_itself(tmp_path):
    path = tmp_path / "old.doc"
    path.write_text("x")
    code, err = run_cli(str(path), "--out", str(tmp_path / "o"), "--offline", "--quiet")
    assert code == 2
    assert "Convert to .docx" in err


def test_findings_and_failures_have_different_exit_codes(tmp_path):
    """A pipeline must tell 'this brief has a problem' from 'this tool broke'."""
    brief = tmp_path / "brief.md"
    brief.write_text("Accord Bogus v. Nobody, 33 Umbrella 422 (2020).")
    code, _ = run_cli(str(brief), "--out", str(tmp_path / "o"), "--offline", "--quiet")
    assert code == 1

    clean = tmp_path / "clean.md"
    clean.write_text("This brief cites no authority at all.")
    code, _ = run_cli(str(clean), "--out", str(tmp_path / "o2"), "--offline", "--quiet")
    assert code == 0
