"""Stage 4c: does the quoted language actually appear in the cited opinion?

Quotation marks promise exact words. This module checks that promise with a
deterministic, legal-citation-aware string comparison -- no model involved.

The hard case is not the fabricated quote, which is easy. It is the quote that
is *almost* right. The LePhantomCite benchmark builds its misquote cases by
swapping one or two words for synonyms, precisely because that preserves
meaning and so defeats semantic comparison. A high string similarity that is
not an exact match is the signature of a fabricated-but-plausible quote, so
near-misses are reported as FAIL with a word-level diff rather than waved
through.

Normalization therefore has to be generous in exactly the ways conventional
permits -- "[T]he", "[sic]", ellipses, bracketed substitutions, internal
citations -- and strict everywhere else.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Optional

from .clients.opinions import OpinionText
from .models import LedgerEntry
from .verify_pincite import verify_pincite
from .verdicts import CheckResult, Verdict

QUOTE_SOURCE = "courtlistener_opinion"

from .config import (
    QUOTE_MATERIAL_ALTERATION_FLOOR as MATERIAL_ALTERATION_FLOOR,
    QUOTE_MIN_CHARS as MIN_QUOTE_CHARS,
    QUOTE_NEAR_MISS_FLOOR as NEAR_MISS_FLOOR,
)

#: Wildcard sentinel standing in for an ellipsis. Chosen to be a string that
#: cannot occur in an opinion.
ELLIPSIS = " ␟ "

LEFT_DQUOTE = "“"
RIGHT_DQUOTE = "”"

_PUNCTUATION_FOLD = {
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "–": "-", "—": "-", "―": "-", "−": "-",
    " ": " ", " ": " ", " ": " ", " ": " ",
}

_BRACKET_ANNOTATIONS = re.compile(
    r"\[\s*(?:sic|emphasis (?:added|in original|omitted)|alteration[s]? in original|"
    r"internal (?:quotation marks|citations?) omitted|citations? omitted|"
    r"quoting[^\]]*|footnote[s]? omitted|brackets in original)\s*\]",
    re.IGNORECASE,
)
#: Bracketed case alteration: "[T]he" -> "the".
_BRACKET_LETTER = re.compile(r"\[([A-Za-z])\]")
#: Bracketed substitution keeps its content: "[to] the" -> "to the".
_BRACKET_WORDS = re.compile(r"\[([^\]]{1,40})\]")
_ELLIPSIS_RE = re.compile(r"(?:\.\s?){3,}|…")
#: Citations embedded mid-sentence in an opinion's own prose.
_INLINE_CITE = re.compile(
    r",?\s*\d{1,4}\s+[A-Z][A-Za-z.'’ ]{1,20}\s+\d{1,5}(?:,\s*\d{1,5})?"
    r"(?:\s*\([^)]{0,40}\d{4}\))?"
)
_SOFT_HYPHEN = re.compile("­")
_LINEBREAK_HYPHEN = re.compile(r"(\w)-\s*\n\s*(\w)")


def normalize_quote(text: str, drop_inline_citations: bool = False) -> str:
    """Canonical comparison form. Applied identically to both sides."""
    text = unicodedata.normalize("NFC", text)
    text = _SOFT_HYPHEN.sub("", text)
    text = _LINEBREAK_HYPHEN.sub(r"\1\2", text)
    for source, target in _PUNCTUATION_FOLD.items():
        text = text.replace(source, target)
    text = _BRACKET_ANNOTATIONS.sub(" ", text)
    text = _ELLIPSIS_RE.sub(ELLIPSIS, text)
    text = _BRACKET_LETTER.sub(r"\1", text)
    text = _BRACKET_WORDS.sub(r"\1", text)
    if drop_inline_citations:
        text = _INLINE_CITE.sub(" ", text)
    # Apostrophes are DELETED, not spaced. Replacing them with a space turns
    # "plaintiff's" into "plaintiff s", which then fails to match an opinion
    # reading "plaintiffs" and is reported as an altered quotation. OCR'd brief
    # text and clean opinion text disagree about possessives constantly, and
    # that disagreement is typographic, not a misquote.
    text = text.replace("'", "")
    text = re.sub(r'"', " ", text)
    text = re.sub(r"\s+", " ", text)
    # Terminal punctuation is the quoting author's, not the court's. A brief
    # ending a quotation with a period where the opinion ran on with a comma
    # ("...will not do," in Twombly) is correct citation practice, not a
    # misquote, so trailing sentence punctuation is dropped from both sides.
    return text.strip().strip(".,;:").strip().lower()


def _words(text: str) -> list[str]:
    return [w for w in re.split(r"[^\w]+", text) if w]


@dataclass
class QuoteFinding:
    """Outcome of checking one quoted passage against one cluster."""

    quote: str
    found: bool = False
    exact: bool = False
    similarity: float = 0.0
    opinion_id: Optional[int] = None
    opinion_type: str = ""
    is_court_holding: bool = True
    author: str = ""
    page: Optional[str] = None
    within_source_quotation: bool = False
    matched_text: str = ""
    diff: str = ""
    #: "typographic" | "omission" | "substitution", set during classification.
    difference: str = ""
    normalized_quote: str = ""
    diff_window: str = ""
    searched_opinions: int = 0
    searched_versions: int = 0
    pagination_available: bool = False
    is_syllabus: bool = False
    descriptor: str = ""
    #: Was any searched version demonstrably the complete opinion?
    searched_complete_text: bool = False
    incompleteness_reason: str = ""

    def to_dict(self) -> dict:
        keep = ("found", "exact", "similarity", "quote")
        return {
            k: v
            for k, v in self.__dict__.items()
            if k in keep or v not in (None, "", False, 0, 0.0)
        }


def _best_window(haystack: str, needle: str) -> tuple[float, int, int]:
    """Best-scoring region of `haystack` against `needle`.

    A full SequenceMatcher over a 100KB opinion is far too slow, so candidate
    regions are anchored on word n-grams drawn from the quote and only those
    windows are scored. A one-word synonym swap leaves most n-grams intact,
    which is exactly the case this has to catch.
    """
    needle_words = _words(needle)
    if not needle_words:
        return 0.0, -1, -1

    span = max(len(needle) * 2, 200)
    seen: set[int] = set()
    best = (0.0, -1, -1)

    for size in (6, 4, 3, 2):
        if len(needle_words) < size:
            continue
        anchors = [
            " ".join(needle_words[i : i + size])
            for i in range(0, max(1, len(needle_words) - size + 1))
        ]
        for anchor in anchors[:40]:
            start = 0
            while True:
                position = haystack.find(anchor, start)
                if position == -1:
                    break
                start = position + 1
                bucket = position // 64
                if bucket in seen:
                    continue
                seen.add(bucket)
                left = max(0, position - span // 2)
                window = haystack[left : left + span + len(needle)]
                matcher = difflib.SequenceMatcher(None, window, needle, autojunk=False)
                blocks = [b for b in matcher.get_matching_blocks() if b.size > 0]
                if not blocks:
                    continue
                # Score the aligned region, not the whole search window. Scoring
                # the window dilutes the ratio by however much surrounding text
                # was pulled in -- a one-word synonym swap scored 0.41 against a
                # 271-character window and 0.90 against its own aligned span,
                # and only the second number means anything.
                # Align on the largest matching block rather than the outermost
                # ones. A small spurious match far downstream would otherwise
                # stretch the candidate across unrelated text and put the
                # opinion's own inline citations into the diff.
                largest = max(blocks, key=lambda b: b.size)
                begin = max(0, left + largest.a - largest.b)
                finish = min(len(haystack), begin + len(needle))
                candidate = haystack[begin:finish]
                ratio = difflib.SequenceMatcher(
                    None, candidate, needle, autojunk=False
                ).ratio()
                if ratio > best[0]:
                    best = (ratio, begin, finish)
        if best[0] >= 0.98:
            break
    return best


def _snap_to_words(text: str, start: int, end: int) -> tuple[int, int]:
    while start > 0 and text[start - 1].isalnum():
        start -= 1
    while end < len(text) and text[end].isalnum():
        end += 1
    return start, end


def _refine(haystack: str, needle: str, begin: int) -> tuple[float, int, int]:
    """Sharpen a coarse alignment before scoring and diffing.

    The anchor block rarely sits at the very start of the quote, so the coarse
    window can begin mid-word ("ormulaic") and run a few words past the end.
    Both depress the similarity score and make the diff hard to read, so a
    small local search picks the span that actually matches.
    """
    best = (0.0, begin, begin + len(needle))
    for offset in (-8, -6, -4, -2, 0, 2, 4, 6, 8):
        for factor in (0.9, 1.0, 1.1, 1.25):
            start = max(0, begin + offset)
            end = min(len(haystack), start + int(len(needle) * factor))
            if end <= start:
                continue
            start, end = _snap_to_words(haystack, start, end)
            ratio = difflib.SequenceMatcher(
                None, haystack[start:end], needle, autojunk=False
            ).ratio()
            if ratio > best[0]:
                best = (ratio, start, end)
    return best


def _segments_in_order(haystack: str, needle: str) -> Optional[int]:
    """Ellipsis handling: every retained span must appear, in order."""
    parts = [p.strip().strip(".,;:").strip() for p in needle.split(ELLIPSIS.strip())]
    parts = [p for p in parts if p]
    if len(parts) < 2:
        return None
    cursor = 0
    first = None
    for part in parts:
        position = haystack.find(part, cursor)
        if position == -1:
            return None
        if first is None:
            first = position
        cursor = position + len(part)
    return first


def _inside_quotation(text: str, offset: int) -> bool:
    """Is this offset inside a quotation in the source opinion?

    Language that appears in an opinion only because that opinion was itself
    quoting another source does not belong to the citing court. A naive string
    search passes; this is what catches it.
    """
    prefix = text[max(0, offset - 4000) : offset]
    if LEFT_DQUOTE in prefix or RIGHT_DQUOTE in prefix:
        return prefix.count(LEFT_DQUOTE) > prefix.count(RIGHT_DQUOTE)
    return prefix.count('"') % 2 == 1


def check_quote(quote: str, opinions: Iterable[OpinionText]) -> QuoteFinding:
    """Locate one quoted passage across a cluster's sub-opinions."""
    finding = QuoteFinding(quote=quote)
    normalized_quote = normalize_quote(quote)
    finding.normalized_quote = normalized_quote
    if not normalized_quote:
        return finding

    candidates = list(opinions)
    finding.searched_versions = len(candidates)
    finding.searched_opinions = len({o.opinion_id for o in candidates})
    finding.pagination_available = any(o.has_pagination for o in candidates)
    # A quote is only "absent" if some version we searched was the whole
    # opinion. Otherwise absence is a fact about our copy, not the brief.
    complete = [o for o in candidates if o.complete or o.is_syllabus]
    finding.searched_complete_text = any(not o.is_syllabus and o.complete for o in candidates)
    if not finding.searched_complete_text:
        reasons = {o.completeness_reason for o in candidates if o.completeness_reason}
        finding.incompleteness_reason = "; ".join(sorted(reasons)[:2])

    best_partial: tuple[float, Optional[OpinionText], int, int] = (0.0, None, -1, -1)

    for opinion in candidates:
        for drop_citations in (False, True):
            haystack = normalize_quote(opinion.text, drop_inline_citations=drop_citations)
            position = haystack.find(normalized_quote)
            if position == -1:
                position = _segments_in_order(haystack, normalized_quote) or -1
            if position != -1:
                _record(finding, opinion, haystack, position, len(normalized_quote), 1.0)
                finding.exact = not drop_citations
                finding.found = True
                return finding

            ratio, begin, finish = _best_window(haystack, normalized_quote)
            if ratio > best_partial[0]:
                best_partial = (ratio, opinion, begin, finish)

    ratio, opinion, begin, finish = best_partial
    if opinion is not None and begin >= 0:
        haystack = normalize_quote(opinion.text)
        ratio, begin, finish = _refine(haystack, normalized_quote, begin)
        if ratio >= NEAR_MISS_FLOOR:
            _record(finding, opinion, haystack, begin, max(finish - begin, 1), ratio)
            finding.diff_window = _diff_window(haystack, begin, normalized_quote)
            finding.diff = _word_diff(finding.diff_window, normalized_quote)
    finding.similarity = round(ratio, 4)
    return finding


