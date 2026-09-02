"""Stage 3: resolve short forms and build the initial ledger.

Resolution is implemented directly over ``RawCitation`` rather than through
``eyecite.resolve_citations`` so that every linkage decision is inspectable
and testable without a model or a network call (P10). Each resolution
records *why* it linked, in the entry's notes.

Deduplication happens here too: every citation carries an ``authority_key``,
and downstream lookups run once per distinct key rather than once per
citation. On real briefs this is the single largest cost reduction
available (spec 4.5).
"""

from __future__ import annotations

import re
from typing import Optional

from .config import snapshot as config_snapshot
from .extract import quote_spans
from .ledger import Ledger
from .models import CiteKind, Document, LedgerEntry, RawCitation
from .verdicts import CheckResult, Verdict

#: Citation kinds that stand on their own as an authority.
STANDALONE = {CiteKind.FULL_CASE, CiteKind.LAW, CiteKind.JOURNAL, CiteKind.UNRECOGNIZED}
#: Citation kinds that must point back at an antecedent to mean anything.
BACKREF = {CiteKind.SHORT_CASE, CiteKind.SUPRA, CiteKind.ID}


def authority_key(cite: RawCitation) -> Optional[str]:
    """Stable key shared by every citation pointing at the same authority."""
    if cite.kind is CiteKind.LAW:
        parts = [cite.title, cite.reporter, cite.section]
        if any(parts):
            return "law:" + " ".join(p for p in parts if p).lower()
    if cite.normalized:
        return re.sub(r"\s+", " ", cite.normalized).strip().lower()
    return None


def _name_tokens(text: Optional[str]) -> set[str]:
    if not text:
        return set()
    return {t for t in re.split(r"[^A-Za-z]+", text.lower()) if len(t) > 2}


def _matches_antecedent(candidate: RawCitation, guess: Optional[str]) -> bool:
    if not guess:
        return False
    guess_tokens = _name_tokens(guess)
    if not guess_tokens:
        return False
    known = _name_tokens(candidate.plaintiff) | _name_tokens(candidate.defendant)
    return bool(guess_tokens & known)


def _find_antecedent(
    cite: RawCitation, earlier: list[tuple[str, RawCitation]]
) -> tuple[Optional[str], str]:
    """Return (citation_id of antecedent, reason). Searches most-recent-first."""
    reversed_earlier = list(reversed(earlier))

    if cite.kind is CiteKind.ID:
        for cid, prev in reversed_earlier:
            if prev.kind in (CiteKind.ID,):
                continue
            return cid, f"id. bound to the immediately preceding authority ({prev.raw_text[:60]})"
        return None, "id. with no preceding authority in the document"

    if cite.kind is CiteKind.SHORT_CASE:
        for cid, prev in reversed_earlier:
            if prev.kind is not CiteKind.FULL_CASE:
                continue
            if prev.volume == cite.volume and prev.reporter == cite.reporter:
                if cite.antecedent_guess and not _matches_antecedent(prev, cite.antecedent_guess):
                    continue
                return cid, (
                    f"short form matched to full cite by volume+reporter "
                    f"({cite.volume} {cite.reporter})"
                )
        for cid, prev in reversed_earlier:
            if prev.kind is CiteKind.FULL_CASE and _matches_antecedent(
                prev, cite.antecedent_guess
            ):
                return cid, "short form matched to full cite by party name only"
        return None, "short form with no matching full citation earlier in the document"

    if cite.kind is CiteKind.SUPRA:
        for cid, prev in reversed_earlier:
            if prev.kind is CiteKind.FULL_CASE and _matches_antecedent(
                prev, cite.antecedent_guess
            ):
                return cid, (
                    f"supra matched to full cite by party name "
                    f"({cite.antecedent_guess!r})"
                )
        return None, "supra with no matching full citation earlier in the document"

    return None, ""


