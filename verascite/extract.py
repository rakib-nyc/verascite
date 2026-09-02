"""Stage 2: extract every authority from a normalized document.

Two passes:

1. **eyecite** -- the parser CourtListener itself uses, over reporters-db and
   courts-db. Do not write a citation regex; the reporter space is hostile.

2. **Shadow scan** -- a permissive scan for citation-shaped spans that eyecite
   did *not* claim.

The second pass exists because of a measured gap. eyecite is a
reporters-db-driven tokenizer: a citation whose reporter is not in the
database does not produce an ``UnknownCitation``, it produces *nothing*::

    >>> get_citations("Bogus v. Nobody, 33 Umbrella 422 (2020).")
    []

That is the exact signature of failure mode 2 in the spec taxonomy
("reporter invalid"), and the whole point of it is that it is a near-
dispositive fabrication signal. Without this pass, a fabricated reporter is
invisible to the pipeline and the document reports clean -- a silent pass,
which P6 forbids. So citation-shaped spans eyecite skipped are recovered
here and checked against reporters-db directly.
"""

from __future__ import annotations

import io
import logging
import re
from contextlib import redirect_stderr, redirect_stdout
from typing import Iterable

from eyecite import get_citations
from eyecite.models import (
    FullCaseCitation,
    FullJournalCitation,
    FullLawCitation,
    IdCitation,
    ShortCaseCitation,
    SupraCitation,
)
from reporters_db import EDITIONS, JOURNALS, LAWS, VARIATIONS_ONLY

from .config import QUOTE_MAX_ATTRIBUTION_GAP
from .models import CiteKind, Document, Location, RawCitation

# --- reporter validity --------------------------------------------------------


def _rep_key(name: str) -> str:
    """Canonical key for reporter lookup.

    Curly apostrophes matter: reporters-db stores "Fed. App'x" with a straight
    apostrophe, and OCR emits the typographic one. Matching literally reported
    the Federal Appendix -- a real reporter -- as invented.
    """
    name = name.replace("\u2019", "'").replace("\u02bc", "'")
    return re.sub(r"\s+", " ", name).strip().lower()


def _rep_variants(name: str) -> set[str]:
    """Punctuation and spacing variants of a reporter or journal name.

    Briefs write "ALA.L.REV." where the database has "Ala. L. Rev."; both name
    the Alabama Law Review.
    """
    base = _rep_key(name)
    return {v for v in (base, base.replace(" ", ""), base.rstrip("."),
                        base.replace(".", "").replace(" ", "")) if v}


#: Every reporter abbreviation American law recognizes, canonical plus variant.
KNOWN_REPORTERS: frozenset[str] = frozenset(
    {v for k in EDITIONS for v in _rep_variants(k)}
    | {v for k in VARIATIONS_ONLY for v in _rep_variants(k)}
)


def _journal_names() -> set[str]:
    """Journal abbreviations *and* their spelled-out names.

    reporters-db keys journals by abbreviation ("Or. L. Rev.") and carries the
    full name as a value ("Oregon Law Review"). eyecite matches the key only,
    so a brief citing "84 Oregon Law Review 227" parses as nothing at all --
    and the shadow scan then reports a real law review as an invented
    reporter. Both forms are needed here.
    """
    names: set[str] = set()
    for key, value in JOURNALS.items():
        names |= _rep_variants(key)
        entries = value if isinstance(value, list) else [value]
        for entry in entries:
            if isinstance(entry, dict) and entry.get("name"):
                names |= _rep_variants(entry["name"])
    return names


#: Journals and statutory compilations. These are citation-shaped and are not
#: case reporters, so they must never be reported as non-existent reporters.
#: Government publications that are citation-shaped but are not case
#: reporters. The Federal Register in particular is cited constantly in
#: administrative-law briefs and is not in reporters-db.
_GOVERNMENT_PUBLICATIONS = (
    "Fed. Reg.", "FR", "Federal Register", "C.F.R.", "CFR",
    "Code of Federal Regulations", "Stat.", "Statutes at Large",
    "Pub. L.", "U.S.C.", "U.S.C.A.", "U.S.C.S.", "Cong. Rec.",
    "Congressional Record", "Op. O.L.C.", "Comp. Gen.",
)

NON_REPORTER_SOURCES: frozenset[str] = frozenset(
    _journal_names()
    | {v for k in LAWS for v in _rep_variants(k)}
    | {v for k in _GOVERNMENT_PUBLICATIONS for v in _rep_variants(k)}
)


def is_non_case_source(name: str) -> bool:
    """A journal, statute, or government publication rather than a reporter."""
    return bool(_rep_variants(name) & NON_REPORTER_SOURCES)


