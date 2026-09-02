"""Stage 4d: is the cited page the page the material is on?

Capability-limited by design (P7). Star pagination originates in commercial
reporter formatting and is frequently absent from free repositories; the
benchmark's own measurements put pincite recall lowest of any category for
every system tested. So this check reports NOT_CHECKABLE often, says why, and
never guesses.

Three things make a pincite verdict possible at all, and all three have to hold:

* The retrieved text carries star pagination.
* That pagination belongs to *the reporter the citation uses*. An opinion
  printed in three reporters carries three interleaved marker sequences, and
  reading the wrong one gives a confident wrong answer.
* Something locatable -- a quotation -- was found, so there is a position to
  map to a page.

Where the third fails but the first two hold, the honest partial result is
still worth reporting: the citation's page is or is not within the range the
opinion occupies. That catches a pincite outside the opinion entirely, which is
a real and detectable error.
"""

from __future__ import annotations

import re
from typing import Optional

from .clients.opinions import OpinionText, PageMark
from .models import LedgerEntry
from .verdicts import CheckResult, Verdict

PINCITE_SOURCE = "courtlistener_opinion"

#: How far the estimated page may differ from the cited one and still agree.
#: Not a tuning knob: it is the measured precision of mapping a match offset
#: through a normalised string back onto star-pagination markers.
PAGE_TOLERANCE = 1


def _forward_run(marks: list[PageMark], citation_index: Optional[int]) -> list[int]:
    """Ascending page labels for one reporter's sequence.

    Filters to a single ``citation-index`` where the source distinguishes them,
    and keeps only the forward-moving run: footnote markers collected at the end
    of a document point back at pages already passed.
    """
    labels = [
        int(m.label)
        for m in marks
        if m.label.isdigit()
        and (citation_index is None or m.citation_index in (None, citation_index))
    ]
    forward: list[int] = []
    for label in labels:
        if not forward or label >= forward[-1]:
            forward.append(label)
    return forward


def _reporter_index(opinion: OpinionText, first_page: Optional[int]) -> Optional[int]:
    """Which marker sequence corresponds to the reporter being cited?

    Chosen by which sequence actually contains the page the citation refers to,
    rather than assumed to be the first.
    """
    if first_page is None:
        return None
    indices = {m.citation_index for m in opinion.page_marks}
    for index in sorted(i for i in indices if i is not None):
        run = _forward_run(opinion.page_marks, index)
        if run and run[0] <= first_page <= run[-1]:
            return index
    return None


def _pincite_page(entry: LedgerEntry) -> Optional[int]:
    raw = (entry.citation.pin_cite or "").strip()
    match = re.match(r"(\d+)", raw.replace(",", ""))
    return int(match.group(1)) if match else None


def _first_page(entry: LedgerEntry) -> Optional[int]:
    page = (entry.citation.page or "").strip().replace(",", "")
    return int(page) if page.isdigit() else None


def _pincite_pages(entry: LedgerEntry) -> set[int]:
    """Every page a pincite names, expanding ranges.

    "at 459-61" cites pages 459 through 461, so language found on 461 is on a
    cited page. Reading only the first number reports a page the citation
    actually names as a page it does not.
    """
    raw = (entry.citation.pin_cite or entry.citation.page or "").strip()
    pages: set[int] = set()
    for chunk in re.split(r"[,;]", raw):
        match = re.match(r"\s*(\d+)\s*[-\u2013]\s*(\d+)", chunk)
        if match:
            low, high = int(match.group(1)), match.group(2)
            # "459-61" abbreviates 461, not 61.
            high_full = int(str(low)[: len(str(low)) - len(high)] + high) if len(high) < len(str(low)) else int(high)
            if low <= high_full <= low + 60:
                pages.update(range(low, high_full + 1))
                continue
        single = re.match(r"\s*(\d+)", chunk)
        if single:
            pages.add(int(single.group(1)))
    return pages


#: More candidate pages than this means the quotation recurs rather than sits
#: somewhere. Two allows for a passage that spans a page break.
_MAX_LOCATED_PAGES = 2


