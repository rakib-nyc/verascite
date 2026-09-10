"""Case-name matching.

The hardest comparison in the tool, and the one that decides most of its
precision. A brief writes ``Messner Manor Assocs. v. Wis. Hous. & Econ. Dev.
Auth.``; CourtListener stores ``Messner Manor Associates v. Wisconsin Housing
and Economic Development Authority``. Those are the same case. Compared
literally they share a quarter of their tokens, and the tool called it a
different case.

Measured over the 952 name comparisons the LePhantomCite eval split produces,
of which 44 are genuine mismatches:

| matcher                          | false alarms | genuine caught |
|----------------------------------|-------------:|---------------:|
| literal token overlap            |          174 |          43/44 |
| + Table T6 / T10 abbreviations   |           95 |          43/44 |
| + ordered acronym expansion      |           38 |          43/44 |
| + containment and surname weight |           26 |          42/44 |

Four things do the work, and each was added because the failures demanded it:

**Abbreviation tables.** ``reporters-db`` ships party-name abbreviations as
``CASE_NAME_ABBREVIATIONS`` (189 entries) and Table T10 as
``STATE_ABBREVIATIONS`` (50). Both are openly licensed. Every surface form of a
word is mapped onto one canonical token, so ``Ass'n``, ``Assoc.``, ``Assocs.``
and ``Association`` compare equal.

**Ordered acronym expansion.** ``Wyoming v. USDA`` against ``Wyoming v. United
States Department of Agriculture``. The acronym is matched against the *ordered*
words of the other name, before noise removal -- dropping "of" first destroys
the initial sequence ``USDA`` has to match.

**Containment.** Briefs shorten long party lists and captions; the reporter
keeps them whole. One name being a subset of the other is a match, not a
partial one.

**Surname weighting.** CourtListener stores first names (``Ricardo Jalil v.
Avdel Corp.``) that briefs never use. Scoring the distinctive tokens as well as
all of them stops common short words from diluting the comparison.
"""

from __future__ import annotations

import difflib
import re
from functools import lru_cache
from typing import Optional

from reporters_db import CASE_NAME_ABBREVIATIONS, STATE_ABBREVIATIONS

#: Words carrying no identifying information.
NOISE_TOKENS = {
    "the", "of", "and", "a", "an", "et", "al", "etal", "dba", "aka", "fka",
    "in", "re", "ex", "parte", "matter", "estate", "rel", "on", "behalf",
    "v", "vs", "versus", "petitioner", "respondent", "appellant", "appellee",
    "plaintiff", "defendant", "individually", "its", "his", "her", "their",
}

#: Kept while matching an acronym, because they sit inside the phrase it
#: abbreviates: the "of" in "Department of Agriculture".
_INTERNAL = {"the", "of", "and", "a", "an", "in", "for", "on", "et", "al", "v", "vs"}

_PROCEDURAL_PREFIX = re.compile(
    r"^\s*(in\s+re(?:\s+the\s+matter\s+of)?|ex\s+parte|matter\s+of|estate\s+of|"
    r"in\s+the\s+matter\s+of)\s+",
    re.IGNORECASE,
)


def _key(word: str) -> str:
    return re.sub(r"[^a-z0-9]", "", word.lower())


def _build_canonical_map() -> dict[str, str]:
    """Every surface form of a word onto one canonical token."""
    canon: dict[str, str] = {}

    def link(*forms: str) -> None:
        keys = [_key(f) for f in forms if _key(f)]
        if not keys:
            return
        canonical = min(keys, key=len)
        for k in keys:
            canon[k] = canonical

    for abbrev, expansions in CASE_NAME_ABBREVIATIONS.items():
        forms = [abbrev, *expansions]
        stem = abbrev.rstrip(".")
        if not stem.endswith("s"):
            forms += [stem + "s.", stem + "s"]
        link(*forms)

    for abbrev, full in STATE_ABBREVIATIONS.items():
        link(abbrev, full)

    # Forms the tables do not carry.
    for group in (
        ("assocs", "assoc", "association", "associates"),
        ("regulatory", "reg", "regulation", "regulations"),
        ("us", "unitedstates", "usa", "unitedstatesofamerica"),
        ("commn", "commission", "comm", "commr", "commissioner"),
        ("intl", "international"), ("natl", "national"),
        ("mfrs", "mfr", "manufacturers", "manufacturing", "mfg"),
        ("dept", "department"), ("auth", "authority"),
        ("univ", "university"), ("indus", "industries", "industrial"),
        ("servs", "services", "service", "serv"),
    ):
        link(*group)
    return canon