def reporter_is_known(reporter: str) -> bool:
    """Is this a reporter abbreviation American law recognizes?

    Tries punctuation variants before answering no. reporters-db stores
    "U.S." with the period; a candidate captured as "U.S" (or "U. S.") is the
    same reporter, and answering no would FLAG a real citation as fabricated.
    """
    base = _rep_key(reporter)
    if not base:
        return False
    variants = {
        base,
        base.rstrip("."),
        base + ".",
        base.replace(". ", "."),
        base.replace(".", ". ").strip(),
        base.replace(" ", ""),
    }
    variants.add(re.sub(r"\.(?=\S)", ". ", base))
    return any(v in KNOWN_REPORTERS for v in variants if v)



def collapse_whitespace(text: str) -> tuple[str, list[int]]:
    """Collapse whitespace runs to single spaces, keeping a map to the original.

    eyecite's tokenizer regexes match a *literal single space* between the
    volume, reporter, and page, so a citation broken across a line -- which is
    ordinary in a real brief -- is not tokenized at all::

        get_citations("Ashcroft v. Iqbal, 556\nU.S. 662 (2009)")  -> []

    Its own ``clean_steps`` fix that, but they rewrite the string before
    tokenizing, so the spans come back indexing the cleaned copy and every
    offset downstream of a blank line is wrong. Verdicts have to point at real
    locations in the user's document, so neither option is acceptable on its
    own.

    This returns the cleaned text alongside ``index_map``, where
    ``index_map[i]`` is the offset in the original text of ``clean[i]``, so
    eyecite's spans can be translated straight back.
    """
    out: list[str] = []
    index_map: list[int] = []
    in_run = False
    for offset, char in enumerate(text):
        if char.isspace():
            if in_run:
                continue
            out.append(" ")
            index_map.append(offset)
            in_run = True
        else:
            out.append(char)
            index_map.append(offset)
            in_run = False
    index_map.append(len(text))
    return "".join(out), index_map


def _to_original(span: tuple[int, int], index_map: list[int]) -> tuple[int, int]:
    start, end = span
    start = index_map[min(max(start, 0), len(index_map) - 1)]
    end = index_map[min(max(end, 0), len(index_map) - 1)]
    return start, end


# --- Introductory signals -----------------------------------------------------

#: Ordered longest-first so "see also" wins over "see".
SIGNALS = (
    "but see also", "see generally", "but see", "see also", "see, e.g.,",
    "compare", "contra", "accord", "cf.", "but cf.", "e.g.,", "see",
)
_SIGNAL_RE = re.compile(
    r"(?:^|[\s;.,(])(" + "|".join(re.escape(s) for s in SIGNALS) + r")\s*$",
    re.IGNORECASE,
)

_QUOTE_RE = re.compile(r"[\"“]([^\"“”]{8,600})[\"”]")
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"“])")


def _signal_before(text: str, start: int) -> str | None:
    window = text[max(0, start - 40) : start]
    match = _SIGNAL_RE.search(window)
    return match.group(1).lower() if match else None


#: A proposition shorter than this is a fragment, not an assertion. Citations
#: inside a string cite sit in a clause, and the claim they support is in the
#: sentence before it.
_MIN_PROPOSITION_CHARS = 80


#: Citation furniture that carries no proposition: reporter cites, short forms,
#: introductory signals, and the parenthetical year. A citation sentence is
#: built entirely out of these, which is why raw length cannot tell a claim
#: from a string cite -- "See A v. B, 1 U.S. 2 (1900); C v. D, 3 F.2d 4 (1910)"
#: is 60 characters of citation and no assertion at all.
_CITATION_FURNITURE = re.compile(
    r"\b\d{1,4}\s+[A-Z][A-Za-z0-9'\u2019&.\-]*(?:\s+[A-Z0-9][A-Za-z0-9'\u2019&.\-]*){0,3}"
    r"\s+\d{1,5}(?:\s*[,-]\s*\d{1,5})*"
    r"|\((?:19|20|1[0-8])\d\d\)"
    r"|\b(?:see|see\s+also|see,\s*e\.g\.,|e\.g\.|accord|cf\.|but\s+see|contra|"
    r"but\s+cf\.|id\.|supra|infra|at)\b"
    r"|\b[A-Z][A-Za-z'\u2019.\-]*\s+v\.\s+[A-Z][A-Za-z'\u2019.\-]*"
    r"|[*_;,()\[\]]",
    re.IGNORECASE,
)


def _prose_chars(sentence: str) -> int:
    """Characters of actual assertion in ``sentence``, ignoring citation text.

    A citation sentence and a string cite are both long and both say nothing.
    Measuring what is left after the citation furniture is removed is what
    separates "the district court correctly held that X" from
    "See SEC v. Chenery Corp., 332 U.S. 194 (1947);".
    """
    stripped = _CITATION_FURNITURE.sub(" ", sentence)
    return len(" ".join(stripped.split()))