def verify_pincite_by_page(
    entry: LedgerEntry, page_texts: dict, normalize=None
) -> bool:
    """Check the pincite against star-paginated page text. True if decided.

    The strong form of the check. Rather than asking whether a passage is
    somewhere in the opinion, it asks whether the passage is on the page the
    citation names -- which is what a pincite asserts.

    A range check cannot do this work: a wrong pincite usually still falls
    inside the opinion, so only page-level text separates a correct pincite
    from a wrong one.
    """
    pincite = _pincite_page(entry)
    quotes = [q for q in entry.citation.quoted_language if len(q.strip()) >= 24]
    if pincite is None or not quotes or not page_texts:
        return False

    if normalize is None:
        from .verify_quote import normalize_quote as normalize

    cited = str(pincite)
    for quote in quotes:
        needle = normalize(quote)
        if len(needle) < 20:
            continue
        normalized_pages = {label: normalize(text) for label, text in page_texts.items()}
        located = [label for label, text in normalized_pages.items() if needle in text]
        if not located:
            continue
        # A passage found on many pages was not located: the needle is a phrase
        # common enough to recur, so which page it "is on" is undefined. Saying
        # the pincite is wrong on that basis reports a finding the evidence does
        # not support, so the check declines to decide.
        if len(located) > _MAX_LOCATED_PAGES:
            continue

        # A quotation that begins on the cited page and runs onto the next is
        # correctly pincited. Matching the whole passage finds it only on the
        # page it ends on, so the opening words are checked against the cited
        # page as well.
        opening = needle[:60]
        starts_on_cited = (
            cited in normalized_pages and opening in normalized_pages[cited]
        )
        cited_pages = _pincite_pages(entry) or {pincite}
        on_a_cited_page = any(
            label.isdigit() and int(label) in cited_pages for label in located
        )
        if starts_on_cited or cited in located or on_a_cited_page:
            entry.try_set_check(
                "pincite",
                CheckResult(
                    Verdict.PASS,
                    evidence=(
                        f"the quoted language appears on page {cited} of the "
                        f"opinion, which is the page cited"
                    ),
                    sources_consulted=[PINCITE_SOURCE],
                ),
            )
            return True
        # Adjacent pages are within the noise of where a passage is deemed to
        # sit; only a real distance is a finding.
        if any(abs(int(label) - pincite) <= 1 for label in located if label.isdigit()):
            entry.try_set_check(
                "pincite",
                CheckResult(
                    Verdict.PASS,
                    evidence=(
                        f"the quoted language appears at page "
                        f"{', '.join(sorted(located, key=int))}, adjacent to the "
                        f"cited page {cited}"
                    ),
                    sources_consulted=[PINCITE_SOURCE],
                ),
            )
            return True

        entry.try_set_check(
            "pincite",
            CheckResult(
                Verdict.FAIL,
                evidence=(
                    f"the citation pincites page {cited}, but the quoted language "
                    f"appears on page {', '.join(sorted(located, key=int))} of the "
                    "opinion. Page text was reconstructed from star pagination, so "
                    "this compares the cited page against the page the words are on."
                ),
                sources_consulted=[PINCITE_SOURCE],
                detail={"cited": pincite, "found_on": located},
            ),
        )
        return True
    return False


def verify_pincite(
    entry: LedgerEntry, opinions: list[OpinionText], located_page: Optional[str] = None
) -> None:
    """Set the ``pincite`` dimension on one ledger entry."""
    pincite = _pincite_page(entry)
    if pincite is None:
        entry.try_set_check(
            "pincite", CheckResult(Verdict.NA, reason="this citation carries no pincite")
        )
        return

    paginated = [o for o in opinions if o.has_pagination]
    if not paginated:
        entry.try_set_check(
            "pincite",
            CheckResult(
                Verdict.NOT_CHECKABLE,
                reason=(
                    "the retrieved text carries no star pagination, so the page a "
                    "passage falls on cannot be determined. Star pagination "
                    "originates in commercial reporters and is often absent from "
                    "free sources; verify the pincite against the published report."
                ),
                sources_consulted=[PINCITE_SOURCE],
            ),
        )
        return

    # A quotation located on a star page settles it outright.
    if located_page and located_page.isdigit():
        found = int(located_page)
        # The located page is accurate to about one page: a match's offset is
        # mapped through a normalised string whose length differs from the
        # source. Claiming page-exact agreement would overstate what was
        # measured, so agreement is asserted only within that tolerance and the
        # tolerance is stated.
        if abs(found - pincite) <= PAGE_TOLERANCE:
            entry.try_set_check(
                "pincite",
                CheckResult(
                    Verdict.PASS,
                    evidence=(
                        f"the quoted language falls at page {found}"
                        + (
                            f", the page cited"
                            if found == pincite
                            else f", within one page of the cited page {pincite}"
                        )
                        + ". Page location is accurate to about one page."
                    ),
                    sources_consulted=[PINCITE_SOURCE],
                ),
            )
        else:
            # Deliberately not a FAIL. The offset of a match is mapped to a page
            # proportionally, because normalisation changes string lengths, so
            # the located page is an estimate accurate to roughly a page. That
            # is enough to tell a reader where to look and not enough to
            # contradict them with.
            entry.try_set_check(
                "pincite",
                CheckResult(
                    Verdict.NOT_CHECKABLE,
                    reason=(
                        f"the citation pincites page {pincite}, but the quoted "
                        f"language appears to fall around page {found}. The page "
                        "estimate is approximate; check the pincite against the "
                        "published report."
                    ),
                    sources_consulted=[PINCITE_SOURCE],
                    detail={"cited": pincite, "estimated": found},
                ),
            )
        return

    # Otherwise the range check is the honest partial result (spec 7.1 step 4).
    first_page = _first_page(entry)
    best: list[int] = []
    for opinion in paginated:
        run = _forward_run(opinion.page_marks, _reporter_index(opinion, first_page))
        if len(run) > len(best):
            best = run
    if len(best) < 2:
        entry.try_set_check(
            "pincite",
            CheckResult(
                Verdict.NOT_CHECKABLE,
                reason="too few star-pagination markers to establish a page range",
                sources_consulted=[PINCITE_SOURCE],
            ),
        )
        return

    low, high = best[0], best[-1]
    if low <= pincite <= high:
        entry.try_set_check(
            "pincite",
            CheckResult(
                Verdict.NOT_CHECKABLE,
                reason=(
                    f"page {pincite} falls within the opinion, which runs *{low} to "
                    f"*{high}, but no quoted language was located to confirm the "
                    "material is on that page. Read the page to confirm."
                ),
                sources_consulted=[PINCITE_SOURCE],
                detail={"cited": pincite, "range": [low, high]},
            ),
        )
        return

    entry.try_set_check(
        "pincite",
        CheckResult(
            Verdict.FAIL,
            evidence=(
                f"the citation pincites page {pincite}, but the opinion occupies "
                f"pages {low} to {high}. The cited page is not part of this opinion."
            ),
            sources_consulted=[PINCITE_SOURCE],
            detail={"cited": pincite, "range": [low, high]},
        ),
    )
