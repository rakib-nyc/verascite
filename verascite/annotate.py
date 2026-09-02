"""Annotate the original .docx with a Word comment at each finding.

This is the artifact lawyers actually use, because it meets them in the
document they are editing rather than in a separate report.

There is no comments API in python-docx, so the OOXML package is edited
directly. Three parts have to agree or Word silently drops the comments, and
one of them being wrong is why this looks fussier than it is:

* ``word/comments.xml`` -- the comment bodies.
* ``word/_rels/document.xml.rels`` -- a relationship pointing at that part.
* ``[Content_Types].xml`` -- an override declaring its content type.

Inside ``word/document.xml`` each annotated span is wrapped in
``w:commentRangeStart`` / ``w:commentRangeEnd`` with a following run carrying
``w:commentReference``.

The awkward part is that a citation is rarely one run. Word splits runs at
every formatting change, so *Aves v. Shah*, 997 F.2d 762 is typically an
italic run, a plain run, and more. Anchoring therefore works on the
concatenated text of a paragraph and then splits the runs at the boundaries it
needs, which preserves the original formatting of every fragment.
"""

from __future__ import annotations

import html
import re
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from xml.sax.saxutils import escape

from .ledger import Ledger
from .safety import check_archive, flatten_for_output, safe_member, strip_xml_illegal
from .models import LedgerEntry
from .verdicts import SEVERITY_ORDER, Overall, Verdict

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
COMMENTS_CT = (
    "application/vnd.openxmlformats-officedocument"
    ".wordprocessingml.comments+xml"
)
COMMENTS_REL = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments"
)
AUTHOR = "verascite"
INITIALS = "vc"

#: Only these get a comment. VERIFIED citations are left alone -- a document
#: with a comment on every citation is a document nobody reads.
ANNOTATED = (Overall.FLAGGED, Overall.UNVERIFIED, Overall.REVIEW)

_PARA_RE = re.compile(r"<w:p[ >].*?</w:p>|<w:p/>", re.DOTALL)
_RUN_RE = re.compile(r"<w:r[ >].*?</w:r>", re.DOTALL)
_TEXT_RE = re.compile(r"(<w:t(?:\s[^>]*)?>)(.*?)(</w:t>)", re.DOTALL)


#: Minimal replacements for package parts a malformed .docx may be missing.
#: Writing a valid output beats crashing on someone else's broken file.
EMPTY_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
    'relationships"></Relationships>'
)
EMPTY_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="xml" ContentType="application/xml"/></Types>'
)


class AnnotationError(RuntimeError):
    pass


def comment_text(entry: LedgerEntry) -> str:
    """What the margin note says. Short: it is read in a 2cm column."""
    lines = [f"{entry.overall.value}: {flatten_for_output(entry.citation.raw_text, limit=90)}"]
    for name, check in entry.checks.items():
        if check.verdict in (Verdict.PASS, Verdict.NA, Verdict.PENDING):
            continue
        if check.suppressed_by:
            continue
        detail = flatten_for_output(check.evidence or check.reason or "", limit=300)
        if detail:
            lines.append(f"- {name} ({check.verdict.value}): {detail}")
    if entry.overall is Overall.UNVERIFIED:
        lines.append(
            "- This is NOT a finding that the authority is fabricated. It means "
            "the sources consulted do not contain it. Check manually."
        )
    return "\n".join(lines)


def _runs_with_text(paragraph: str) -> list[tuple[int, int, str]]:
    """(start, end, run_xml) for each run in a paragraph, in document order."""
    return [(m.start(), m.end(), m.group()) for m in _RUN_RE.finditer(paragraph)]


def _run_text(run_xml: str) -> str:
    return "".join(html.unescape(m.group(2)) for m in _TEXT_RE.finditer(run_xml))


def _split_run(run_xml: str, cut: int) -> tuple[str, str]:
    """Split one run at a character offset into its visible text."""
    seen = 0
    for match in _TEXT_RE.finditer(run_xml):
        body = html.unescape(match.group(2))
        if seen + len(body) >= cut:
            local = cut - seen
            head = escape(body[:local])
            tail = escape(body[local:])
            before = (
                run_xml[: match.start()] + match.group(1) + head + match.group(3)
            )
            after = match.group(1) + tail + match.group(3) + run_xml[match.end() :]
            opening = run_xml[: run_xml.index(">") + 1]
            props = re.search(r"<w:rPr>.*?</w:rPr>", run_xml, re.DOTALL)
            prefix = opening + (props.group() if props else "")
            return before + "</w:r>", prefix + after
        seen += len(body)
    return run_xml, ""


def _annotate_paragraph(paragraph: str, targets: list[tuple[str, int]]) -> str:
    """Wrap each (text, comment_id) occurrence in this paragraph."""
    for needle, comment_id in targets:
        runs = _runs_with_text(paragraph)
        if not runs:
            continue
        flat = "".join(_run_text(r[2]) for r in runs)
        position = _find(flat, needle)
        if position is None:
            continue
        start, end = position

        rebuilt: list[str] = []
        cursor = 0
        opened = closed = False
        head = paragraph[: runs[0][0]]
        for _rs, _re_, run_xml in runs:
            body = _run_text(run_xml)
            run_start, run_end = cursor, cursor + len(body)
            piece = run_xml
            if not opened and run_start <= start < run_end:
                before, after = _split_run(piece, start - run_start)
                rebuilt.append(before)
                rebuilt.append(f'<w:commentRangeStart w:id="{comment_id}"/>')
                piece = after
                opened = True
                run_start = start
            if opened and not closed and run_start < end <= run_end:
                before, after = _split_run(piece, end - run_start)
                rebuilt.append(before)
                rebuilt.append(
                    f'<w:commentRangeEnd w:id="{comment_id}"/>'
                    f'<w:r><w:rPr><w:rStyle w:val="CommentReference"/></w:rPr>'
                    f'<w:commentReference w:id="{comment_id}"/></w:r>'
                )
                piece = after
                closed = True
            if piece:
                rebuilt.append(piece)
            cursor = run_end
        if not closed and opened:
            rebuilt.append(
                f'<w:commentRangeEnd w:id="{comment_id}"/>'
                f'<w:r><w:commentReference w:id="{comment_id}"/></w:r>'
            )
        paragraph = head + "".join(rebuilt) + paragraph[runs[-1][1] :]
    return paragraph


