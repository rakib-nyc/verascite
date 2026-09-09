"""Free federal sources for statutory and regulatory citations.

Contracts below were established by fetching the live endpoints, not by
reading documentation, because two of them behave in ways the documentation
does not mention and one of those two is dangerous.

**No credential is required anywhere in this module, and none is ever sent.**
The one federal endpoint that needs an API key -- ``api.govinfo.gov`` -- is
deliberately not used: everything it offers is available from services that
need nothing.

**The three-state result, and why it is the whole design.** Every lookup here
returns ``EXISTS``, ``ABSENT``, or ``UNVERIFIED``. The distinction between the
last two is the governing rule of this project expressed as a type:

``ABSENT``
    An authoritative source affirmatively reported that the provision is not
    there. Permitted from exactly three observations and no others: a
    successfully parsed local US Code title that does not contain the
    identifier, an eCFR ``/versions/`` response that returned **HTTP 200** with
    an empty result set, and a link-service **HTTP 400**.

``UNVERIFIED``
    Everything else. A timeout, a 5xx, a connection failure, an unparseable
    body, a source that was never consulted -- and, critically, an eCFR
    ``/full/`` **404**.

**The trap, stated plainly because it would otherwise be invisible.** eCFR's
``/versioner/v1/full/`` endpoint returns the *identical* 404 for a fabricated
section and for a real section requested at a date outside its history, and
its history begins 2017-01-01. Code that reads that 404 as non-existence will
report every genuine pre-2017 regulatory citation as absent. That is precisely
the false accusation this project exists to avoid, so ``/full/`` is never an
existence check here -- it supplies text, and only after ``/versions/`` has
said the provision is real.

**Structural asymmetry between the two codes.** US Code XML nests subsections
with canonical identifiers, so ``42 U.S.C. § 12112(b)(3)(A)`` maps exactly onto
``/us/usc/t42/s12112/b/3/A`` and a missing subsection is a lookup miss in a
file we hold. CFR XML is flat: paragraph designators are plain text at the head
of ``<P>`` elements and the hierarchy is implied by the order of the
designators, not encoded. Regulatory subsection checking is therefore
materially less reliable, and every result says so rather than presenting the
two as equivalent.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

from ..config import NEGATIVE_RESULT_TTL_DAYS

#: The three states. Strings rather than an enum so they survive the ledger's
#: JSON round trip without a custom decoder.
EXISTS = "EXISTS"
ABSENT = "ABSENT"
UNVERIFIED = "UNVERIFIED"

GOVINFO_LINK = "https://www.govinfo.gov/link"
GOVINFO_BULK = "https://www.govinfo.gov/bulkdata"
ECFR_API = "https://www.ecfr.gov/api/versioner/v1"
OLRC = "https://uscode.house.gov"

#: Source identifiers recorded on every result. Stable, because they appear in
#: reports and a record naming a source that later changes name is not a record.
SRC_LINK = "govinfo_link_service"
SRC_BULK = "govinfo_bulk_data"
SRC_ECFR_VERSIONS = "ecfr_versions"
SRC_ECFR_FULL = "ecfr_full_text"
SRC_USLM = "uscode_house_uslm"

#: These are free services run at public expense. The limiter is deliberately
#: gentler than anything they publish, because none of them publishes one and
#: the correct response to an unknown ceiling is not to go looking for it.
_MIN_INTERVAL_S = 0.4
_TIMEOUT_S = 30
#: A title is roughly 18 MB and the publisher serves it slowly. This is a
#: deliberate download, not a lookup, so it gets its own budget.
_DOWNLOAD_TIMEOUT_S = 600

#: eCFR's history begins here. A date before it is out of range, not absent.
ECFR_EPOCH = "2017-01-01"

_UA = "VeraScite citation verification (+https://github.com/rakib-nyc/verascite)"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _age_days(stamp: str) -> float:
    try:
        when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return 1e9
    return (datetime.now(timezone.utc) - when).total_seconds() / 86400.0


@dataclass
class SourceResult:
    """One lookup against one source, with everything a report needs to cite it."""

    state: str
    detail: str
    sources_consulted: list[str] = field(default_factory=list)
    url: str = ""
    retrieved_at: str = field(default_factory=_utcnow)
    #: Set when the source cannot support a confident answer even though it
    #: gave one -- the flat CFR text, above all.
    confidence: str = "high"
    #: What the source says the provision's currency is, when it says.
    as_of: str = ""

    @property
    def affirmative(self) -> bool:
        return self.state in (EXISTS, ABSENT)

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v not in ("", [], None)}


def unverified(detail: str, sources: list[str], url: str = "",
               as_of: str = "") -> SourceResult:
    """The safe answer. Used for every failure mode without exception."""
    return SourceResult(UNVERIFIED, detail, list(sources), url, as_of=as_of)


class _Cache:
    """Content-addressed disk cache, mirroring the CourtListener client's rules.

    A positive result about a published provision is a fact about a fixed
    document and keeps indefinitely. A negative or a failure does not: sources
    are amended, and a cached ABSENT that outlives its accuracy becomes a false
    accusation, which is the expensive error here.
    """

    def __init__(self, directory: Optional[Path]):
        self.directory = Path(directory) / "statutes" if directory else None
        if self.directory:
            self.directory.mkdir(parents=True, exist_ok=True)
        self.hits = 0
        self.misses = 0

    def _path(self, key: str) -> Optional[Path]:
        if not self.directory:
            return None
        return self.directory / (hashlib.sha256(key.encode()).hexdigest()[:24] + ".json")

    def get(self, key: str) -> Optional[SourceResult]:
        path = self._path(key)
        if not path or not path.exists():
            self.misses += 1
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            result = SourceResult(**data)
        except (json.JSONDecodeError, TypeError, OSError):
            self.misses += 1
            return None
        if result.state != EXISTS and _age_days(result.retrieved_at) > NEGATIVE_RESULT_TTL_DAYS:
            self.misses += 1
            return None
        self.hits += 1
        return result

    def put(self, key: str, result: SourceResult) -> None:
        path = self._path(key)
        if path:
            try:
                path.write_text(json.dumps(result.to_dict()), encoding="utf-8")
            except OSError:
                pass  # a cache that cannot write is slow, not broken


class StatuteClient:
    """Lookups against the free federal sources."""

    def __init__(self, cache_dir: Optional[Path] = None,
                 session: Optional[requests.Session] = None,
                 allow_title_download: bool = False):
        self.cache = _Cache(cache_dir)
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": _UA})
        #: Downloading a US Code title is an 18 MB transfer, so it never
        #: happens implicitly. Without it, subsection checking reports
        #: UNVERIFIED and says why, which is the honest degradation.
        self.allow_title_download = allow_title_download
        self.title_dir = Path(cache_dir) / "uscode-titles" if cache_dir else None
        self._last_request = 0.0
        self.stats = {"requests": 0, "failures": 0}

    # --- transport ------------------------------------------------------

    def _pace(self) -> None:
        wait = _MIN_INTERVAL_S - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)

    def _get(self, url: str, *, params: dict | None = None,
             allow_redirects: bool = True, headers: dict | None = None,
             timeout: int = _TIMEOUT_S):
        self._pace()
        self.stats["requests"] += 1
        try:
            response = self.session.get(
                url, params=params, timeout=timeout,
                allow_redirects=allow_redirects, headers=headers or {},
            )
        except requests.RequestException as exc:
            self.stats["failures"] += 1
            return None, str(exc)
        finally:
            self._last_request = time.monotonic()
        return response, ""

    # --- the link service: a clean existence oracle ---------------------

    def _link(self, path: str, params: dict, label: str) -> SourceResult:
        """A 302 means the provision resolves; a 400 means it does not.

        Redirects are not followed: the status *is* the answer, and following
        it would replace a two-state signal with whatever the target happens to
        return.
        """
        url = f"{GOVINFO_LINK}/{path}"
        response, error = self._get(url, params=params, allow_redirects=False)
        if response is None:
            return unverified(
                f"{label} could not be checked: the link service was unreachable "
                f"({error})", [SRC_LINK], url)
        if response.status_code in (301, 302, 303, 307, 308):
            return SourceResult(
                EXISTS, f"{label} resolves in the government's own link service",
                [SRC_LINK], response.headers.get("Location", url))
        if response.status_code == 400:
            return SourceResult(
                ABSENT,
                f"the link service reports no {label}. This is an affirmative "
                "answer from the publisher of the text, not a failure to find one",
                [SRC_LINK], url)
        return unverified(
            f"{label} could not be checked: the link service answered "
            f"HTTP {response.status_code}, which is neither a resolution nor a "
            "report of absence", [SRC_LINK], url)

    def usc_section(self, title: str, section: str,
                    year: Optional[str] = None) -> SourceResult:
        """Whether a US Code section exists, optionally as of a year."""
        key = f"usc:{title}:{section}:{year or 'current'}"
        cached = self.cache.get(key)
        if cached:
            return cached
        params = {"year": year} if year else {}
        result = self._link(f"uscode/{title}/{section}", params,
                            f"{title} U.S.C. § {section}")
        if year:
            result.as_of = year
        self.cache.put(key, result)
        return result

    def cfr_section(self, title: str, part: str, section: str,
                    year: Optional[str] = None) -> SourceResult:
        """Whether a CFR section exists.

        Tries the eCFR versions endpoint first because it answers for the
        current text and reports absence unambiguously, then falls back to the
        annual editions in the link service, which reach back to 1996 and are
        the only option for a date before eCFR's history begins.
        """
        key = f"cfr:{title}:{part}:{section}:{year or 'current'}"
        cached = self.cache.get(key)
        if cached:
            return cached
        result = self._ecfr_versions(title, part, section)
        if not result.affirmative:
            fallback = self._link(
                f"cfr/{title}/{part}",
                {"sectionnum": section.split(".", 1)[-1], "year": year or _this_year()},
                f"{title} C.F.R. § {section}")
            if fallback.affirmative:
                fallback.sources_consulted = [SRC_ECFR_VERSIONS] + fallback.sources_consulted
                result = fallback
        if year:
            result.as_of = year
        self.cache.put(key, result)
        return result

    def _ecfr_versions(self, title: str, part: str, section: str) -> SourceResult:
        """The cleanest live existence oracle available for regulations.

        A fabricated section returns HTTP 200 with an empty ``content_versions``
        array. A 200 with an empty array is an affirmative statement that the
        section is not in the title; any non-200 is a reachability failure and
        says nothing about the section.
        """
        url = f"{ECFR_API}/versions/title-{title}.json"
        response, error = self._get(url, params={"part": part, "section": section})
        if response is None:
            return unverified(
                f"{title} C.F.R. § {section} could not be checked: the regulation "
                f"service was unreachable ({error})", [SRC_ECFR_VERSIONS], url)
        if response.status_code != 200:
            return unverified(
                f"{title} C.F.R. § {section} could not be checked: the regulation "
                f"service answered HTTP {response.status_code}",
                [SRC_ECFR_VERSIONS], url)
        try:
            payload = response.json()
        except ValueError:
            return unverified(
                "the regulation service returned a body that could not be read "
                "as JSON", [SRC_ECFR_VERSIONS], url)
        versions = payload.get("content_versions")
        if not isinstance(versions, list):
            return unverified(
                "the regulation service returned an unrecognised body",
                [SRC_ECFR_VERSIONS], url)
        if versions:
            latest = versions[-1].get("date", "")
            return SourceResult(
                EXISTS,
                f"{title} C.F.R. § {section} is in the current regulations, with "
                f"{len(versions)} recorded version(s)",
                [SRC_ECFR_VERSIONS], url, as_of=latest)
        return SourceResult(
            ABSENT,
            f"the regulation service holds no section {section} in title {title}. "
            "It answered successfully with an empty result, which is a report of "
            "absence rather than a failure to answer",
            [SRC_ECFR_VERSIONS], url)

    def public_law(self, congress: str, number: str) -> SourceResult:
        key = f"plaw:{congress}:{number}"
        cached = self.cache.get(key)
        if cached:
            return cached
        result = self._link(f"plaw/{congress}/public/{number}", {},
                            f"Public Law {congress}-{number}")
        self.cache.put(key, result)
        return result

    def statute_at_large(self, volume: str, page: str) -> SourceResult:
        key = f"stat:{volume}:{page}"
        cached = self.cache.get(key)
        if cached:
            return cached
        result = self._link(f"statute/{volume}/{page}", {},
                            f"{volume} Stat. {page}")
        self.cache.put(key, result)
        return result

    # --- subsections ----------------------------------------------------

    def usc_subsection(self, title: str, section: str,
                       path: list[str]) -> SourceResult:
        """Whether a subsection exists inside a US Code section.

        The highest-value check in this module and the only one with a properly
        structural source. A fabricated subsection of a real statute is common,
        damaging, and nearly invisible to a human reviewer, because everything
        before the parenthesis is correct.
        """
        if not path:
            return unverified("no subsection was named", [])
        identifier = "/us/usc/t{}/s{}/{}".format(title, section, "/".join(path))
        cited = f"{title} U.S.C. § {section}" + "".join(f"({p})" for p in path)

        local = self._title_file(title)
        if local is None:
            return unverified(
                f"the subsection cited in {cited} was not checked: the structural "
                "text of title " + str(title) + " was not available locally, and "
                "it is the only source that encodes subsection structure. The "
                "section itself may still have been checked",
                [SRC_USLM])
        try:
            body = local.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return unverified(
                f"the local copy of title {title} could not be read ({exc})",
                [SRC_USLM])

        release = _release_point(body)
        if f'identifier="{identifier}"' in body:
            return SourceResult(
                EXISTS,
                f"{cited} is present in the official structural text of title "
                f"{title}",
                [SRC_USLM], as_of=release)
        # Only meaningful if the *section* is there. A missing section makes a
        # missing subsection uninformative, and reporting it would double-count
        # one problem as two.
        if f'identifier="/us/usc/t{title}/s{section}"' not in body:
            return unverified(
                f"the subsection cited in {cited} was not checked, because "
                f"section {section} itself was not located in the structural text",
                [SRC_USLM], as_of=release)
        return SourceResult(
            ABSENT,
            f"section {section} of title {title} is present in the official "
            f"structural text and does not contain the subsection the citation "
            f"names. Checked against release point {release or 'unrecorded'}",
            [SRC_USLM], as_of=release)

    def cfr_subsection(self, title: str, part: str, section: str,
                       path: list[str], date: Optional[str] = None) -> SourceResult:
        """Whether a regulation contains the paragraph a citation names.

        Deliberately weaker than its US Code counterpart, and it says so in
        every result. The regulation text is flat: paragraph designators are
        text at the head of a paragraph element and the hierarchy is implied by
        the order they appear in, not encoded. A designator can also be run
        into the same paragraph as its parent, so a naive scan misses it. The
        answer here is therefore never better than ``medium`` confidence, and
        an absence is never reported at all -- only a positive location.
        """
        if not path:
            return unverified("no paragraph was named", [])
        cited = f"{title} C.F.R. § {section}" + "".join(f"({p})" for p in path)
        when = date or ECFR_EPOCH
        url = f"{ECFR_API}/full/{when}/title-{title}.xml"
        response, error = self._get(
            url, params={"part": part, "section": section},
            headers={"Accept-Encoding": "gzip"})
        if response is None or response.status_code != 200:
            # A 404 here means the section is absent OR the date is out of
            # range, and the two are indistinguishable. Never ABSENT.
            code = response.status_code if response is not None else error
            return unverified(
                f"the paragraph cited in {cited} was not checked: the regulation "
                f"text service answered {code}. Note that this service cannot "
                "distinguish a section that does not exist from one requested "
                "outside its recorded history, so no conclusion is drawn",
                [SRC_ECFR_FULL], url)
        designators = _cfr_designators(response.text)
        if _path_present(designators, path):
            return SourceResult(
                EXISTS,
                f"the paragraph designators in {title} C.F.R. § {section} include "
                f"the one cited. Regulation text is not structurally encoded, so "
                "this is a reading of the printed designators rather than a "
                "structural lookup",
                [SRC_ECFR_FULL], url, confidence="medium", as_of=when)
        return unverified(
            f"the paragraph cited in {cited} was not located among the "
            f"designators printed in the section. Regulation text is flat rather "
            "than structured, so a designator can be missed by this reading; "
            "absence here is not evidence the paragraph does not exist",
            [SRC_ECFR_FULL], url)

    # --- the structural title file --------------------------------------

    def _title_file(self, title: str) -> Optional[Path]:
        """A cached US Code title, downloading it only if explicitly allowed."""
        if not self.title_dir:
            return None
        self.title_dir.mkdir(parents=True, exist_ok=True)
        padded = str(title).zfill(2)
        existing = sorted(self.title_dir.glob(f"usc{padded}@*.xml"))
        if existing:
            return existing[-1]
        if not self.allow_title_download:
            return None
        return self._download_title(title)

    def _download_title(self, title: str) -> Optional[Path]:
        release = self.current_release_point()
        if not release:
            return None
        congress, law = release
        padded = str(title).zfill(2)
        url = (f"{OLRC}/download/releasepoints/us/pl/{congress}/{law}/"
               f"xml_usc{padded}@{congress}-{law}.zip")
        response, _ = self._get(url, timeout=_DOWNLOAD_TIMEOUT_S)
        if response is None or response.status_code != 200:
            return None
        archive = self.title_dir / f"usc{padded}@{congress}-{law}.zip"
        try:
            archive.write_bytes(response.content)
            with zipfile.ZipFile(archive) as zf:
                names = [n for n in zf.namelist() if n.endswith(".xml")]
                if not names:
                    return None
                target = self.title_dir / f"usc{padded}@{congress}-{law}.xml"
                target.write_bytes(zf.read(names[0]))
        except (OSError, zipfile.BadZipFile):
            return None
        finally:
            archive.unlink(missing_ok=True)
        return target

    def current_release_point(self) -> Optional[tuple[str, str]]:
        """The Public Law the Code is current through, read from the publisher."""
        response, _ = self._get(f"{OLRC}/currency/currency.shtml")
        if response is None or response.status_code != 200:
            return None
        match = re.search(r"Public Law (\d{1,3})[-–](\d{1,4})", response.text)
        return (match.group(1), match.group(2)) if match else None


def _this_year() -> str:
    return str(datetime.now(timezone.utc).year)


def _release_point(body: str) -> str:
    """The release point a title file declares about itself.

    Read from the artefact rather than tracked alongside it, so a cached file
    can never be described by a release point it does not actually carry.
    """
    match = re.search(r"<docPublicationName>([^<]+)</docPublicationName>", body)
    return match.group(1).strip() if match else ""


#: A designator at the head of a printed paragraph. Regulation text runs a
#: child designator into its parent's paragraph, so this scans the text rather
#: than the element boundaries.
_DESIGNATOR_RUN = re.compile(r"\(\s*([A-Za-z0-9]{1,4})\s*\)")


def _cfr_designators(xml: str) -> list[str]:
    text = re.sub(r"<[^>]+>", " ", xml)
    return _DESIGNATOR_RUN.findall(text)


def _path_present(designators: list[str], path: list[str]) -> bool:
    """Whether the designators appear in the cited order somewhere in the text.

    Order rather than containment, because ``(d)(1)`` asserts that paragraph 1
    sits under paragraph d. Containment alone would accept a document that
    printed both designators in unrelated places.
    """
    remaining = list(path)
    for token in designators:
        if remaining and token.lower() == remaining[0].lower():
            remaining.pop(0)
            if not remaining:
                return True
    return False
