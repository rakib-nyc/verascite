"""Opinion text retrieval and parsing.

CourtListener stores opinion text in several mutually exclusive fields
depending on where the opinion came from. On *Bell Atlantic Corp. v. Twombly*,
for instance, ``plain_text`` and ``html`` are both empty while
``html_with_citations`` holds 70KB and ``xml_harvard`` holds 54KB. Any
retrieval path that reads only ``plain_text`` finds nothing for a large share
of the corpus, so this module tries the sources in order of usefulness.

Two properties are preserved through parsing because later checks depend on
them:

* **Quotation marks.** The quote-within-quote check needs to know whether
  matched language sits inside a quotation in the source opinion, so quote
  characters survive intact.
* **Star pagination.** ``xml_harvard`` carries structured
  ``<page-number label="549" citation-index="1">`` markers. These are captured
  with their offsets rather than discarded, so the pincite check (M5) can say
  which page a passage falls on -- and, where they are absent, say honestly
  that it cannot.

A cluster's opinions are fetched separately and kept separate. Language found
in a dissent is not language the court held, and collapsing sub-opinions into
one blob would silently erase that distinction.
"""

from __future__ import annotations

import html as html_module
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import requests

from ..config import (
    DISPOSITION_TAIL_FRACTION,
    MIN_COMPLETE_OPINION_CHARS,
    MIN_PAGE_MARKS_FOR_CONTIGUITY,
    OPINION_REQUESTS_PER_MINUTE,
)
from .courtlistener import API_ROOT, CourtListenerClient, _DiskCache, _TokenBucket

OPINION_SOURCE_NAME = "courtlistener_opinion"
CAP_SOURCE_NAME = "caselaw_access_project"

#: CAP labels opinions with the same vocabulary CourtListener uses for types.
CAP_TYPE_LABELS = {
    "majority": ("majority opinion", True),
    "concurrence": ("concurrence", False),
    "dissent": ("dissent", False),
    "concurring-in-part-and-dissenting-in-part": ("opinion concurring in part", False),
    "unanimous": ("unanimous opinion", True),
    "plurality": ("plurality opinion", False),
    "remittitur": ("remittitur", True),
    "rehearing": ("opinion on rehearing", True),
    "on-the-merits": ("opinion on the merits", True),
}


def from_cap(case, html: str = "") -> list["OpinionText"]:
    """Build OpinionText objects from a Caselaw Access Project record.

    Reuses the same parsing and completeness machinery as CourtListener, so a
    quotation, an attribution, and a pincite are checked identically whichever
    source supplied the text.
    """
    out: list[OpinionText] = []

    # The HTML carries star pagination; the JSON carries per-opinion typing and
    # authorship. Their offsets do not correspond, so both are emitted: the
    # typed opinions decide attribution, and the HTML-derived text -- whose
    # page markers align with its own offsets -- decides which page a passage
    # falls on. verify_quote searches every version and takes the page from
    # whichever one supplies it.
    if html:
        text, marks = strip_markup(html)
        if text.strip():
            out.append(
                OpinionText(
                    opinion_id=-(abs(hash(case.file_name)) % 10**8),
                    text=text,
                    source_field="cap_html",
                    type_label="combined opinion",
                    is_court_holding=True,
                    page_marks=marks,
                    complete=True,
                    completeness_reason=(
                        f"Caselaw Access Project reports this case at pages "
                        f"{case.first_page}-{case.last_page}"
                    ),
                )
            )

    for index, opinion in enumerate(case.opinions):
        label, holding = CAP_TYPE_LABELS.get(
            (opinion.get("type") or "majority").lower(), ("opinion", True)
        )
        text = opinion.get("text") or ""
        if not text.strip():
            continue
        out.append(
            OpinionText(
                opinion_id=-(abs(hash(case.file_name)) % 10**8) - index,
                text=text,
                source_field="cap_json",
                type_label=label,
                is_court_holding=holding,
                author=opinion.get("author") or "",
                complete=True,
                completeness_reason="Caselaw Access Project full text",
            )
        )
    return out