def _cluster_parallel(entries: list[LedgerEntry]) -> int:
    """Link adjacent full citations that describe one decision.

    ``Roe v. Wade, 410 U.S. 113, 93 S. Ct. 705 (1973)`` is one authority with
    two reporter cites. Each member still gets its own lookup -- that is how a
    bad parallel-cite member is caught -- but they are grouped so the report
    presents them together.
    """
    groups = 0
    full = [e for e in entries if e.citation.kind is CiteKind.FULL_CASE]
    for prev, cur in zip(full, full[1:]):
        gap = cur.citation.location.char_start - prev.citation.location.char_end
        if not (-2 <= gap <= 4):
            continue
        if cur.citation.case_name and prev.citation.case_name:
            if _name_tokens(cur.citation.case_name) != _name_tokens(prev.citation.case_name):
                continue
        root = prev.notes_parallel_root if hasattr(prev, "notes_parallel_root") else None
        anchor = root or prev.citation_id
        cur.notes.append(f"parallel citation of {anchor}")
        prev.notes.append(f"parallel citation with {cur.citation_id}")
        setattr(cur, "notes_parallel_root", anchor)
        groups += 1
    return groups


def resolve(doc: Document, cites: list[RawCitation], notes: list[str] | None = None) -> Ledger:
    """Build the ledger: every entry PENDING, short forms linked, dedup keyed."""
    ledger = Ledger(
        document_path=doc.source_path,
        document_format=doc.source_format,
    )
    ledger.run_meta["thresholds"] = config_snapshot()
    ledger.run_meta["extraction_notes"] = list(notes or [])
    ledger.run_meta["document_warnings"] = list(doc.warnings)

    seen: list[tuple[str, RawCitation]] = []

    for index, cite in enumerate(cites):
        cid = f"cite_{index:04d}"
        entry = LedgerEntry(citation_id=cid, citation=cite)

        if cite.kind in BACKREF:
            antecedent_id, reason = _find_antecedent(cite, seen)
            entry.antecedent_id = antecedent_id
            if reason:
                entry.notes.append(reason)
            if antecedent_id:
                parent = ledger.get(antecedent_id)
                entry.authority_key = parent.authority_key if parent else None
                # A short form inherits the antecedent's reporter identity so
                # its own pincite can be checked against the right opinion.
                if parent and not cite.case_name:
                    cite.case_name = parent.citation.case_name
            else:
                entry.set_check(
                    "existence",
                    CheckResult(
                        Verdict.NOT_CHECKABLE,
                        reason=(
                            "dangling back-reference: no antecedent full citation was "
                            "found earlier in the document, so there is nothing to look up"
                        ),
                    ),
                )
        else:
            entry.authority_key = authority_key(cite)

        ledger.add(entry)
        seen.append((cid, cite))

    groups = _cluster_parallel(list(ledger))
    if groups:
        ledger.run_meta.setdefault("extraction_notes", []).append(
            f"{groups} parallel-citation link(s) detected"
        )

    ledger.run_meta["distinct_authorities"] = len(ledger.distinct_authorities())
    ledger.run_meta["citations_found"] = len(ledger)
    ledger.run_meta["unparsed_spans"] = [
        note.split(": ", 1)[1]
        for note in ledger.run_meta.get("extraction_notes", [])
        if note.startswith("unparsed citation-shaped span: ")
    ]

    # Quote accounting (P3). A quote that could not be attached to a citation
    # is left unchecked, which is the right call -- but if a third of a brief's
    # quotations were never attributed, a report that does not say so invites
    # the reader to assume they were checked and cleared.
    found = len(quote_spans(doc.text))
    attributed = sum(len(e.citation.quoted_language) for e in ledger)
    ledger.run_meta["quotes"] = {
        "found_in_document": found,
        "attributed_to_a_citation": attributed,
        "unattributed": max(0, found - attributed),
        "checked_against_source": 0,
    }
    return ledger