CANONICAL = _build_canonical_map()


def canonical_token(word: str) -> str:
    return CANONICAL.get(_key(word), _key(word))


@lru_cache(maxsize=8192)
def _ordered_words_cached(name: str) -> tuple[str, ...]:
    return tuple(_ordered_words_uncached(name))


def _ordered_words(name: str) -> list[str]:
    return list(_ordered_words_cached(name or ""))


def _ordered_words_uncached(name: str) -> list[str]:
    text = _PROCEDURAL_PREFIX.sub("", name or "")
    text = re.sub(r"\b(?:d/b/a|a/k/a|f/k/a|o/b/o)\b", " ", text, flags=re.IGNORECASE)
    text = text.replace("&", " and ").replace("’", "'")
    text = re.sub(r"\bu\.?\s*s\.?\s*a?\.?(?=\s|$)", " unitedstates ", text, flags=re.IGNORECASE)
    text = re.sub(r"\bunited\s+states(\s+of\s+america)?\b", " unitedstates ", text,
                  flags=re.IGNORECASE)
    return [w for w in re.split(r"[^A-Za-z0-9']+", text) if w]


@lru_cache(maxsize=8192)
def party_tokens(name: Optional[str]) -> frozenset[str]:
    """Identifying tokens of a case name, canonicalised."""
    tokens = set()
    for word in _ordered_words(name or ""):
        lowered = word.lower().strip("'")
        if not lowered or lowered in NOISE_TOKENS:
            continue
        token = canonical_token(lowered)
        if token and token not in NOISE_TOKENS:
            tokens.add(token)
    return frozenset(tokens)


@lru_cache(maxsize=8192)
def _expand_against(name: str, other: str) -> frozenset[str]:
    """Party tokens with acronyms replaced by the words they stand for."""
    mine = [w.lower() for w in _ordered_words(name)]
    theirs = [w.lower() for w in _ordered_words(other)]
    resolved: set[str] = set()
    consumed: set[str] = set()

    for word in mine:
        if not (2 <= len(word) <= 6 and word.isalpha()):
            continue
        letters = list(word)
        for start in range(len(theirs)):
            window: list[str] = []
            cursor = start
            while cursor < len(theirs) and len(window) < len(letters):
                candidate = theirs[cursor]
                if candidate in _INTERNAL and window:
                    cursor += 1
                    continue
                window.append(candidate)
                cursor += 1
            if len(window) == len(letters) and all(
                w.startswith(c) for w, c in zip(window, letters)
            ):
                resolved.update(window)
                consumed.add(canonical_token(word))
                break

    tokens = {t for t in party_tokens(name) if t not in consumed}
    tokens.update(
        canonical_token(w) for w in resolved if w.lower() not in NOISE_TOKENS
    )
    return frozenset(t for t in tokens if t)


#: Two long tokens this similar are the same word misspelled: "Ziglar"/"Zigler".
_SPELLING_RATIO = 0.82
_MIN_FUZZY_LEN = 5


def _overlap(left: frozenset[str], right: frozenset[str]) -> float:
    if not left or not right:
        return 0.0
    matched = len(left & right)
    remaining_left = sorted(left - right)
    remaining_right = sorted(right - left)
    for token in remaining_left:
        if len(token) < _MIN_FUZZY_LEN:
            continue
        for other in remaining_right:
            if len(other) < _MIN_FUZZY_LEN or abs(len(token) - len(other)) > 2:
                continue
            if difflib.SequenceMatcher(None, token, other).ratio() >= _SPELLING_RATIO:
                matched += 1
                remaining_right.remove(other)
                break
    return min(matched / min(len(left), len(right)), 1.0)