#: Text fields in descending order of fidelity.
TEXT_FIELDS = (
    "xml_harvard",
    "html_with_citations",
    "html_columbia",
    "html_lawbox",
    "html_anon_2020",
    "html",
    "plain_text",
)

#: CourtListener opinion type codes -> whether the text is the court's holding.
#: Verified against the live OPTIONS schema for /api/rest/v4/opinions/.
#: The value is (label, does this text carry the court's holding).
#: A plurality is marked non-holding: it did not command a majority, so
#: quoting it as "the Court held" overstates it.
OPINION_TYPES = {
    "010combined": ("combined opinion", True),
    "015unamimous": ("unanimous opinion", True),
    "020lead": ("majority opinion", True),
    "025plurality": ("plurality opinion", False),
    "030concurrence": ("concurrence", False),
    "035concurrenceinpart": ("opinion concurring in part", False),
    "040dissent": ("dissent", False),
    "050addendum": ("addendum", False),
    "060remittitur": ("remittitur", True),
    "070rehearing": ("opinion on rehearing", True),
    "080onthemerits": ("opinion on the merits", True),
    "090onmotiontostrike": ("opinion on motion to strike", True),
    "100trialcourt": ("trial court document", True),
}

#: Language that ends an opinion. Used as a completeness signal: text that
#: stops before any of these is a fragment, whatever its length.
_DISPOSITION_RE = re.compile(
    r"\b(?:it is so ordered|so ordered|affirmed|reversed and remanded|reversed|"
    r"vacated and remanded|vacated|remanded|dismissed|petition (?:is )?denied|"
    r"writ (?:is )?denied|judgment (?:of the [\w .]+ )?is (?:affirmed|reversed|vacated)|"
    r"we (?:affirm|reverse|vacate|remand)|for the foregoing reasons)\b",
    re.IGNORECASE,
)

_BLOCK_TAGS = {
    "p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
    "blockquote", "opinion", "section", "footnote", "author", "center",
}
_TAG_RE = re.compile(r"<(/?)([A-Za-z][\w-]*)((?:\s+[^<>]*?)?)/?>", re.DOTALL)
_PAGE_XML_RE = re.compile(
    r'<page-number[^>]*\blabel="(?P<label>[^"]+)"[^>]*>(?P<body>.*?)</page-number>',
    re.DOTALL,
)
_PAGE_IDX_RE = re.compile(r'citation-index="(\d+)"')
#: Star pagination in HTML. CourtListener marks it with a star-pagination
#: span; the Caselaw Access Project uses an anchor with class="page-label".
_PAGE_SPAN_RE = re.compile(
    r'<(?:span|a)[^>]*class="[^"]*(?:star-pagination|page-label)[^"]*"[^>]*>'
    r'\s*\*?(?P<label>\d+)\s*</(?:span|a)>',
    re.IGNORECASE | re.DOTALL,
)


@dataclass
class PageMark:
    """A star-pagination marker and where it falls in the extracted text."""

    label: str
    offset: int
    citation_index: Optional[int] = None

    def to_dict(self) -> dict:
        d = {"label": self.label, "offset": self.offset}
        if self.citation_index is not None:
            d["citation_index"] = self.citation_index
        return d