def _diff_window_for(finding: "QuoteFinding") -> str:
    return finding.diff_window or finding.matched_text


def _record(
    finding: QuoteFinding,
    opinion: OpinionText,
    haystack: str,
    position: int,
    length: int,
    ratio: float,
) -> None:
    finding.similarity = round(ratio, 4)
    finding.opinion_id = opinion.opinion_id
    finding.opinion_type = opinion.type_label
    finding.is_court_holding = opinion.is_court_holding
    finding.is_syllabus = opinion.is_syllabus
    finding.descriptor = opinion.descriptor
    finding.author = opinion.author
    finding.matched_text = haystack[position : position + length]
    # Normalization changes offsets; scale back to the source proportionally.
    source_offset = int(position / max(len(haystack), 1) * len(opinion.text))
    finding.page = opinion.page_for(source_offset)
    finding.within_source_quotation = _inside_quotation(opinion.text, source_offset)


def _diff_window(haystack: str, begin: int, needle: str) -> str:
    """Source text aligned to the quotation, for diffing.

    Two failure modes have to be avoided at once. Character alignment
    maximises similarity, so it drops the very word that was replaced when the
    replacement is longer -- the diff then reads "brief says 'suffice'"
    without saying what it replaced. But taking a fixed extra word overshoots
    the end of the quotation and reports text the brief never claimed: against
    Twombly's "...will not do. See Papasan v. Allain", that produced the diff
    "opinion says 'do see'", and there is no "do see" anywhere in Twombly.

    So the window takes exactly as many words as the quotation has, and never
    more. Whatever follows the quoted passage in the opinion is not part of
    what the brief asserted and must not appear in a finding against it.
    """
    wanted = len(_words(needle))
    if wanted <= 0:
        return ""
    collected: list[str] = []
    for match in re.finditer(r"\S+", haystack[begin:]):
        collected.append(match.group())
        if len(collected) >= wanted:
            break
    return " ".join(collected)