def _find(haystack: str, needle: str) -> Optional[tuple[int, int]]:
    """Locate a citation, tolerating whitespace differences."""
    index = haystack.find(needle)
    if index != -1:
        return index, index + len(needle)
    pattern = r"\s+".join(re.escape(w) for w in needle.split())
    match = re.search(pattern, haystack)
    return (match.start(), match.end()) if match else None


def _comments_part(entries: list[tuple[int, LedgerEntry]]) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    body = []
    for comment_id, entry in entries:
        paragraphs = "".join(
            f"<w:p><w:r><w:t xml:space=\"preserve\">{escape(strip_xml_illegal(line))}</w:t></w:r></w:p>"
            for line in comment_text(entry).split("\n")
        )
        body.append(
            f'<w:comment w:id="{comment_id}" w:author="{AUTHOR}" '
            f'w:initials="{INITIALS}" w:date="{stamp}">{paragraphs}</w:comment>'
        )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:comments xmlns:w="{W}">' + "".join(body) + "</w:comments>"
    )


def annotate_docx(source: Path, ledger: Ledger, destination: Path) -> int:
    """Write an annotated copy of `source`. Returns the number of comments."""
    source, destination = Path(source), Path(destination)
    if source.suffix.lower() != ".docx":
        raise AnnotationError(
            f"annotation needs a .docx source; {source.suffix or 'this file'} "
            "cannot carry comments. The findings are in report.md."
        )

    # Screen the archive before anything else, including before the shortcut
    # for a document with no findings. Copying a hostile file through unread
    # would hand it to whatever opens the output next.
    with check_archive(source) as archive:
        parts = {
            name: archive.read(name)
            for name in archive.namelist()
            if safe_member(name)
        }
    if "word/document.xml" not in parts:
        raise AnnotationError(f"{source.name} has no word/document.xml to annotate")

    findings = [
        e for e in sorted(ledger, key=lambda e: (SEVERITY_ORDER[e.overall],
                                                 e.citation.location.char_start))
        if e.overall in ANNOTATED
    ]
    if not findings:
        shutil.copyfile(source, destination)
        return 0

    numbered = list(enumerate(findings))
    if False:
        # Members with absolute or traversal paths are dropped rather than
        # copied into the output, where a naive extractor would honour them.
        parts = {
            name: archive.read(name)
            for name in archive.namelist()
            if safe_member(name)
        }
    if "word/document.xml" not in parts:
        raise AnnotationError(f"{source.name} has no word/document.xml to annotate")

    document = parts["word/document.xml"].decode("utf-8")
    by_paragraph: dict[int, list[tuple[str, int]]] = {}
    paragraphs = list(_PARA_RE.finditer(document))
    for comment_id, entry in numbered:
        needle = " ".join(entry.citation.raw_text.split())
        for index, match in enumerate(paragraphs):
            if _find("".join(_run_text(r[2]) for r in _runs_with_text(match.group())),
                     needle):
                by_paragraph.setdefault(index, []).append((needle, comment_id))
                break

    rebuilt, cursor = [], 0
    for index, match in enumerate(paragraphs):
        rebuilt.append(document[cursor : match.start()])
        rebuilt.append(
            _annotate_paragraph(match.group(), by_paragraph[index])
            if index in by_paragraph
            else match.group()
        )
        cursor = match.end()
    rebuilt.append(document[cursor:])
    parts["word/document.xml"] = "".join(rebuilt).encode("utf-8")

    parts["word/comments.xml"] = _comments_part(numbered).encode("utf-8")

    # A well-formed .docx always carries these, but a malformed one must
    # produce a clear failure or a valid output -- never a KeyError.
    rels_name = "word/_rels/document.xml.rels"
    rels = parts.get(rels_name, EMPTY_RELS.encode("utf-8")).decode("utf-8")
    if "comments.xml" not in rels:
        new_id = f"rIdVerascite{len(re.findall(r'<Relationship ', rels)) + 1}"
        rels = rels.replace(
            "</Relationships>",
            f'<Relationship Id="{new_id}" Type="{COMMENTS_REL}" '
            'Target="comments.xml"/></Relationships>',
        )
        parts[rels_name] = rels.encode("utf-8")

    types_name = "[Content_Types].xml"
    types = parts.get(types_name, EMPTY_TYPES.encode("utf-8")).decode("utf-8")
    if "comments+xml" not in types:
        types = types.replace(
            "</Types>",
            f'<Override PartName="/word/comments.xml" '
            f'ContentType="{COMMENTS_CT}"/></Types>',
        )
        parts[types_name] = types.encode("utf-8")

    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as out:
        for name, blob in parts.items():
            out.writestr(name, blob)
    return len(numbered)