def _sentence_around(text: str, start: int, end: int) -> str:
    """Verbatim proposition the citation supports (spec 5.3: substrings only).

    The assertion a citation supports is normally the sentence it closes. A
    citation buried in a string cite closes only a clause, so a bare sentence
    scan returns something like "See id. at 197-98; National Ass'n of Home
    Builders v." -- 53 characters of citation and no claim at all. Where that
    happens the window extends backward to pick up the sentence that actually
    states the proposition.
    """
    sentence = _sentence_span(text, start, end)
    if _prose_chars(sentence) >= _MIN_PROPOSITION_CHARS:
        return sentence

    left = max(0, start - 700)
    window = text[left:end]
    pieces = [p.strip() for p in _SENT_SPLIT.split(window) if p.strip()]
    extended: list[str] = []
    for piece in reversed(pieces):
        extended.insert(0, piece)
        if _prose_chars(" ".join(extended)) >= _MIN_PROPOSITION_CHARS:
            break
    joined = " ".join(extended).strip()
    return joined if _prose_chars(joined) > _prose_chars(sentence) else sentence


def _sentence_span(text: str, start: int, end: int) -> str:
    left = text.rfind("\n", 0, start)
    window_start = max(0, start - 900, left + 1 if left != -1 else 0)
    right = text.find("\n", end)
    window_end = min(len(text), end + 900, right if right != -1 else len(text))
    window = text[window_start:window_end]
    rel_start = start - window_start

    best = window
    cursor = 0
    for piece in _SENT_SPLIT.split(window):
        piece_end = cursor + len(piece)
        if cursor <= rel_start < piece_end + 2:
            best = piece
            break
        cursor = window.find(piece, piece_end) if False else piece_end + 1
    return best.strip()


def quote_spans(text: str) -> list[tuple[int, int, str]]:
    """All quoted spans in the document, paired from the start of the text.

    Pairing has to be global. Scanning only a window around a citation can
    begin mid-quotation, in which case the first quote mark encountered is a
    *closing* one and every subsequent pair is off by one -- which yields
    "quotes" made of the prose between two real quotations, and then reports
    them as misquotes. Pairing once over the whole document avoids that.
    """
    spans: list[tuple[int, int, str]] = []
    open_at: int | None = None
    for index, char in enumerate(text):
        if char == "\u201c":
            open_at = index
        elif char == "\u201d":
            if open_at is not None:
                spans.append((open_at, index + 1, text[open_at + 1 : index]))
                open_at = None
        elif char == '"':
            if open_at is None:
                open_at = index
            else:
                spans.append((open_at, index + 1, text[open_at + 1 : index]))
                open_at = None
    return [(a, b, body) for a, b, body in spans if 8 <= len(body) <= 600]


#: Text that may separate two citations of a single string cite. Anything else
#: -- a sentence terminator, prose -- means the second citation belongs to a
#: different assertion and is not competing to own the quotation.
_STRING_CITE_JOIN = re.compile(r"^[\s;,]*$")


def _gap_to(quote_start: int, quote_end: int, bound: tuple[int, int]):
    """(gap, side) between a quotation and a citation, or None if they overlap.

    Side matters: a citation before the quotation and one after it are not
    competing to own it, but two citations after it -- a string cite -- are.
    """
    cite_start, cite_end = bound
    if cite_start >= quote_end:
        return cite_start - quote_end, "after"
    if cite_end <= quote_start:
        return quote_start - cite_end, "before"
    return None