#: Function words whose omission or insertion rarely changes what a passage
#: means. OCR drops and doubles these constantly.
_MINOR_WORDS = {
    "a", "an", "the", "of", "to", "in", "on", "at", "by", "for", "with",
    "that", "which", "such", "any", "all", "is", "are", "was", "were", "be",
    "been", "as", "it", "its", "this", "these", "those", "s",
}


#: Whether "this quotation is not in the opinion" carries a finding.
#: Measured on LePhantomCite: it produced 45 false alarms. A quotation that
#: cannot be located may be fabricated, or attributed to the wrong citation,
#: or absent from the text this tool managed to retrieve.
_ABSENT_IS_A_FINDING = False


#: Two words this similar are the same word, mis-scanned or inflected.
_SAME_WORD_RATIO = 0.75


def _near_identical(left: list[str], right: list[str]) -> bool:
    """Is this replacement a scanning or inflection artefact, not a swap?"""
    if len(left) != len(right):
        return False
    for a, b in zip(left, right):
        if a == b:
            continue
        if min(len(a), len(b)) < 4:
            return False
        # Mis-scanned: "limiting" read as "hmiting".
        if difflib.SequenceMatcher(None, a, b).ratio() >= _SAME_WORD_RATIO:
            continue
        # Inflected: "acts"/"acted", "fails"/"failed". A shared stem with a
        # short tail is one word in two forms; "do"/"suffice" shares nothing.
        stem = 0
        for x, y in zip(a, b):
            if x != y:
                break
            stem += 1
        if stem >= 3 and max(len(a), len(b)) - stem <= 3:
            continue
        return False
    return True


