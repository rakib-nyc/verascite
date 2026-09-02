"""Guards for untrusted input.

A citation checker is pointed at documents from opposing counsel, from the
internet, and from clients. The document is the attack surface, so the two
formats that carry structure -- ZIP and XML -- are opened defensively.

Measured before being written, against the real ingest path:

* **XML entity expansion.** A 618-byte `.docx` expands to 1,000,000 characters
  through five levels of nested internal entities -- 1,600x amplification.
  Python 3.10's expat happens to cap this at the sixth level, but that is a
  property of one parser version and not something to rely on. No legitimate
  `.docx` contains a DTD at all, so the whole construct is refused.

* **Compression bombs.** A 398 KB `.docx` carrying 419 MB of compressed zeros
  is read entirely into memory by the annotator. Uncompressed size is checked
  against the archive's own directory before anything is read.

Neither is exotic. Both are what you get by pointing a parser at a file
somebody else made.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

#: Refuse an archive whose members total more than this uncompressed. Real
#: briefs with images run to tens of megabytes; nothing legitimate approaches
#: this.
MAX_UNCOMPRESSED_BYTES = 300 * 1024 * 1024
#: Refuse a single part larger than this. word/document.xml for a 200-page
#: brief is a few megabytes.
MAX_PART_BYTES = 100 * 1024 * 1024
#: Refuse absurd compression ratios even under the size caps.
MAX_COMPRESSION_RATIO = 200

_DOCTYPE = re.compile(rb"<!DOCTYPE", re.IGNORECASE)
_ENTITY = re.compile(rb"<!ENTITY", re.IGNORECASE)


#: Characters XML 1.0 forbids outright. Word refuses to open a document
#: containing them, so they are stripped rather than escaped.
_XML_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MARKDOWN_BREAKING = re.compile(r"[|\r\n]+")


def flatten_for_output(text: str, limit: int = 300) -> str:
    """Make untrusted document text safe to interpolate into a report.

    Text from the document under audit is attacker-controlled in the ordinary
    case -- it arrives from opposing counsel, or from whatever produced the
    PDF. Interpolated raw it can forge report structure: a citation containing
    a newline and a `#` writes its own heading into the findings, and a pipe
    breaks the table row it sits in so the columns after it shift.

    Newlines and pipes therefore collapse to spaces, and control characters go
    entirely.
    """
    cleaned = _XML_ILLEGAL.sub("", text or "")
    cleaned = _MARKDOWN_BREAKING.sub(" ", cleaned)
    cleaned = " ".join(cleaned.split())
    return cleaned[: limit - 3] + "..." if len(cleaned) > limit else cleaned


def strip_xml_illegal(text: str) -> str:
    """Remove characters that XML 1.0 cannot represent at all."""
    return _XML_ILLEGAL.sub("", text or "")


class UnsafeDocument(RuntimeError):
    """The document is malformed or hostile enough that we decline to parse it."""


def check_archive(path: Path) -> zipfile.ZipFile:
    """Open a ZIP after checking what it claims to contain.

    The archive's own central directory is consulted first, so nothing is
    decompressed to find out that it should not have been.
    """
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise UnsafeDocument(f"{path.name} is not a readable .docx archive: {exc}") from exc

    total = 0
    for info in archive.infolist():
        if info.file_size > MAX_PART_BYTES:
            archive.close()
            raise UnsafeDocument(
                f"{path.name} contains a part of {info.file_size / 1e6:.0f} MB "
                f"({info.filename}), which is far beyond anything a brief needs. "
                "Refusing to read it."
            )
        if info.compress_size and info.file_size / max(info.compress_size, 1) > MAX_COMPRESSION_RATIO:
            archive.close()
            raise UnsafeDocument(
                f"{path.name} contains a part compressed {info.file_size // max(info.compress_size, 1)}:1 "
                f"({info.filename}). That is a compression bomb, not a document."
            )
        total += info.file_size

    if total > MAX_UNCOMPRESSED_BYTES:
        archive.close()
        raise UnsafeDocument(
            f"{path.name} expands to {total / 1e6:.0f} MB, beyond the "
            f"{MAX_UNCOMPRESSED_BYTES / 1e6:.0f} MB limit. Refusing to read it."
        )
    return archive


def safe_member(name: str) -> bool:
    """Is this a member name a document package should contain?

    Absolute paths and parent traversal have no legitimate place in a `.docx`.
    Nothing here extracts to the filesystem, so this is not a live traversal
    bug -- but such a member must not be copied into the annotated output
    either, where a naive extractor downstream would honour it.
    """
    if not name or name.startswith(("/", "\\")):
        return False
    if re.match(r"^[A-Za-z]:", name):
        return False
    return ".." not in Path(name.replace("\\", "/")).parts


def check_xml(payload: bytes, part: str) -> bytes:
    """Refuse XML carrying a document type definition.

    Internal entities are an amplification vector and external ones are an
    information-disclosure vector. A `.docx` produced by any word processor has
    neither, so refusing the whole construct costs nothing and closes both.
    """
    head = payload[:4096]
    if _DOCTYPE.search(head) or _ENTITY.search(head):
        raise UnsafeDocument(
            f"{part} declares a document type or XML entities. No word processor "
            "writes that, and it is the standard shape of an entity-expansion "
            "attack. Refusing to parse it."
        )
    return payload