def assign_quotes(
    citations: list[RawCitation],
    spans: list[tuple[int, int, str]],
    text: str = "",
    max_gap: int = QUOTE_MAX_ATTRIBUTION_GAP,
) -> None:
    """Attach each quoted passage to the citation that vouches for it.

    Legal writing puts a quotation immediately beside its citation, in one of
    two orders::

        The Court held that "...." Twombly, 550 U.S. at 555.
        Twombly, 550 U.S. at 555 ("....").

    So a quote is assigned to the nearest citation on either side, provided
    nothing but a short gap separates them and no other citation intervenes.
    Sentence-boundary heuristics were tried first and are not usable here: a
    quotation ending in `."` looks exactly like the end of a sentence, and a
    citation followed by `.\n\n` does not match a naive `". "` terminator, so
    windows silently ran on into the next paragraph and picked up the next
    citation's quote.

    Each quote goes to at most one citation. An unassigned quote is left
    unchecked rather than guessed at -- checking a quote against the wrong
    opinion produces a misquote finding that is not one.
    """
    if not citations:
        return
    bounds = [(c.location.char_start, c.location.char_end) for c in citations]

    def intervenes(low: int, high: int, skip: int) -> bool:
        return any(
            index != skip and low < end and start < high
            for index, (start, end) in enumerate(bounds)
        )

    for quote_start, quote_end, body in spans:
        best: tuple[int, int] | None = None  # (distance, citation index)

        for index, (cite_start, cite_end) in enumerate(bounds):
            measured = _gap_to(quote_start, quote_end, (cite_start, cite_end))
            if measured is None:
                continue
            gap, _side = measured
            if gap > max_gap:
                continue
            low, high = min(quote_end, cite_end), max(quote_start, cite_start)
            if intervenes(low, high, index):
                continue
            if best is None or gap < best[0]:
                best = (gap, index)

        if best is None:
            continue

        # If a second citation sits about as close as the winner, the quotation
        # cannot be attributed confidently. String cites -- "...quote." A v. B,
        # 1 F.3d 1; C v. D, 2 F.3d 2 -- are the common case, and guessing
        # between them means checking a quotation against the wrong opinion and
        # reporting a misquote that is not one.
        gap, winner = best
        winner_start, winner_end = bounds[winner]
        contested = False
        for index, (other_start, other_end) in enumerate(bounds):
            if index == winner:
                continue
            # Only a citation joined to the winner by string-cite punctuation
            # competes for the quotation: "...quote." A v. B, 1 F.3d 1; C v. D,
            # 2 F.3d 2. A citation in the next sentence is about something else.
            if other_start >= winner_end:
                between = text[winner_end:other_start]
            elif other_end <= winner_start:
                between = text[other_end:winner_start]
            else:
                continue
            if _STRING_CITE_JOIN.match(between):
                contested = True
                break
        if contested:
            continue

        citations[winner].quoted_language.append(body.strip())


# --- pass 1: eyecite ----------------------------------------------------------

_KIND_BY_TYPE = [
    (FullCaseCitation, CiteKind.FULL_CASE),
    (ShortCaseCitation, CiteKind.SHORT_CASE),
    (SupraCitation, CiteKind.SUPRA),
    (IdCitation, CiteKind.ID),
    (FullLawCitation, CiteKind.LAW),
    (FullJournalCitation, CiteKind.JOURNAL),
]


def _kind_of(cite) -> CiteKind:
    for cls, kind in _KIND_BY_TYPE:
        if isinstance(cite, cls):
            return kind
    return CiteKind.UNRECOGNIZED


def _case_name(plaintiff: str | None, defendant: str | None) -> str | None:
    if plaintiff and defendant:
        return f"{plaintiff} v. {defendant}"
    return defendant or plaintiff or None



_SENT_BREAK_RE = re.compile(r"(?<=[.!?])\s+(?=[\"\u201cA-Z])")
_TRAILING_PAREN_RE = re.compile(r"^[\s,]*\([^()]{0,80}\)")


def _tighten_span(text: str, cite, prev_core_end: int) -> tuple[int, int]:
    """Compute an accurate document span for a citation.

    ``eyecite.full_span()`` is not usable directly. For parallel citations it
    returns the span of the whole citation *group*, and that group can run past
    a sentence boundary into the following case::

        "Roe v. Wade, 410 U.S. 113, 93 S. Ct. 705 (1973) is foundational.
         Aves v. Shah, 997 F.2d 762 ..."
        full_span() for 410 U.S. 113  ->  the entire 113-character string above

    Left-clamping to the previous citation and right-bounding at the pincite
    plus its own court/year parenthetical keeps each entry pointing at the text
    it actually describes.
    """
    core_start, core_end = cite.span()
    try:
        full_start, _ = cite.full_span()
    except Exception:  # pragma: no cover - short forms
        full_start = core_start
    try:
        _, pin_end = cite.span_with_pincite()
    except Exception:  # pragma: no cover
        pin_end = core_end

    start = max(min(full_start, core_start), prev_core_end)
    # The previous-citation clamp must never push the start past the citation
    # itself. eyecite can return overlapping or out-of-order spans on degenerate
    # input -- citations run together with no separators -- and the clamp then
    # inverts the span, producing an empty raw_text and a location that points
    # nowhere.
    start = min(start, core_start)

    # Anchor on the case-name structure in the text itself rather than on
    # eyecite's party metadata, which is not always drawn from this citation
    # (it will happily report the previous sentence's defendant). Sentence
    # detection is the last resort, because it is unreliable in legal prose --
    # "Roe v. Wade" is a period followed by a capital letter.
    prefix = text[start:core_start]
    marker = None
    for candidate in _CASE_MARKER_RE.finditer(prefix):
        marker = candidate
    if marker is not None:
        start = _case_name_start(text, start + marker.start(), start)
    else:
        breaks = list(_SENT_BREAK_RE.finditer(prefix))
        if breaks:
            start += breaks[-1].end()
    while start < core_start and text[start] in " \t\n,;:":
        start += 1

    end = max(core_end, pin_end)
    # "(10th Cir. 1993) (Stevens, J., dissenting)" is two parentheticals, and
    # the second one is what tells us the language is a separate writing.
    for _ in range(3):
        trailing = _TRAILING_PAREN_RE.match(text[end : end + 90])
        if not trailing:
            break
        end += trailing.end()
    # Belt and braces: a span must always address real text, ascending.
    start = max(0, min(start, len(text)))
    end = max(start, min(end, len(text)))
    return start, end