@dataclass
class OpinionText:
    """One version of one sub-opinion's text, with provenance.

    A single sub-opinion may be stored in several fields at once, each with
    different completeness. Every populated field becomes its own OpinionText
    so a quote can be searched against all of them before being called absent.
    """

    opinion_id: int
    text: str
    source_field: str
    type_code: str = ""
    type_label: str = "unknown"
    is_court_holding: bool = True
    author: str = ""
    extracted_by_ocr: bool = False
    page_marks: list[PageMark] = field(default_factory=list)
    #: Syllabi and headnotes are prepared by the Reporter of Decisions, not the
    #: court. United States v. Detroit Timber & Lumber Co., 200 U.S. 321, 337
    #: (1906), is printed on every U.S. Reports headnote saying exactly that.
    is_syllabus: bool = False
    complete: bool = True
    completeness_reason: str = ""

    @property
    def has_pagination(self) -> bool:
        return bool(self.page_marks)

    @property
    def descriptor(self) -> str:
        if self.is_syllabus:
            return "syllabus"
        label = self.type_label
        return f"{label} by {self.author}" if self.author else label

    def page_for(self, offset: int) -> Optional[str]:
        """Star page containing this offset, or None without pagination."""
        current = None
        for mark in self.page_marks:
            if mark.offset <= offset:
                current = mark.label
            else:
                break
        return current

    def to_dict(self) -> dict:
        return {
            "opinion_id": self.opinion_id,
            "source_field": self.source_field,
            "type_label": self.type_label,
            "is_court_holding": self.is_court_holding,
            "author": self.author,
            "extracted_by_ocr": self.extracted_by_ocr,
            "is_syllabus": self.is_syllabus,
            "complete": self.complete,
            "completeness_reason": self.completeness_reason,
            "type_code": self.type_code,
            "length": len(self.text),
            "has_pagination": self.has_pagination,
            "page_marks": [m.to_dict() for m in self.page_marks],
            "text": self.text,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "OpinionText":
        return cls(
            opinion_id=d["opinion_id"],
            text=d.get("text", ""),
            source_field=d.get("source_field", ""),
            type_code=d.get("type_code", ""),
            type_label=d.get("type_label", "unknown"),
            is_court_holding=bool(d.get("is_court_holding", True)),
            author=d.get("author", ""),
            extracted_by_ocr=bool(d.get("extracted_by_ocr", False)),
            page_marks=[PageMark(**m) for m in d.get("page_marks", [])],
            is_syllabus=bool(d.get("is_syllabus", False)),
            complete=bool(d.get("complete", True)),
            completeness_reason=d.get("completeness_reason", ""),
        )


def strip_markup(markup: str) -> tuple[str, list[PageMark]]:
    """Flatten opinion markup to text, capturing star-pagination offsets.

    Quote characters are preserved deliberately: the quote-within-quote check
    depends on knowing whether matched language sits inside a quotation in the
    source.
    """
    out: list[str] = []
    marks: list[PageMark] = []
    length = 0

    def emit(chunk: str) -> None:
        nonlocal length
        if chunk:
            out.append(chunk)
            length += len(chunk)

    # Page markers first, so their offsets are known before tags are dropped.
    tokens: list[tuple[int, int, Optional[PageMark]]] = []
    for match in _PAGE_XML_RE.finditer(markup):
        index = _PAGE_IDX_RE.search(match.group(0))
        tokens.append(
            (
                match.start(),
                match.end(),
                PageMark(
                    label=match.group("label"),
                    offset=0,
                    citation_index=int(index.group(1)) if index else None,
                ),
            )
        )
    for match in _PAGE_SPAN_RE.finditer(markup):
        if any(start <= match.start() < end for start, end, _ in tokens):
            continue
        tokens.append((match.start(), match.end(), PageMark(match.group("label"), 0)))
    tokens.sort(key=lambda t: t[0])

    cursor = 0
    for start, end, mark in tokens + [(len(markup), len(markup), None)]:
        segment = markup[cursor:start]
        for piece in _split_tags(segment):
            emit(piece)
        if mark is not None:
            mark.offset = length
            marks.append(mark)
        cursor = end

    # Marks were recorded against the pre-normalization string, and unescaping
    # and whitespace collapsing both change lengths. Remap each offset by
    # normalizing its own prefix, so a page label still points at its page.
    raw = "".join(out)
    normalized = _normalize_text(raw)
    lead = len(normalized) - len(normalized.lstrip())
    for mark in marks:
        mark.offset = max(0, len(_normalize_text(raw[: mark.offset])) - lead)
    return normalized.strip(), marks


def _normalize_text(raw: str) -> str:
    text = html_module.unescape(raw)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text)


