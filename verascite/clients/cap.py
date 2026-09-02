"""Caselaw Access Project: an independent second source.

Harvard's Caselaw Access Project publishes 6.5M US decisions as static files
under CC0 -- no authentication, no rate limit, no quota. That matters more than
it sounds. CourtListener throttles a default-tier account at 125 requests per
day, which caps quotation checking at roughly one brief a day and puts pincite
verification out of reach entirely. CAP removes that ceiling.

It is also a genuinely *independent* source, which is the point the literature
makes most forcefully: a single-source verifier is structurally capped on
precision, and disagreement between two databases about the same citation is
strong evidence that a citation is *unverifiable* rather than fabricated
(CiteTracer, arXiv:2605.08583, whose ablation shows real-class F1 collapsing
from 97.0 to 31.4 when secondary sources are removed).

What CAP provides that CourtListener's citation-lookup does not:

* **Full opinion text**, typed by role, without a request budget.
* **Explicit star pagination** -- ``<a class="page-label">*2`` in the HTML --
  which is what pincite verification needs and what free sources usually lack.
* **First and last page**, so a pincite outside the opinion is detectable even
  when pagination is missing.
* **The court**, without the separate docket request CourtListener requires.

Layout, all under ``https://static.case.law``::

    /ReportersMetadata.json              short_name -> slug for 401 reporters
    /{slug}/{volume}/CasesMetadata.json  every case in the volume
    /{slug}/{volume}/cases/{file}.json   one case, full text
    /{slug}/{volume}/html/{file}.html    the same case with page labels
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import requests

BASE = "https://static.case.law"
SOURCE_NAME = "caselaw_access_project"

#: Volume metadata is a few megabytes and covers every case in the volume, so
#: one fetch usually serves many citations from the same reporter volume.
_VOLUME_CACHE_DIR = "cap-volumes"


@dataclass
class CapCase:
    """One decision as CAP holds it."""

    cite: str
    name: str
    name_abbreviation: str
    decision_date: str
    court: str
    court_abbreviation: str
    first_page: Optional[int]
    last_page: Optional[int]
    file_name: str
    reporter_slug: str
    volume: str
    opinions: list[dict] = field(default_factory=list)
    page_labels: list[tuple[str, int]] = field(default_factory=list)

    @property
    def year(self) -> Optional[str]:
        return self.decision_date[:4] if self.decision_date else None

    def covers_page(self, page: int) -> Optional[bool]:
        """Whether ``page`` falls inside this case. None when CAP omits a bound.

        CAP records the bounds as strings in some volumes and as integers in
        others, so both are coerced before comparison.
        """
        try:
            first, last = int(self.first_page), int(self.last_page)
        except (TypeError, ValueError):
            return None
        return first <= int(page) <= last

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v not in (None, [], "")}


class CapClient:
    """Read-only client over the CAP static file tree."""

    def __init__(self, cache_dir: Optional[Path] = None, session=None,
                 timeout: float = 60.0):
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            (self.cache_dir / _VOLUME_CACHE_DIR).mkdir(parents=True, exist_ok=True)
        self.session = session or requests.Session()
        self.timeout = timeout
        self._reporters: Optional[dict[str, str]] = None
        self._volumes: dict[tuple[str, str], list[dict]] = {}
        self.stats = {"requests": 0, "cache_hits": 0, "misses": 0, "failures": 0}

    # --- plumbing --------------------------------------------------------

    def _cache_path(self, name: str) -> Optional[Path]:
        if not self.cache_dir:
            return None
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", name)
        return self.cache_dir / _VOLUME_CACHE_DIR / safe

    def _get(self, url: str, cache_key: Optional[str] = None) -> Optional[bytes]:
        path = self._cache_path(cache_key) if cache_key else None
        if path and path.exists():
            self.stats["cache_hits"] += 1
            return path.read_bytes()
        try:
            response = self.session.get(url, timeout=self.timeout)
            self.stats["requests"] += 1
        except requests.RequestException:
            self.stats["failures"] += 1
            return None
        if response.status_code != 200:
            self.stats["misses"] += 1
            return None
        if path:
            path.write_bytes(response.content)
        return response.content

    # --- reporters -------------------------------------------------------

    def reporter_slug(self, reporter: Optional[str]) -> Optional[str]:
        """Map a reporter abbreviation to the archive's directory slug."""
        if not reporter:
            return None
        if self._reporters is None:
            blob = self._get(f"{BASE}/ReportersMetadata.json", "ReportersMetadata.json")
            table: dict[str, str] = {}
            if blob:
                try:
                    for entry in json.loads(blob):
                        short, slug = entry.get("short_name"), entry.get("slug")
                        if short and slug:
                            table[_reporter_key(short)] = slug
                except (json.JSONDecodeError, TypeError):
                    pass
            self._reporters = table
        return self._reporters.get(_reporter_key(reporter))

    # --- lookup ----------------------------------------------------------

    def volume_cases(self, slug: str, volume: str) -> list[dict]:
        key = (slug, volume)
        if key in self._volumes:
            return self._volumes[key]
        blob = self._get(
            f"{BASE}/{slug}/{volume}/CasesMetadata.json",
            f"{slug}-{volume}-CasesMetadata.json",
        )
        cases: list[dict] = []
        if blob:
            try:
                cases = json.loads(blob)
            except json.JSONDecodeError:
                cases = []
        self._volumes[key] = cases
        return cases

    def find(
        self,
        volume: str,
        reporter: str,
        page: str,
        *,
        page_is_pincite: bool = False,
    ) -> Optional[CapCase]:
        """Locate a case by its citation. Returns None when CAP lacks it.

        ``page_is_pincite`` says the page addresses somewhere *inside* the
        case rather than the page it begins on -- true of a short form whose
        antecedent is not in the document, as when a brief excerpt begins
        after the full citation. Only then may a case be matched because its
        page range contains the page. It must not be the general fallback: a
        page number always falls inside *some* case, so containment applied to
        a full citation would find a case for a citation that does not exist,
        and report the wrong case as evidence that it does.
        """
        slug = self.reporter_slug(reporter)
        if not slug or not str(volume).isdigit():
            return None

        wanted = f"{volume} {reporter} {page}".replace("  ", " ").strip()
        wanted_key = _cite_key(wanted)
        for record in self.volume_cases(slug, str(volume)):
            for citation in record.get("citations") or []:
                if _cite_key(citation.get("cite", "")) == wanted_key:
                    return _to_case(record, slug, str(volume))
        # Fall back to the starting page, which is how a citation addresses a
        # case even when the recorded cite string differs.
        if str(page).isdigit():
            for record in self.volume_cases(slug, str(volume)):
                # CAP records the page as a string in some volumes and an
                # integer in others; comparing the raw value silently never
                # matched for the string ones.
                try:
                    first = int(record.get("first_page"))
                except (TypeError, ValueError):
                    continue
                if first == int(page):
                    return _to_case(record, slug, str(volume))
        if page_is_pincite and str(page).isdigit():
            for record in self.volume_cases(slug, str(volume)):
                case = _to_case(record, slug, str(volume))
                if case.covers_page(int(page)):
                    return case
        return None

    def opinions_for(
        self, volume: str, reporter: str, page: str, *, page_is_pincite: bool = False
    ):
        """Everything needed to check a citation: OpinionText plus the record.

        Returns (opinions, case) or ([], None) when CAP does not hold it.
        """
        from .opinions import from_cap

        case = self.find(volume, reporter, page, page_is_pincite=page_is_pincite)
        if case is None:
            return [], None
        self.opinions(case)
        html = self._get(
            f"{BASE}/{case.reporter_slug}/{case.volume}/html/{case.file_name}.html",
            f"{case.reporter_slug}-{case.volume}-{case.file_name}.html",
        )
        decoded = html.decode("utf-8", "replace") if html else ""
        return from_cap(case, decoded), case

    def page_texts_for(self, case: CapCase) -> dict[str, str]:
        """Star-paginated page text for a case, keyed by page label."""
        html = self._get(
            f"{BASE}/{case.reporter_slug}/{case.volume}/html/{case.file_name}.html",
            f"{case.reporter_slug}-{case.volume}-{case.file_name}.html",
        )
        return page_texts(html.decode("utf-8", "replace")) if html else {}

    def opinions(self, case: CapCase) -> CapCase:
        """Attach full opinion text and star-pagination labels."""
        if case.opinions:
            return case
        blob = self._get(
            f"{BASE}/{case.reporter_slug}/{case.volume}/cases/{case.file_name}.json",
            f"{case.reporter_slug}-{case.volume}-{case.file_name}.json",
        )
        if blob:
            try:
                body = json.loads(blob).get("casebody") or {}
                case.opinions = [
                    {"type": o.get("type") or "majority", "text": o.get("text") or "",
                     "author": o.get("author") or ""}
                    for o in body.get("opinions") or []
                    if (o.get("text") or "").strip()
                ]
            except (json.JSONDecodeError, AttributeError, TypeError):
                pass

        html = self._get(
            f"{BASE}/{case.reporter_slug}/{case.volume}/html/{case.file_name}.html",
            f"{case.reporter_slug}-{case.volume}-{case.file_name}.html",
        )
        if html:
            case.page_labels = _page_labels(html.decode("utf-8", "replace"))
        return case