_YEAR_IN_TEXT_RE = re.compile(r"\((?:[^()]*?)(1[6-9]\d\d|20\d\d)\)")

#: Reporters published only by one court, so the court is implied by the cite.
_SCOTUS_REPORTERS = {"u.s.", "s. ct.", "s.ct.", "l. ed.", "l. ed. 2d", "l.ed.", "l.ed.2d"}


def _corroborated_year(cite, raw_text: str) -> str | None:
    """Take the year from the citation's own parenthetical, not from metadata.

    eyecite derives metadata from ``full_span()``, which over-extends across
    adjacent citations. On a real brief it reported year 1993 for both
    *Twombly* (2007) and *Iqbal* (2009), having reached a *1993* citation two
    sentences later. A wrong year here is a FAIL verdict against a real case,
    so the year is read out of the citation's own text and metadata is used
    only when it agrees.
    """
    matches = _YEAR_IN_TEXT_RE.findall(raw_text)
    if matches:
        return matches[-1]
    year = getattr(cite.metadata, "year", None)
    return str(year) if year and str(year) in raw_text else None


def _corroborated_court(cite, raw_text: str) -> str | None:
    """Keep eyecite's court id only if the citation itself supports it."""
    court_id = getattr(cite.metadata, "court", None)
    if not court_id:
        return None

    flat = " ".join(raw_text.split())
    try:
        from courts_db import find_court_by_id

        for record in find_court_by_id(court_id) or []:
            label = record.get("citation_string")
            if label and label in flat:
                return court_id
    except Exception:  # pragma: no cover - courts-db guard
        pass

    # A court-specific reporter implies its court without naming it:
    # "457 U.S. 800 (1982)" is the Supreme Court whether or not it says so.
    reporter = (getattr(cite, "groups", {}) or {}).get("reporter", "")
    if court_id == "scotus" and reporter.strip().lower() in _SCOTUS_REPORTERS:
        return court_id
    return None


_COURT_YEAR = re.compile(
    r"^\s*(?:\d{1,2}(?:st|nd|rd|th)\s+Cir\.?|[NSEWMD]\.?\s*D\.|Cir\.|"
    r"\d{4}|[A-Z][a-z]*\.?\s*(?:App|Ct|Sup)\.?)[\s.,]*\d{0,4}\s*$"
)


_V_MARKER = re.compile(r"\s+v\.?s?\.?\s+", re.IGNORECASE)

#: The signature of eyecite's apostrophe truncation: a token ending in an
#: apostrophe, left behind when the letter after it was stripped as a word.
_TRUNCATED_APOSTROPHE = re.compile(r"[A-Za-z]['\u2019](?=\s|$)")


def case_name_from_span(raw_text: str, volume: str | None, reporter: str | None) -> str | None:
    """Recover the case name from the citation's own text.

    eyecite's plaintiff field is corrupted for any party name containing an
    apostrophe. ``helpers.py`` strips lowercase stop-words with
    ``re.sub(r"\b[a-z]\w*\b", "", plaintiff)``, and an apostrophe is a word
    boundary, so the ``l`` of ``Nat'l`` and the ``n`` of ``Ass'n`` are removed
    as though they were words::

        "Nat'l Ass'n of Mfrs. v. Dep't of Def."  ->  plaintiff "Nat' Ass'  Mfrs."

    Contracted abbreviations are pervasive in institutional party names --
    Nat'l, Ass'n, Comm'n, Dep't, Int'l, Sec'y, Gov't, Fed'n, P'ship -- so this
    silently corrupts a large share of comparisons before they are made.

    The span this tool computes already starts at the case name, so the name
    can be read straight out of it, uncorrupted.
    """
    if not raw_text or not volume or not reporter:
        return None
    flat = " ".join(raw_text.split())
    cut = flat.find(f"{volume} {reporter}")
    if cut <= 0:
        cut = flat.find(str(volume))
    if cut <= 0:
        return None
    name = flat[:cut].strip().rstrip(",").strip()
    if not name or not _V_MARKER.search(name):
        return name or None
    return name


def _corroborate(name: str | None, raw_text: str) -> str | None:
    """Keep a parsed party name only if it occurs in the citation's own text.

    Also rejects court-and-year fragments. eyecite sometimes reports the
    contents of a trailing parenthetical as a party -- "9th Cir. 1996" -- and
    comparing that against a real caption produces a confident mismatch about
    nothing.
    """
    if not name or not name.strip():
        return None
    name = name.strip()
    if _COURT_YEAR.match(name):
        return None
    return name if name in " ".join(raw_text.split()) else None