def _split_tags(segment: str):
    """Yield text pieces, turning block-level tags into newlines."""
    cursor = 0
    for match in _TAG_RE.finditer(segment):
        yield segment[cursor : match.start()]
        if match.group(2).lower() in _BLOCK_TAGS:
            yield "\n"
        cursor = match.end()
    yield segment[cursor:]


def _page_runs(marks: list[PageMark]) -> list[list[int]]:
    """Numeric page labels grouped by reporter.

    An opinion carries star pagination for every reporter it was printed in --
    Twombly has U.S., S. Ct., and L. Ed. 2d markers interleaved through the
    same text, distinguished only by ``citation-index``. Reading them as one
    sequence manufactures gaps and reports a complete opinion as truncated.
    """
    grouped: dict[object, list[int]] = {}
    for mark in marks:
        if mark.label.isdigit():
            grouped.setdefault(mark.citation_index, []).append(int(mark.label))

    runs: list[list[int]] = []
    for labels in grouped.values():
        # Harvard markup collects footnotes at the end of the document with
        # their own page markers, which point back at pages already passed:
        # Twombly's majority runs *549..*570 and then emits *552 and *564.
        # Read literally that looks like a gap in a complete opinion. Only the
        # forward-moving run describes the body text.
        forward = [labels[0]] if labels else []
        for label in labels[1:]:
            if label >= forward[-1]:
                forward.append(label)
        if forward:
            runs.append(forward)
    return runs


def assess_completeness(
    opinion: OpinionText, expected_first_page: Optional[int] = None
) -> tuple[bool, str]:
    """Is this text the whole opinion, or a fragment?

    "Not empty" is not "complete". CourtListener clusters sometimes carry only
    a syllabus, a truncated scrape, or a first-page fragment, and a quote
    absent from a fragment is not a misquote -- it is a fact about our copy.

    Two structural signals are available without reading the text:

    1. **Contiguous star pagination**, per reporter. Labels running without
       gaps mean the text between them is all there.
    2. **A disposition.** Opinions end by disposing of the case -- affirmed,
       reversed, "It is so ordered". Text that stops before any such language
       ended early, however contiguous the pages it does carry.

    Pagination alone is not enough: a copy truncated at page 4 of 20 still has
    four contiguous pages. So contiguity establishes no gaps in the middle and
    the disposition establishes that the end is present, and a text is only
    called complete when nothing contradicts either.
    """
    if len(opinion.text) < MIN_COMPLETE_OPINION_CHARS:
        return False, (
            f"retrieved text is only {len(opinion.text)} characters, short enough "
            "to be a headnote or a first-page fragment rather than the opinion"
        )

    tail_start = int(len(opinion.text) * (1 - DISPOSITION_TAIL_FRACTION))
    has_disposition = bool(_DISPOSITION_RE.search(opinion.text[tail_start:]))

    runs = [r for r in _page_runs(opinion.page_marks) if len(r) >= MIN_PAGE_MARKS_FOR_CONTIGUITY]
    if runs:
        longest = max(runs, key=len)
        gaps = [b - a for a, b in zip(longest, longest[1:])]
        missing = [g for g in gaps if g > 1]
        if missing:
            return False, (
                f"star pagination has {len(missing)} gap(s) between *{longest[0]} "
                f"and *{longest[-1]}, so pages are missing from the retrieved text"
            )
        if expected_first_page is not None and longest[0] < expected_first_page:
            return False, (
                f"star pagination starts at *{longest[0]}, before the reporter page "
                f"{expected_first_page} where this case begins"
            )
        if not has_disposition:
            return False, (
                f"star pagination runs contiguously from *{longest[0]} to "
                f"*{longest[-1]}, but the text does not end with a disposition, so "
                "the closing pages may be missing"
            )
        return True, (
            f"star pagination runs contiguously from *{longest[0]} to *{longest[-1]} "
            "and the text ends with a disposition"
        )

    if has_disposition:
        return True, "text ends with a disposition"

    return False, (
        "no contiguous star pagination and no disposition language at the end, "
        "so the retrieved text cannot be shown to be the complete opinion"
    )