def classify_difference(actual: str, asserted: str) -> str:
    """How does the brief's quotation differ from the opinion's words?

    Three outcomes, because they warrant three different verdicts:

    * ``typographic`` -- the same words in the same order. Punctuation,
      spacing, capitalisation, hyphenation. OCR-derived brief text and clean
      opinion text disagree about these constantly and it is not a misquote.
    * ``omission`` -- only function words added or dropped, no word replaced.
      Technically an unmarked alteration, but it does not change the meaning
      and calling it fabrication is crying wolf.
    * ``substitution`` -- a word has been replaced by a different word. This is
      the failure the benchmark is built around: one or two words swapped for
      synonyms, which preserves meaning and defeats semantic comparison. It is
      the one that gets reported as a misquote.
    """
    left, right = _words(actual), _words(asserted)
    if left == right:
        return "typographic"

    matcher = difflib.SequenceMatcher(None, left, right, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "replace":
            # A synonym swap replaces a word with a *different* word:
            # "do" -> "suffice", "recitation" -> "listing". Scanned reporter
            # text and OCR'd briefs instead produce words that are nearly the
            # same string -- "limiting" read as "hmiting", "acts" against
            # "acted". Both sides of this comparison are OCR output, and
            # treating their disagreements as misquotes accounted for most of
            # the false alarms this check produced.
            if _near_identical(left[i1:i2], right[j1:j2]):
                continue
            return "substitution"
        changed = left[i1:i2] if tag == "delete" else right[j1:j2]
        if any(w.lower() not in _MINOR_WORDS for w in changed):
            return "substitution"
    return "omission"


def _word_diff(actual: str, asserted: str) -> str:
    """Word-level diff, so a one-word swap is visible at a glance."""
    actual_words, asserted_words = _words(actual), _words(asserted)
    pieces: list[str] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
        None, actual_words, asserted_words, autojunk=False
    ).get_opcodes():
        if tag == "equal":
            continue
        source = " ".join(actual_words[i1:i2]) or "(nothing)"
        target = " ".join(asserted_words[j1:j2]) or "(nothing)"
        pieces.append(f"opinion says {source!r}, brief says {target!r}")
    return "; ".join(pieces[:6])