_PAGE_LABEL = re.compile(r'<a[^>]*class="page-label"[^>]*>\*?(\d+)</a>')
_TAG = re.compile(r"<[^>]+>")


def page_texts(html: str) -> dict[str, str]:
    """Text of each star-paginated page, keyed by page label.

    A page label marks where a page *begins*, so the text of page N runs from
    its own anchor to the next one. Labels are not monotonic -- footnotes and
    separate opinions carry their own anchors back into earlier pages -- so
    segments are accumulated per label rather than assumed to appear once.

    This is what a pincite check needs: not whether a passage is in the
    opinion, but whether it is on the page the citation names.
    """
    import html as html_module

    marks = list(_PAGE_LABEL.finditer(html))
    pages: dict[str, list[str]] = {}
    for index, mark in enumerate(marks):
        start = mark.end()
        end = marks[index + 1].start() if index + 1 < len(marks) else len(html)
        body = html_module.unescape(_TAG.sub(" ", html[start:end]))
        pages.setdefault(mark.group(1), []).append(" ".join(body.split()))
    return {label: " ".join(parts) for label, parts in pages.items()}


def _page_labels(html: str) -> list[tuple[str, int]]:
    """(label, offset) for each star-pagination marker, in document order."""
    return [(m.group(1), m.start()) for m in _PAGE_LABEL.finditer(html)]


def _reporter_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _cite_key(cite: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (cite or "").lower())


def _to_case(record: dict, slug: str, volume: str) -> CapCase:
    court = record.get("court") or {}
    cites = record.get("citations") or [{}]
    return CapCase(
        cite=cites[0].get("cite", ""),
        name=record.get("name") or "",
        name_abbreviation=record.get("name_abbreviation") or "",
        decision_date=record.get("decision_date") or "",
        court=court.get("name") or "",
        court_abbreviation=court.get("name_abbreviation") or "",
        first_page=record.get("first_page"),
        last_page=record.get("last_page"),
        file_name=record.get("file_name") or "",
        reporter_slug=slug,
        volume=volume,
    )