def parse_opinion_versions(payload: dict) -> list[OpinionText]:
    """Every populated text version of one sub-opinion.

    Different sources have different completeness, so all of them are kept.
    A quote must be absent from *all* versions before it can be called absent.
    """
    code = payload.get("type") or ""
    label, is_holding = OPINION_TYPES.get(code, ("opinion", True))
    versions: list[OpinionText] = []

    for field_name in TEXT_FIELDS:
        raw = payload.get(field_name)
        if not raw or not raw.strip():
            continue
        if field_name == "plain_text":
            text = raw
            marks = [PageMark(m.group(1), m.start()) for m in re.finditer(r"\*(\d{1,5})\b", raw)]
        else:
            text, marks = strip_markup(raw)
        if not text.strip():
            continue
        versions.append(
            OpinionText(
                opinion_id=payload.get("id", 0),
                text=text,
                source_field=field_name,
                type_code=code,
                type_label=label,
                is_court_holding=is_holding,
                author=payload.get("author_str") or "",
                extracted_by_ocr=bool(payload.get("extracted_by_ocr")),
                page_marks=marks,
            )
        )
    return versions


def parse_opinion(payload: dict) -> Optional[OpinionText]:
    """The single best version of a sub-opinion."""
    versions = parse_opinion_versions(payload)
    return versions[0] if versions else None


_SYLLABUS_RE = re.compile(r"<syllabus[^>]*>(.*?)</syllabus>", re.DOTALL | re.IGNORECASE)


def parse_syllabus(cluster: dict) -> Optional[OpinionText]:
    """The Reporter of Decisions' syllabus, if the cluster carries one.

    CourtListener has no syllabus opinion type -- the OPTIONS schema lists
    thirteen types and none of them is a syllabus -- so it arrives either in
    the cluster's ``syllabus`` field or inside Harvard ``headmatter`` markup.
    It is captured because quoting a headnote as the Court's own language is a
    real and common error, and no string search over the opinions would catch
    it.
    """
    raw = (cluster.get("syllabus") or "").strip()
    if not raw:
        match = _SYLLABUS_RE.search(cluster.get("headmatter") or "")
        raw = match.group(1) if match else ""
    if not raw.strip():
        return None

    text, marks = strip_markup(raw)
    if not text.strip():
        return None
    return OpinionText(
        opinion_id=-int(cluster.get("id", 0) or 0),
        text=text,
        source_field="syllabus",
        type_label="syllabus",
        is_court_holding=False,
        is_syllabus=True,
        page_marks=marks,
        complete=True,
        completeness_reason="syllabus text, not an opinion",
    )