def _from_eyecite(
    doc: Document,
    cite,
    index: int,
    prev_core_end: int,
    clean: str,
    index_map: list[int],
    spans: list | None = None,
) -> RawCitation:
    kind = _kind_of(cite)
    md = cite.metadata
    groups = dict(getattr(cite, "groups", {}) or {})

    clean_start, clean_end = _tighten_span(clean, cite, prev_core_end)
    full_start, full_end = _to_original((clean_start, clean_end), index_map)

    normalized = None
    if groups.get("volume") and groups.get("reporter") and groups.get("page"):
        normalized = f"{groups['volume']} {groups['reporter']} {groups['page']}"

    pin = getattr(md, "pin_cite", None)
    if pin:
        pin = re.sub(r"^(at|p{1,2}\.)\s*", "", str(pin).strip(), flags=re.IGNORECASE)

    location = Location(
        char_start=full_start,
        char_end=full_end,
        page=doc.page_for(full_start),
        in_footnote=doc.in_footnote(full_start),
    )

    raw_text = doc.text[full_start:full_end]

    # eyecite's plaintiff/defendant can be picked up from text outside this
    # citation. A party name that does not appear in the citation's own span
    # is not this case's party, and trusting it would mis-resolve `supra`
    # back-references to the wrong authority.
    plaintiff = _corroborate(getattr(md, "plaintiff", None), raw_text)
    defendant = _corroborate(getattr(md, "defendant", None), raw_text)
    # eyecite's name is used unless it shows the apostrophe-truncation
    # signature -- a token ending in an apostrophe, as in "Nat' Ass'". Reading
    # the name out of the span is more faithful when that happens and less
    # faithful otherwise, because the span may begin a word or two early.
    parsed_name = _case_name(plaintiff, defendant)
    span_name = None
    # Use the span-derived name when eyecite's is truncated at an apostrophe,
    # or when corroboration rejected a party and left only one side -- but
    # only if the span yields a complete caption. A span that began a word
    # late gives one party, which is worse than what it replaces.
    incomplete = not parsed_name or not _V_MARKER.search(parsed_name)
    if incomplete or _TRUNCATED_APOSTROPHE.search(parsed_name or ""):
        candidate = case_name_from_span(
            raw_text, groups.get("volume"), groups.get("reporter")
        )
        if candidate and _V_MARKER.search(candidate):
            span_name = candidate

    return RawCitation(
        kind=kind,
        raw_text=raw_text,
        location=location,
        volume=groups.get("volume"),
        reporter=groups.get("reporter"),
        page=groups.get("page"),
        normalized=normalized,
        pin_cite=pin,
        plaintiff=plaintiff,
        defendant=defendant,
        case_name=span_name or parsed_name,
        court=_corroborated_court(cite, raw_text),
        year=_corroborated_year(cite, raw_text),
        antecedent_guess=getattr(md, "antecedent_guess", None),
        title=groups.get("title"),
        section=groups.get("section"),
        sentence=_sentence_around(doc.text, full_start, full_end),
        signal=_signal_before(doc.text, full_start),
    )


# --- pass 2: shadow scan ------------------------------------------------------

#: <volume> <Reporter-ish> <page>. Deliberately permissive; the guards below
#: do the discriminating.
#: <volume> <Reporter-ish> <page>. Deliberately permissive; the guards below
#: do the discriminating.
#:
#: Digits belong in the reporter token. A fabricated citation most often names
#: a reporter *series* that does not exist -- "982 N.E.4th 701", "475 F.6th
#: 972" -- and every one of the 31 invented citations in the LePhantomCite
#: eval split is of that shape. Excluding digits from the first token matched
#: the multi-word forms ("Cal. Rptr. 4th") and silently skipped the single
#: token ones, which is most of them.
_SHADOW_RE = re.compile(
    r"\b(?P<volume>\d{1,4})\s+"
    r"(?P<reporter>[A-Z][A-Za-z0-9'’&.\-]*"
    r"(?:\s+(?:[A-Z0-9][A-Za-z0-9'’&.\-]*|of|the|and)){0,3})\s+"
    r"(?P<page>\d{1,5})\b"
)
_YEAR_PAREN_RE = re.compile(r"^[\s,]*\(?[^()]{0,40}?(1[6-9]\d\d|20\d\d)\)")
_CASE_MARKER_RE = re.compile(r"\b(?:v\.|vs\.|In re|Ex parte|Matter of|Estate of)\s")
#: Tokens that may appear inside a party name without ending it.
_NAME_CONNECTORS = {
    "of", "the", "and", "for", "in", "on", "to", "at", "re", "ex", "rel.",
    "rel", "de", "el", "la", "du", "von", "van", "der", "den",
    "van", "von", "d/b/a", "a/k/a", "et", "al.", "&",
}


