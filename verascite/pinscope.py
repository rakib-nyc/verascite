"""Scope a proposition read to the page the citation actually names."""
import re
from typing import Optional


def first_page_of(pin_cite: Optional[str]) -> Optional[int]:
    """The first page a pincite names. '852 n.12' -> 852; '1075-1076' -> 1075."""
    if not pin_cite:
        return None
    match = re.search(r"\d+", str(pin_cite))
    return int(match.group()) if match else None


def passage_for_pincite(page_texts: dict, pin_cite, width_pages: int = 1) -> str:
    """Text of the cited page plus its neighbours, or '' when unavailable.

    A proposition is asserted about a specific page. Reading a keyword-matched
    window of the whole opinion answers a different question -- whether the
    case says this anywhere -- and a case very often says something close to
    the claim somewhere other than where it was cited. The neighbours are
    included because a passage that begins on the cited page routinely runs
    onto the next, and because a pincite is often one page off in either
    direction without being wrong about the holding.
    """
    page = first_page_of(pin_cite)
    if page is None or not page_texts:
        return ""
    wanted = [str(p) for p in range(page - width_pages, page + width_pages + 1)]
    parts = [page_texts[p] for p in wanted if p in page_texts]
    return " ".join(" ".join(p.split()) for p in parts if p)