class OpinionClient:
    """Fetches and caches opinion text. Opinions are immutable; cache forever."""

    def __init__(self, client: CourtListenerClient, cache_dir: Optional[Path] = None):
        self.client = client
        # Stay inside the cache directory the caller named. Deriving this from
        # its parent leaks opinion text outside --cache-dir and lets a stale
        # entry survive a deliberately fresh cache.
        base = cache_dir or client.cache.directory
        self.cache = _DiskCache(Path(base) / "opinions" if base else None)
        # The opinions/clusters endpoints are metered separately from, and far
        # more tightly than, citation lookup.
        self.bucket = client.request_bucket
        self.stats = {
            "fetched": 0,
            "cache_hits": 0,
            "failed": 0,
            "unreachable_sub_opinions": 0,
            "throttle_waits": 0,
        }

    def _get(self, url: str, params: dict | None = None) -> Optional[dict]:
        if getattr(self.client, "cache_only", False):
            return None

        """GET with throttle handling. Returns None only after real failure.

        A swallowed 429 here is not a slow request, it is missing text: the
        sub-opinion holding the quotation quietly drops out of the search and
        a correct quotation gets reported as absent. So throttling is waited
        out rather than treated as an empty result.
        """
        self.client.require_token()

        for attempt in range(4):
            self.bucket.acquire(1)
            try:
                response = self.client.session.get(
                    url,
                    params=params or {},
                    headers={
                        "Authorization": f"Token {self.client.token}",
                        "User-Agent": self.client.user_agent,
                    },
                    timeout=self.client.timeout,
                )
                self.client.stats["requests"] += 1
            except requests.RequestException:
                self.client.sleeper(min(2**attempt, 20))
                continue

            if response.status_code == 429:
                self.stats["throttle_waits"] += 1
                self.client.sleeper(_retry_delay(response))
                continue
            if response.status_code in (502, 503, 504):
                self.client.sleeper(min(2**attempt, 20))
                continue
            if response.status_code != 200:
                return None
            try:
                return response.json()
            except ValueError:
                return None
        return None

    def fetch_for_cluster(self, cluster_id: int) -> list[OpinionText]:
        """Every version of every sub-opinion in a cluster, plus any syllabus.

        Sub-opinions are kept separate: language in a dissent is not language
        the court held, and merging them would erase that distinction.
        """
        if not cluster_id:
            return []

        cache_path = self.cache._path(f"cluster:{cluster_id}")
        if cache_path and cache_path.exists():
            try:
                data = json.loads(cache_path.read_text(encoding="utf-8"))
                self.stats["cache_hits"] += 1
                return [OpinionText.from_dict(d) for d in data]
            except (json.JSONDecodeError, KeyError, OSError):
                pass

        cluster = self._get(
            f"{API_ROOT}/clusters/{cluster_id}/",
            {"fields": "id,sub_opinions,citations,syllabus,headmatter,citation_count"},
        )
        if not cluster:
            self.stats["failed"] += 1
            return []

        expected_first_page = _first_reporter_page(cluster)

        opinions: list[OpinionText] = []
        syllabus = parse_syllabus(cluster)
        if syllabus:
            opinions.append(syllabus)

        # One list request returns every sub-opinion with all its text fields.
        # Fetching them individually costs one request each against a 5/min
        # limit, which is both slow and how coverage went missing.
        expected = len(cluster.get("sub_opinions") or [])
        listing = self._get(f"{API_ROOT}/opinions/", {"cluster": cluster_id})
        results = (listing or {}).get("results") or []
        for payload in results:
            opinions.extend(parse_opinion_versions(payload))

        # A sub-opinion we could not retrieve is missing coverage, not absent
        # text. Silently dropping it lets a quote that lives in the unreachable
        # opinion be reported as a misquote.
        unreachable = max(0, expected - len(results))

        if unreachable:
            self.stats["unreachable_sub_opinions"] += unreachable

        for opinion in opinions:
            if opinion.is_syllabus:
                continue
            opinion.complete, opinion.completeness_reason = assess_completeness(
                opinion, expected_first_page
            )

        if unreachable:
            for opinion in opinions:
                opinion.complete = False
                opinion.completeness_reason = (
                    f"{unreachable} sub-opinion(s) of this authority could not be "
                    "retrieved, so the text searched is not the whole decision"
                )


        if opinions:
            self.stats["fetched"] += len(opinions)
            if cache_path:
                cache_path.write_text(
                    json.dumps([o.to_dict() for o in opinions]), encoding="utf-8"
                )
        else:
            self.stats["failed"] += 1
        return opinions


_WAIT_SECONDS_RE = re.compile(r"available in (\d+) second", re.IGNORECASE)


def _retry_delay(response) -> float:
    """How long the server says to wait, rather than a guess."""
    header = response.headers.get("Retry-After")
    if header and str(header).isdigit():
        return min(float(header) + 1.0, 300.0)
    try:
        match = _WAIT_SECONDS_RE.search(response.json().get("detail", ""))
        if match:
            return min(float(match.group(1)) + 1.0, 300.0)
    except (ValueError, AttributeError):
        pass
    return 30.0


def _first_reporter_page(cluster: dict) -> Optional[int]:
    """Reporter page where the case begins, for the pagination sanity check."""
    for citation in cluster.get("citations") or []:
        page = str(citation.get("page") or "")
        if page.isdigit():
            return int(page)
    return None