#: Parentheticals that identify a separate writing. Plurality opinions cite as
#: "(plurality opinion)" or "(opinion of Kennedy, J.)" and contain neither
#: "concurring" nor "dissenting", so matching on those two words alone misses
#: them entirely.
_SEPARATE_WRITING_SIGNAL = re.compile(
    r"dissent(?:ing|s)?\b|concurr(?:ing|ence)\b|plurality\b|per\s+curiam\b|"
    r"opinion\s+of\s+[A-Z]|in\s+the\s+judgment\b|in\s+part\b|"
    r"\b[A-Z][a-z]+,\s*(?:C\.\s*)?J\.|JJ\.",
    re.IGNORECASE,
)
#: Quoting the Reporter's syllabus as the Court's words.
_SYLLABUS_SIGNAL = re.compile(r"syllabus|headnote", re.IGNORECASE)


def verify_quote(entry: LedgerEntry, opinions: list[OpinionText]) -> None:
    """Set the ``quote`` and ``quote_source`` dimensions on one ledger entry."""
    quotes = [q for q in entry.citation.quoted_language if len(q.strip()) >= MIN_QUOTE_CHARS]

    if not quotes:
        entry.set_check("quote", CheckResult(Verdict.NA, reason="no quoted language attributed"))
        entry.set_check("quote_source", CheckResult(Verdict.NA))
        return

    if not opinions:
        entry.set_check(
            "quote",
            CheckResult(
                Verdict.NOT_CHECKABLE,
                reason=(
                    "no opinion text was retrieved for this authority, so the quoted "
                    "language could not be compared against the source"
                ),
                sources_consulted=[QUOTE_SOURCE],
            ),
        )
        entry.set_check(
            "quote_source",
            CheckResult(Verdict.NOT_CHECKABLE, reason="no opinion text was retrieved"),
        )
        return

    findings = [check_quote(q, opinions) for q in quotes]
    detail = {"quotes": [f.to_dict() for f in findings]}

    # Reclassify near-misses by *what* differs, not just how much.
    for finding in findings:
        if not finding.found and finding.similarity >= MATERIAL_ALTERATION_FLOOR:
            finding.difference = classify_difference(
                _diff_window_for(finding), finding.normalized_quote
            )
            if finding.difference == "typographic":
                finding.found = True
                finding.exact = False

    unmatched = [f for f in findings if not f.found and f.similarity < NEAR_MISS_FLOOR]
    altered = [
        f for f in findings
        if not f.found
        and f.similarity >= MATERIAL_ALTERATION_FLOOR
        and f.difference == "substitution"
    ]
    minor = [
        f for f in findings
        if not f.found
        and f.similarity >= MATERIAL_ALTERATION_FLOOR
        and f.difference == "omission"
    ]
    # Near-misses, plus close matches whose only difference is added or dropped
    # function words. Both are a human's call rather than a finding.
    weak = [
        f
        for f in findings
        if not f.found and NEAR_MISS_FLOOR <= f.similarity < MATERIAL_ALTERATION_FLOOR
    ] + minor
    ocr = any(o.extracted_by_ocr for o in opinions)

    if altered:
        worst = max(altered, key=lambda f: f.similarity)
        # Lead with the substitution. The percentage is a sort key, not the
        # finding -- a reader can act on "do -> suffice" and cannot calibrate
        # "94%".
        entry.set_check(
            "quote",
            CheckResult(
                Verdict.FAIL,
                evidence=(
                    f"altered quotation. {worst.diff or 'wording differs from the source'}. "
                    f"The passage is otherwise present in the {worst.descriptor or 'opinion'}, "
                    f"so this is a changed quote rather than a different one "
                    f"(similarity {min(worst.similarity, 0.999):.0%})."
                ),
                sources_consulted=[QUOTE_SOURCE],
                detail=detail,
            ),
        )
    elif unmatched and ocr:
        entry.set_check(
            "quote",
            CheckResult(
                Verdict.NOT_CHECKABLE,
                reason=(
                    "the retrieved opinion text was produced by OCR, so a failed "
                    "match may be a transcription artifact rather than a misquote"
                ),
                sources_consulted=[QUOTE_SOURCE],
                detail=detail,
            ),
        )
    elif unmatched and not unmatched[0].searched_complete_text:
        # Absence from a fragment is not a misquote (P7).
        entry.set_check(
            "quote",
            CheckResult(
                Verdict.NOT_CHECKABLE,
                reason=(
                    "the quoted language was not found, but the retrieved text "
                    "cannot be shown to be the complete opinion: "
                    f"{unmatched[0].incompleteness_reason or 'completeness unknown'}. "
                    "Verify this quotation against the published report."
                ),
                sources_consulted=[QUOTE_SOURCE],
                detail=detail,
            ),
        )
    elif unmatched and _ABSENT_IS_A_FINDING:
        worst = unmatched[0]
        entry.set_check(
            "quote",
            CheckResult(
                Verdict.FAIL,
                evidence=(
                    f"quoted language does not appear in the cited opinion: "
                    f"{' '.join(worst.quote.split())[:160]!r}. Searched "
                    f"{worst.searched_versions} text "
                    f"version(s) of {worst.searched_opinions} sub-opinion(s), at least "
                    f"one of them the complete opinion; closest match "
                    f"{max(f.similarity for f in unmatched):.0%}."
                ),
                sources_consulted=[QUOTE_SOURCE],
                detail=detail,
            ),
        )
    elif unmatched:
        entry.set_check(
            "quote",
            CheckResult(
                Verdict.NOT_CHECKABLE,
                reason=(
                    f"the quoted language was not found in the retrieved text "
                    f"(closest match {max(f.similarity for f in unmatched):.0%}). "
                    "It may be misquoted, or attributed here in error, or absent "
                    "from the text available. Compare it against the opinion."
                ),
                sources_consulted=[QUOTE_SOURCE],
                detail=detail,
            ),
        )
    elif weak:
        entry.set_check(
            "quote",
            CheckResult(
                Verdict.NOT_CHECKABLE,
                reason=(
                    f"quoted language differs from the opinion. "
                    + (
                        "Words were added or dropped without marking the "
                        "alteration, though none was replaced. "
                        if any(f.difference == "omission" for f in weak)
                        else ""
                    )
                    + f"Closest match {max(f.similarity for f in weak):.1%}. "
                    "Compare by hand."
                ),
                sources_consulted=[QUOTE_SOURCE],
                detail=detail,
            ),
        )
    else:
        located = [f for f in findings if f.found]
        pages = sorted({f.page for f in located if f.page})
        evidence = (
            f"{len(located)} quoted passage(s) found verbatim in the cited opinion"
        )
        if pages:
            evidence += f", at page {', '.join(pages)}"
        elif not any(f.pagination_available for f in located):
            evidence += (
                "; the page could not be identified because the retrieved text "
                "carries no star pagination"
            )
        if not all(f.exact for f in located):
            evidence += (
                " (matched after removing citations embedded in the opinion text)"
            )
        entry.set_check(
            "quote",
            CheckResult(
                Verdict.PASS,
                evidence=evidence + _pincite_note(entry, pages),
                sources_consulted=[QUOTE_SOURCE],
                detail=detail,
            ),
        )

    _check_quote_source(entry, findings)

    # The page a quotation was located on is exactly what the pincite check
    # needs, and it is already computed here.
    located = [f for f in findings if f.found and f.page]
    verify_pincite(entry, opinions, located[0].page if located else None)


