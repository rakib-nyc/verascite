"""Stage 1: normalize .docx / .pdf / .txt / .md into text plus an offset map.

Footnotes carry a large share of citations and are dropped by naive
extraction (spec 5.1), so .docx footnotes and endnotes are pulled from the
package parts explicitly and appended with their spans recorded.

OCR is never run implicitly: OCR noise creates phantom citations, which is
exactly the false-positive failure P1 exists to prevent.
"""

from __future__ import annotations

import re
import unicodedata
import zipfile
from pathlib import Path

from .models import Document
from .safety import UnsafeDocument, check_archive, check_xml, safe_member

TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".text"}

_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _normalize(text: str) -> str:
    """Conservative normalization. Offsets are computed *after* this runs."""
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace(" ", " ").replace(" ", " ").replace(" ", " ")
    text = text.replace("\u00ad", "")       # soft hyphen
    # Emphasis markers, replaced with spaces rather than deleted so that every
    # character offset still addresses the original document.
    #
    # PDF-to-text conversion italicises case names, which is how case names are
    # conventionally set: "*Heartland Regional Med. Ctr. v. Sebelius*". The
    # asterisk stops the case-name walk-back dead, so the citation is recorded
    # as "Regional Med.Ctr. v. Sebelius*" -- first party lost, marker kept.
    # That produces false positives (a correct citation scored against a
    # truncated name) and false negatives (a truncated name that still matches
    # by token overlap, hiding a real mismatch).
    text = re.sub(r"\*+", lambda m: " " * len(m.group()), text)
    text = re.sub(r"(?<=[\s(])_+(?=\S)|(?<=\S)_+(?=[\s).,;:]|$)",
                  lambda m: " " * len(m.group()), text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return text


def _ingest_text(path: Path) -> Document:
    raw = _normalize(path.read_text(encoding="utf-8", errors="replace"))
    return Document(
        text=raw,
        source_path=str(path),
        source_format=path.suffix.lstrip(".").lower() or "txt",
        page_map=[(0, len(raw), 1)],
    )


def _docx_paragraph_text(para) -> str:
    return "".join(node.text or "" for node in para.iter(f"{_W_NS}t"))


def _ingest_docx(path: Path) -> Document:
    """Body text, then footnotes and endnotes, with their spans recorded."""
    from xml.etree import ElementTree as ET

    warnings: list[str] = []
    chunks: list[str] = []
    footnote_spans: list[tuple[int, int]] = []

    with check_archive(path) as zf:
        names = {n for n in zf.namelist() if safe_member(n)}
        if "word/document.xml" not in names:
            raise UnsafeDocument(
                f"{path.name} has no word/document.xml; it is not a Word document."
            )

        body = ET.fromstring(check_xml(zf.read("word/document.xml"), "word/document.xml"))
        for para in body.iter(f"{_W_NS}p"):
            chunks.append(_docx_paragraph_text(para))
        body_text = _normalize("\n".join(chunks))
        parts = [body_text]
        cursor = len(body_text)

        for part_name, label, skip_types in (
            ("word/footnotes.xml", "FOOTNOTES", {"separator", "continuationSeparator"}),
            ("word/endnotes.xml", "ENDNOTES", {"separator", "continuationSeparator"}),
        ):
            if part_name not in names:
                continue
            tag = part_name.split("/")[-1].replace("s.xml", "")
            root = ET.fromstring(check_xml(zf.read(part_name), part_name))
            notes: list[str] = []
            for note in root.iter(f"{_W_NS}{tag}"):
                if note.get(f"{_W_NS}type") in skip_types:
                    continue
                nid = note.get(f"{_W_NS}id", "?")
                text = " ".join(
                    _docx_paragraph_text(p).strip()
                    for p in note.iter(f"{_W_NS}p")
                ).strip()
                if text:
                    notes.append(f"[{tag} {nid}] {text}")
            if not notes:
                continue
            header = f"\n\n===== {label} =====\n"
            block = _normalize(header + "\n".join(notes))
            start = cursor + len(header)
            parts.append(block)
            cursor += len(block)
            footnote_spans.append((start, cursor))
            warnings.append(f"{len(notes)} {label.lower()} appended and scanned for citations")

    text = "".join(parts)
    return Document(
        text=text,
        source_path=str(path),
        source_format="docx",
        page_map=[(0, len(text), 1)],
        footnote_spans=footnote_spans,
        warnings=warnings,
    )


def _ingest_pdf(path: Path) -> Document:
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise RuntimeError(
            "PDF ingest requires pdfplumber: pip install pdfplumber"
        ) from exc

    pages: list[str] = []
    page_map: list[tuple[int, int, int]] = []
    warnings: list[str] = []
    cursor = 0
    empty = 0

    with pdfplumber.open(str(path)) as pdf:
        for number, page in enumerate(pdf.pages, start=1):
            body = _normalize(page.extract_text() or "")
            if not body.strip():
                empty += 1
            block = body + "\n\n"
            pages.append(block)
            page_map.append((cursor, cursor + len(block), number))
            cursor += len(block)

    if empty:
        warnings.append(
            f"{empty} of {len(page_map)} PDF pages yielded no extractable text. "
            "This document may be a scan. OCR is NOT run automatically because "
            "OCR noise produces phantom citations; re-run with explicit consent "
            "after OCRing separately if these pages contain authority."
        )

    text = "".join(pages)
    return Document(
        text=text,
        source_path=str(path),
        source_format="pdf",
        page_map=page_map,
        warnings=warnings,
    )


def ingest(path) -> Document:
    """Normalize a document into text with page/footnote offsets preserved."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)

    suffix = path.suffix.lower()
    if suffix in TEXT_SUFFIXES:
        return _ingest_text(path)
    if suffix == ".docx":
        return _ingest_docx(path)
    if suffix == ".pdf":
        return _ingest_pdf(path)
    if suffix == ".doc":
        raise RuntimeError(
            "Legacy .doc is not supported. Convert to .docx or .pdf first."
        )
    raise RuntimeError(f"Unsupported format: {suffix or '(none)'}")