def _shadow_signals(text: str, match: re.Match) -> tuple[bool, bool, re.Match | None, re.Match | None]:
    """Corroborating structure around a candidate span.

    Returns (comma_preceded, has_case_marker, nearest marker match, year match).
    A candidate is accepted on two of the three signals; one alone is prose.
    """
    preceding = text[max(0, match.start() - 90) : match.start()]
    comma_preceded = preceding.rstrip().endswith((",", ";"))

    marker = None
    for candidate in _CASE_MARKER_RE.finditer(preceding):
        marker = candidate  # keep the last (nearest) marker

    trailing = text[match.end() : match.end() + 60]
    year = _YEAR_PAREN_RE.match(trailing)
    return comma_preceded, marker is not None, marker, year


#: Party-name words that end in a period without ending a sentence. Without
#: this, "Bell Atlantic Corp. v. Twombly" loses "Bell Atlantic" because
#: "Corp." looks exactly like the end of a sentence.
#:
#: Generated from reporters-db rather than maintained by hand: it ships
#: party-name abbreviations as CASE_NAME_ABBREVIATIONS (189 entries) and
#: STATE_ABBREVIATIONS (50). A hand-written list covered 18 of them, and the
#: gaps -- Bhd., Auth., Cmty., Cnty., Envtl., Fed'n, Sec'y, Gov't -- were
#: silently truncating institutional party names.
def _name_abbreviations() -> frozenset[str]:
    from reporters_db import CASE_NAME_ABBREVIATIONS, STATE_ABBREVIATIONS

    words = set()
    for table in (CASE_NAME_ABBREVIATIONS, STATE_ABBREVIATIONS):
        for abbreviation in table:
            core = abbreviation.rstrip(".").strip().lower()
            for form in {core, core.replace("'", "").replace("\u2019", "")}:
                if form:
                    words.add(form)
                    words.add(form + "s")
    words |= {
        "v", "vs", "no", "nos", "jr", "sr", "st", "mt", "ft", "ex", "rel",
        "et", "al", "us", "usa", "ala", "ariz", "ark", "cal", "colo", "conn",
        "del", "fla", "ga", "ill", "ind", "kan", "ky", "la", "md", "mass",
        "mich", "minn", "miss", "mo", "mont", "neb", "nev", "okla", "ore",
        "pa", "tenn", "tex", "va", "vt", "wash", "wis", "wyo",
    }
    return frozenset(words)


_NAME_ABBREVIATIONS = _name_abbreviations()


def _ends_a_sentence(word: str) -> bool:
    """Does this period actually terminate a sentence, or abbreviate a name?"""
    if not word.endswith((".", "!", "?")):
        return False
    core = word.rstrip(".!?").strip("(),;\u201c\u201d\"")
    if not core:
        return False
    if core.lower() in _NAME_ABBREVIATIONS:
        return False
    if len(core) <= 2:          # initials: "J.", "A.", and "v."
        return False
    if core.isupper():          # acronyms: "EPA.", "NLRB."
        return False
    if "." in core:             # internal periods: "U.S.C.", "S.D.N.Y."
        return False
    if "'" in core or "\u2019" in core:
        # Contracted abbreviations are pervasive in party names and no finite
        # list covers them: Nat'l, Ass'n, Comm'r, Dep't, Eng'g, Sec'y.
        return False
    return True


def _case_name_start(text: str, marker_abs_start: int, floor: int) -> int:
    """Walk backwards over the party name preceding a ``v.``-style marker."""
    cursor = marker_abs_start
    words = 0
    # Party names are usually short, but not always: "In re Application of the
    # United States for Historical Cell Site Data" is fourteen words, and
    # stopping early captured only "Cell Site Data", which then matched nothing.
    while cursor > floor and words < 18:
        words += 1
        chunk_start = cursor
        while chunk_start > floor and text[chunk_start - 1] == " ":
            chunk_start -= 1
        word_end = chunk_start
        while chunk_start > floor and text[chunk_start - 1] not in " \n\t":
            chunk_start -= 1
        word = text[chunk_start:word_end]
        if not word:
            break
        stripped = word.strip("(),;\u201c\u201d\"")
        if stripped and (stripped[0].isupper() or stripped.lower() in _NAME_CONNECTORS):
            # A word ending a prior sentence ("...held. Bogus") stops the walk,
            # but "Corp." and "Inc." must not.
            if _ends_a_sentence(word) and stripped.lower() not in _NAME_CONNECTORS:
                break
            cursor = chunk_start
            continue
        break
    return cursor