def _pincite_note(entry: LedgerEntry, pages: list[str]) -> str:
    """Partial credit toward the pincite check (spec 7.1, step 4).

    The full pincite verdict needs more than this, but where a quotation was
    located on a star page and the citation carries a pincite, saying whether
    the two agree is free and genuinely useful.
    """
    pincite = (entry.citation.pin_cite or "").strip()
    if not pincite or not pages:
        return ""
    first = re.match(r"\d+", pincite)
    if not first:
        return ""
    if first.group() in pages:
        return f". This is consistent with the pincite {pincite}."
    return (
        f". Note the citation pincites {pincite}, but the quoted language was "
        f"found at page {', '.join(pages)}."
    )


def _check_quote_source(entry: LedgerEntry, findings: list[QuoteFinding]) -> None:
    """Is the language the court's own holding, and is it the court's words?"""
    located = [f for f in findings if f.found]
    if not located:
        entry.set_check(
            "quote_source",
            CheckResult(Verdict.NA, reason="no quoted passage was located to attribute"),
        )
        return

    syllabus = [f for f in located if f.is_syllabus]
    if syllabus and not any(f.is_court_holding for f in located):
        signalled = bool(_SYLLABUS_SIGNAL.search(entry.citation.sentence or ""))
        entry.set_check(
            "quote_source",
            CheckResult(
                Verdict.PASS if signalled else Verdict.FAIL,
                evidence=(
                    "language is from the syllabus and the citation says so"
                    if signalled
                    else (
                        "the quoted language is from the syllabus, which is prepared "
                        "by the Reporter of Decisions and is not part of the opinion "
                        "of the court. See United States v. Detroit Timber & Lumber "
                        "Co., 200 U.S. 321, 337 (1906). It has no precedential weight."
                    )
                ),
                sources_consulted=[QUOTE_SOURCE],
            ),
        )
        return

    separate = [f for f in located if not f.is_court_holding and not f.is_syllabus]
    plurality = [f for f in separate if f.opinion_type.startswith("plurality")]
    if plurality and not any(f.is_court_holding for f in located):
        # A plurality commanded no majority, but under Marks v. United States,
        # 430 U.S. 188, 193 (1977), the position taken on the narrowest grounds
        # may still be controlling. Telling a lawyer this citation is wrong
        # would be a REVIEW-flavoured observation dressed as a finding.
        finding = plurality[0]
        signalled = bool(_SEPARATE_WRITING_SIGNAL.search(entry.citation.raw_text))
        entry.set_check(
            "quote_source",
            CheckResult(
                Verdict.PASS if signalled else Verdict.NOT_CHECKABLE,
                evidence=(
                    "language is from the plurality opinion and the citation "
                    "identifies it as such"
                )
                if signalled
                else None,
                reason=None
                if signalled
                else (
                    "the quoted language is from a plurality opinion, which did not "
                    "command a majority, and the citation does not say so. Whether "
                    "it is controlling depends on a narrowest-grounds analysis under "
                    "Marks v. United States, 430 U.S. 188, 193 (1977). Consider "
                    "identifying it as a plurality."
                ),
                sources_consulted=[QUOTE_SOURCE],
            ),
        )
        return

    if separate and not any(f.is_court_holding for f in located):
        finding = separate[0]
        attribution = f" by {finding.author}" if finding.author else ""
        if _SEPARATE_WRITING_SIGNAL.search(entry.citation.raw_text):
            entry.set_check(
                "quote_source",
                CheckResult(
                    Verdict.PASS,
                    evidence=(
                        f"language is from the {finding.opinion_type}{attribution}, and "
                        "the citation identifies it as a separate writing"
                    ),
                    sources_consulted=[QUOTE_SOURCE],
                ),
            )
        else:
            entry.set_check(
                "quote_source",
                CheckResult(
                    Verdict.FAIL,
                    evidence=(
                        f"the quoted language appears in the {finding.opinion_type}"
                        f"{attribution}, not in the opinion of the court, and the "
                        "citation gives no signal that it is a separate writing. It "
                        "does not carry the weight the citation claims for it."
                    ),
                    sources_consulted=[QUOTE_SOURCE],
                ),
            )
        return

    if any(f.within_source_quotation for f in located):
        entry.set_check(
            "quote_source",
            CheckResult(
                Verdict.NOT_CHECKABLE,
                reason=(
                    "the quoted language appears in the cited opinion only inside a "
                    "quotation -- the cited court was itself quoting another source. "
                    "Attributing these words to the cited court may be an error of "
                    "authority; check what it was quoting."
                ),
                sources_consulted=[QUOTE_SOURCE],
            ),
        )
        return

    entry.set_check(
        "quote_source",
        CheckResult(
            Verdict.PASS,
            evidence="quoted language is the cited court's own, in the opinion of the court",
            sources_consulted=[QUOTE_SOURCE],
        ),
    )