#: Party names that identify a *side* of a case without identifying the case.
#: "State v. Ing" and "State v. Pune" share a party and are different cases;
#: so do "Commonwealth v. Burton" and "Com v. Reid". Criminal and government
#: captions are an enormous share of American case law, so a matcher that
#: treats a shared generic party as a match will confirm a fabricated case
#: name against whatever unrelated decision happens to sit at that page.
#:
#: Found by external validation, not by the benchmark: the benchmark's
#: citations carry years and courts, which mask the failure. See
#: evals/names/V9_PROTOCOL.md.
GENERIC_PARTIES = frozenset({
    "state", "states", "people", "commonwealth", "united", "us", "usa",
    "government", "republic", "city", "county", "town", "township", "village",
    "borough", "board", "commission", "committee", "department", "dept",
    "director", "commissioner", "secretary", "administrator", "warden",
    "sheriff", "attorney", "general", "district", "division", "bureau",
    "agency", "authority", "office", "service", "services",
    "doe", "roe", "unknown", "minor", "juvenile",
    # Abbreviated captions. CourtListener's `case_name_short` is frequently a
    # bare party abbreviation -- "Com.", "State", "In re" -- which is contained
    # in thousands of unrelated captions and identifies none of them.
    "com", "comm", "commw", "cmwlth", "cwlth", "st", "people's",
})

#: Ceiling applied when two names each name a specific party and the parties
#: are different. Below NAME_REVIEW_THRESHOLD, so it reads as an affirmative
#: mismatch rather than as a borderline one -- because it is one.
GENERIC_ONLY_CEILING = 0.30


def _distinctive(tokens: frozenset[str]) -> frozenset[str]:
    """Tokens that identify *this* case rather than a side of many cases."""
    return frozenset(t for t in tokens if t not in GENERIC_PARTIES)


def _shared_tokens(left: frozenset[str], right: frozenset[str]) -> frozenset[str]:
    """Tokens the two names have in common, allowing for misspelling.

    Fuzzy, because refusing a match on "Ziglar"/"Zigler" would turn a spelling
    difference into an accusation -- the error this whole module exists to
    avoid.
    """
    shared = set(left & right)
    for token in left - right:
        if len(token) < _MIN_FUZZY_LEN:
            continue
        for other in right - left:
            if len(other) < _MIN_FUZZY_LEN or abs(len(token) - len(other)) > 2:
                continue
            if difflib.SequenceMatcher(None, token, other).ratio() >= _SPELLING_RATIO:
                shared.add(token)
                break
    return frozenset(shared)


@lru_cache(maxsize=8192)
def name_similarity(asserted: Optional[str], actual: Optional[str]) -> float:
    """How much two case names agree, from 0 to 1.

    Cached: a brief cites the same authority repeatedly, and the metadata
    stage compares each citation against three CourtListener name fields, so
    the same pair recurs many times per document.
    """
    if not asserted or not actual:
        return 0.0
    left = _expand_against(asserted, actual)
    right = _expand_against(actual, asserted)
    if not left or not right:
        return 0.0

    if left == right:
        return 1.0

    # The rule that external validation forced. Whatever these two names have
    # in common, if none of it identifies a *case* -- if it is all "State",
    # "People", "Com." -- then they agree on caption boilerplate and nothing
    # else. "State v. Ing" reduces to {state}, which is contained in
    # {state, pune}, so containment alone would have every criminal caption in
    # the reporter confirm every other. Measured on real sanctioned filings:
    # see evals/names/V9_PROTOCOL.md.
    if not _distinctive(_shared_tokens(left, right)):
        return min(
            GENERIC_ONLY_CEILING,
            max(_overlap(left, right), _overlap(_distinctive(left), _distinctive(right))),
        )

    if left <= right or right <= left:
        return 1.0
    distinctive_left = frozenset(t for t in left if len(t) >= 4) or left
    distinctive_right = frozenset(t for t in right if len(t) >= 4) or right
    return max(_overlap(left, right), _overlap(distinctive_left, distinctive_right))