def _shadow_scan(
    doc: Document, claimed: list[tuple[int, int]]
) -> tuple[list[RawCitation], list[str]]:
    """Recover citation-shaped spans eyecite did not claim.

    Returns (unrecognized-reporter citations, unparsed citation-shaped spans).
    Candidates whose reporter *is* in reporters-db are not emitted as entries,
    since eyecite usually skipped them as part of a larger match -- but they
    are reported so the omission is visible rather than silent.
    """
    text = doc.text
    found: list[RawCitation] = []
    unparsed: list[str] = []

    for match in _SHADOW_RE.finditer(text):
        start, end = match.span()
        if any(cs < end and start < ce for cs, ce in claimed):
            continue

        comma, has_marker, marker, year = _shadow_signals(text, match)
        if sum((comma, has_marker, year is not None)) < 2:
            continue

        reporter = re.sub(r"\s+", " ", match.group("reporter")).strip(" ,;:") or ""
        if not reporter:
            continue
        if reporter_is_known(reporter):
            # A real reporter in a citation-shaped span that eyecite did not
            # claim. Usually it is part of a larger match already captured, but
            # it may be a citation that failed to parse -- which means it went
            # unchecked, and that is material.
            unparsed.append(text[start:end].strip())
            continue

        if is_non_case_source(reporter):
            # A law review or statutory compilation written out in full.
            # Out of scope, emphatically not a fabricated reporter.
            kind = CiteKind.JOURNAL
        else:
            kind = CiteKind.UNRECOGNIZED

        span_end = end + (year.end() if year else 0)
        span_start = start
        if marker is not None:
            floor = max(0, start - 90)
            marker_abs = floor + marker.start()
            span_start = _case_name_start(text, marker_abs, floor)

        found.append(
            RawCitation(
                kind=kind,
                raw_text=text[span_start:span_end].strip(),
                location=Location(
                    char_start=span_start,
                    char_end=span_end,
                    page=doc.page_for(span_start),
                    in_footnote=doc.in_footnote(span_start),
                ),
                volume=match.group("volume"),
                reporter=reporter,
                page=match.group("page"),
                normalized=f"{match.group('volume')} {reporter} {match.group('page')}",
                year=year.group(1) if year else None,
                sentence=_sentence_around(text, span_start, span_end),
                signal=_signal_before(text, span_start),
            )
        )

    return found, unparsed


# --- entry point --------------------------------------------------------------


def extract(doc: Document) -> tuple[list[RawCitation], list[str]]:
    """Return (citations in document order, informational notes)."""
    buffer = io.StringIO()
    # eyecite emits tokenizer diagnostics through the logging module, which
    # redirect_stderr does not intercept.
    eyecite_log = logging.getLogger("eyecite")
    previous_level = eyecite_log.level
    eyecite_log.setLevel(logging.ERROR)
    # eyecite writes tokenizer overlap diagnostics to stderr; keep them out of
    # the CLI's own output but surface the count in the ledger.
    #
    # Deliberately NOT passing clean_steps. eyecite's cleaners rewrite the text
    # before tokenizing, so the spans they return index the *cleaned* string --
    # collapsing "\n\n" to " " shifts every subsequent offset by one, and the
    # drift accumulates down the document until raw_text is visibly truncated.
    # Offsets have to address doc.text exactly, because verdicts point at
    # locations in the user's document. ingest() already handles the
    # normalization that matters (NBSP, soft hyphens, CRLF).
    if not doc.text.strip():
        # eyecite raises on empty input. An empty document is a legitimate
        # thing to be handed -- a blank page, a scanned PDF whose text layer is
        # missing -- and must produce an empty result, not a crash.
        return [], ["document contains no extractable text"]

    clean, index_map = collapse_whitespace(doc.text)
    spans = quote_spans(doc.text)
    try:
        with redirect_stdout(buffer), redirect_stderr(buffer):
            cites = get_citations(clean)
    finally:
        eyecite_log.setLevel(previous_level)

    notes: list[str] = []
    chatter = buffer.getvalue().strip()
    if chatter:
        notes.append(f"eyecite emitted {len(chatter.splitlines())} tokenizer diagnostic(s)")

    out: list[RawCitation] = []
    prev_core_end = 0
    for index, cite in enumerate(cites):
        out.append(
            _from_eyecite(doc, cite, index, prev_core_end, clean, index_map, spans)
        )
        try:
            prev_core_end = max(prev_core_end, cite.span()[1])
        except Exception:  # pragma: no cover - defensive
            pass
    claimed = [(c.location.char_start, c.location.char_end) for c in out]

    shadow, unparsed = _shadow_scan(doc, claimed)
    notes.extend(f"unparsed citation-shaped span: {u}" for u in unparsed)

    out.extend(shadow)
    out.sort(key=lambda c: c.location.char_start)
    assign_quotes(out, spans, doc.text)
    return out, notes
