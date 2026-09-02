"""CourtListener Citation Lookup client.

Contract verified against the live docs (wiki.free.law, Aug. 2026):

* ``POST /api/rest/v4/citation-lookup/``
* ``Authorization: Token <token>``
* text mode (<=64,000 chars) or structured ``volume``/``reporter``/``page``
* at most 250 citations per request; 60 valid citations per minute
* per-citation ``status``: 200 found, 404 valid-but-absent, 400 bad reporter,
  300 ambiguous, 429 throttled
* a throttled response carries ``wait_util``, an ISO-8601 timestamp
  (the field name is spelled that way by the API; it is not a typo here)

**Privacy: this client never transmits document text.** The spec's chunking
advice assumes posting the brief itself, but documents may be privileged
(spec 9). Instead the client builds a *synthetic* payload containing only the
distinct normalized citation strings, separated by blank lines::

    "410 U.S. 113\\n\\n997 F.2d 762\\n\\n678 F. Supp. 3d 443"

This keeps the 250-citations-per-request efficiency that Free Law Project's
rate limits are designed around, gives an exact offset mapping back to a
string we constructed ourselves, and discloses nothing but the citations.
"""

from __future__ import annotations

import hashlib
import json
import re
import os
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

import requests

from ..config import (
    NEGATIVE_RESULT_TTL_DAYS,
    OPINION_REQUESTS_PER_MINUTE,
    REQUESTS_PER_MINUTE,
)

API_ROOT = "https://www.courtlistener.com/api/rest/v4"
CITATION_LOOKUP_URL = f"{API_ROOT}/citation-lookup/"
DOCKETS_URL = f"{API_ROOT}/dockets"

MAX_CITATIONS_PER_REQUEST = 250
MAX_TEXT_CHARS = 64_000
CITATIONS_PER_MINUTE = 60

SOURCE_NAME = "courtlistener_citation_lookup"
COURT_SOURCE_NAME = "courtlistener_docket"

#: Sentinel for "the endpoint returned nothing about this citation".
#: Deliberately not 404: we did not learn that it is absent.
STATUS_NO_RESPONSE = -1

#: Reporters that structurally cannot be resolved by CourtListener. Detecting
#: these up front avoids burning budget on a lookup that cannot succeed, and --
#: more importantly -- routes them to OUT_OF_SCOPE rather than NOT_FOUND
#: (spec 7.3, "known-gap detection").
VENDOR_ONLY_REPORTERS = {
    "wl": "Westlaw-only identifier",
    "u.s. app. lexis": "Lexis-only identifier",
    "u.s. dist. lexis": "Lexis-only identifier",
    "lexis": "Lexis-only identifier",
    "westlaw": "Westlaw-only identifier",
}


def _age_days(timestamp: Optional[str]) -> float:
    """Age of a cached record in days. Unparseable timestamps read as ancient."""
    if not timestamp:
        return float("inf")
    try:
        stamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return float("inf")
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - stamp).total_seconds() / 86400.0


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class CourtListenerError(RuntimeError):
    pass


class MissingToken(CourtListenerError):
    pass


def _clean_clusters(raw) -> list[dict]:
    """Keep only cluster records that are actually records.

    A response that is not the shape the docs promise must not become a
    verdict. Left unchecked, a cluster arriving as a string crashes the
    metadata comparison, and one whose case_name is a nested object crashes
    the name normaliser -- and a partially-garbled record can produce a
    FLAGGED citation, which is the harshest thing this tool says, on the
    strength of data it should not have trusted.
    """
    cleaned: list[dict] = []
    for entry in raw or []:
        if not isinstance(entry, dict):
            continue
        record = dict(entry)
        for field_name in ("case_name", "case_name_full", "case_name_short",
                           "absolute_url",
                           "date_filed", "precedential_status", "court_id"):
            value = record.get(field_name)
            if value is not None and not isinstance(value, str):
                record[field_name] = None
        cluster_id = record.get("id")
        record["id"] = cluster_id if isinstance(cluster_id, int) else None
        docket_id = record.get("docket_id")
        record["docket_id"] = docket_id if isinstance(docket_id, int) else None
        cleaned.append(record)
    return cleaned


@dataclass
class LookupResult:
    """One citation's lookup outcome, cache-round-trippable."""

    citation: str
    status: int
    normalized_citations: list[str] = field(default_factory=list)
    clusters: list[dict] = field(default_factory=list)
    error_message: Optional[str] = None
    retrieved_at: str = field(default_factory=utcnow)
    from_cache: bool = False

    def __post_init__(self) -> None:
        self.clusters = _clean_clusters(self.clusters)
        try:
            self.status = int(self.status)
        except (TypeError, ValueError):
            self.status = STATUS_NO_RESPONSE

    def to_dict(self) -> dict:
        return {
            "citation": self.citation,
            "status": self.status,
            "normalized_citations": self.normalized_citations,
            "clusters": self.clusters,
            "error_message": self.error_message,
            "retrieved_at": self.retrieved_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LookupResult":
        return cls(
            citation=d["citation"],
            status=int(d["status"]),
            normalized_citations=[
                x for x in (d.get("normalized_citations") or []) if isinstance(x, str)
            ],
            clusters=_clean_clusters(d.get("clusters")),
            error_message=d.get("error_message"),
            retrieved_at=d.get("retrieved_at", utcnow()),
        )


class _TokenBucket:
    """Sliding-window limiter over *citations*, which is what CL meters.

    Both the clock and the sleep are injectable. A rate limiter that can only
    be exercised by actually waiting a minute is a rate limiter nobody tests,
    and this one guards a small nonprofit's infrastructure.
    """

    def __init__(
        self,
        capacity: int = CITATIONS_PER_MINUTE,
        window: float = 60.0,
        clock=time.monotonic,
        sleeper=time.sleep,
    ):
        self.capacity = capacity
        self.window = window
        self.clock = clock
        self.sleeper = sleeper
        self._events: deque[tuple[float, int]] = deque()

    def _spent(self, now: float) -> int:
        while self._events and now - self._events[0][0] >= self.window:
            self._events.popleft()
        return sum(count for _, count in self._events)

    def acquire(self, count: int) -> float:
        waited = 0.0
        while True:
            now = self.clock()
            if self._spent(now) + count <= self.capacity or not self._events:
                self._events.append((now, count))
                return waited
            delay = self.window - (now - self._events[0][0]) + 0.05
            delay = max(delay, 0.0)
            before = self.clock()
            self.sleeper(delay)
            # A sleeper that does not advance the clock would spin forever.
            if self.clock() <= before:
                self._events.clear()
                return waited + delay
            waited += delay


class _DiskCache:
    """Content-addressed cache keyed on the normalized citation string."""

    def __init__(self, directory: Optional[Path]):
        self.directory = Path(directory) if directory else None
        if self.directory:
            self.directory.mkdir(parents=True, exist_ok=True)
        self.hits = 0
        self.misses = 0
        self.expired = 0
        self.oldest_hit: Optional[str] = None

    def _path(self, citation: str) -> Optional[Path]:
        if not self.directory:
            return None
        digest = hashlib.sha256(citation.strip().lower().encode("utf-8")).hexdigest()[:24]
        return self.directory / f"{digest}.json"

    def get(self, citation: str) -> Optional[LookupResult]:
        path = self._path(citation)
        if not path or not path.exists():
            self.misses += 1
            return None
        try:
            result = LookupResult.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, KeyError, OSError):
            self.misses += 1
            return None

        # A negative result expires; a positive one does not. "Not in the
        # database" is true of a moment, and CourtListener ingests
        # continuously, so a stale 404 becomes a false UNVERIFIED.
        if result.status in (404, 300) and _age_days(result.retrieved_at) > NEGATIVE_RESULT_TTL_DAYS:
            self.misses += 1
            self.expired += 1
            return None

        result.from_cache = True
        self.hits += 1
        if self.oldest_hit is None or result.retrieved_at < self.oldest_hit:
            self.oldest_hit = result.retrieved_at
        return result

    def put(self, result: LookupResult) -> None:
        path = self._path(result.citation)
        if not path:
            return
        # A throttled or empty result is a statement about the request, not
        # about the citation. Caching it would make a transient failure
        # permanent.
        if result.status in (429, STATUS_NO_RESPONSE):
            return
        path.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")


class CourtListenerClient:
    """Rate-limited, cached client for the citation-lookup endpoint."""

    def __init__(
        self,
        token: Optional[str] = None,
        cache_dir: Optional[Path] = None,
        timeout: float = 60.0,
        session: Optional[requests.Session] = None,
        sleeper=time.sleep,
        clock=time.monotonic,
        cache_only: bool = False,
        user_agent: str = "verascite/0.1 (+https://github.com/; legal citation verification)",
    ):
        self.token = token or os.environ.get("COURTLISTENER_API_TOKEN") or ""
        #: Serve from cache and never touch the network. A miss becomes an
        #: explicit "not looked up" rather than a guess or an exception, which
        #: is what lets a run proceed honestly when a quota is exhausted.
        self.cache_only = cache_only
        self.timeout = timeout
        self.session = session or requests.Session()
        self.sleeper = sleeper
        self.user_agent = user_agent
        self.cache = _DiskCache(cache_dir)
        # A decided case's court never changes, so this cache never expires.
        self.court_cache = _DiskCache(
            Path(cache_dir) / "courts" if cache_dir else None
        )
        # Two independent limits, and only the first is documented.
        #
        # citation_bucket meters CITATIONS (60/min) and applies to lookups.
        # request_bucket meters REQUESTS (5/min) and applies to everything --
        # citation-lookup included. Metering citations alone was a bug: ten
        # single-citation lookups are throttled at the fifth request despite
        # being nowhere near the citation ceiling, which crashed a benchmark
        # run four minutes in.
        self.bucket = _TokenBucket(clock=clock, sleeper=self.sleeper)
        self.request_bucket = _TokenBucket(
            capacity=REQUESTS_PER_MINUTE, window=60.0,
            clock=clock, sleeper=self.sleeper,
        )
        #: Retained name; every endpoint shares the one request budget.
        self.detail_bucket = self.request_bucket
        self.stats = {"requests": 0, "citations_looked_up": 0, "throttle_waits": 0}

    @property
    def has_token(self) -> bool:
        return bool(self.token)

    @property
    def can_serve(self) -> bool:
        """Can this client answer a lookup at all?

        Cache-only mode needs no credential: it never makes a request. Gating
        on has_token alone silently degrades a cache-served run to offline,
        which looks like a legitimate result and is not one.
        """
        return self.has_token or self.cache_only

    def require_token(self) -> None:
        if self.cache_only:
            return
        if not self.has_token:
            raise MissingToken(
                "COURTLISTENER_API_TOKEN is not set. Create a free CourtListener "
                "account at https://www.courtlistener.com/sign-in/ and generate a "
                "token, or run with --offline to use the local deterministic "
                "checks only."
            )

    # --- request plumbing ------------------------------------------------

    @staticmethod
    def is_vendor_only(reporter: Optional[str]) -> Optional[str]:
        """Return a reason string when a reporter is structurally unresolvable."""
        if not reporter:
            return None
        key = reporter.strip().lower().rstrip(".")
        direct = VENDOR_ONLY_REPORTERS.get(key)
        if direct:
            return direct
        # Lexis publishes many series -- "U.S. Dist. LEXIS", "DOLWH LEXIS" --
        # all equally absent from every free source.
        if "lexis" in key:
            return "Lexis-only identifier"
        if key.endswith(" wl") or key.startswith("wl "):
            return "Westlaw-only identifier"
        return None

    @staticmethod
    def _batch(citations: list[str]) -> Iterable[list[str]]:
        """Chunk by both the 250-citation cap and the 64,000-character cap."""
        batch: list[str] = []
        size = 0
        for citation in citations:
            addition = len(citation) + 2
            if batch and (
                len(batch) >= MAX_CITATIONS_PER_REQUEST or size + addition > MAX_TEXT_CHARS
            ):
                yield batch
                batch, size = [], 0
            batch.append(citation)
            size += addition
        if batch:
            yield batch

    def _wait_for(self, wait_util: Optional[str]) -> None:
        """Honor the server's own recovery timestamp rather than guessing."""
        self.stats["throttle_waits"] += 1
        delay = 5.0
        if wait_util:
            try:
                target = datetime.fromisoformat(wait_util.replace("Z", "+00:00"))
                delay = (target - datetime.now(timezone.utc)).total_seconds() + 1.0
            except ValueError:
                pass
        self.sleeper(max(0.0, min(delay, 300.0)))

    def _post(self, payload_text: str, citation_count: int) -> list[dict]:
        self.require_token()
        self.bucket.acquire(citation_count)
        self.request_bucket.acquire(1)
        headers = {
            "Authorization": f"Token {self.token}",
            "User-Agent": self.user_agent,
        }
        for attempt in range(4):
            response = self.session.post(
                CITATION_LOOKUP_URL,
                data={"text": payload_text},
                headers=headers,
                timeout=self.timeout,
            )
            self.stats["requests"] += 1

            if response.status_code == 429:
                self._wait_for(
                    response.headers.get("wait_util")
                    or (response.json().get("wait_util") if _is_json(response) else None)
                )
                continue
            if response.status_code in (502, 503, 504):
                self.sleeper(min(2**attempt, 30))
                continue
            if response.status_code == 401:
                raise CourtListenerError(
                    "CourtListener rejected the API token (401). Check "
                    "COURTLISTENER_API_TOKEN."
                )
            if response.status_code >= 400:
                raise CourtListenerError(
                    f"CourtListener returned HTTP {response.status_code}: "
                    f"{response.text[:300]}"
                )
            return response.json()

        raise CourtListenerError(
            "CourtListener remained throttled or unavailable after 4 attempts. "
            "No verdict is inferred from this (P6): affected citations stay "
            "unverified with the reason recorded."
        )

    # --- public API ------------------------------------------------------

    def lookup(self, citations: list[str]) -> dict[str, LookupResult]:
        """Look up distinct normalized citations. Returns {citation: result}.

        Cache hits never leave the machine. Only misses are batched into a
        synthetic citations-only payload and posted.
        """
        out: dict[str, LookupResult] = {}
        pending: list[str] = []
        for citation in dict.fromkeys(c.strip() for c in citations if c and c.strip()):
            cached = self.cache.get(citation)
            if cached:
                out[citation] = cached
            else:
                pending.append(citation)

        if self.cache_only:
            for citation in pending:
                out[citation] = LookupResult(
                    citation=citation,
                    status=STATUS_NO_RESPONSE,
                    error_message=(
                        "cache-only mode: this citation was not in the local "
                        "cache and no request was made"
                    ),
                )
            self.stats["cache_only_misses"] = self.stats.get(
                "cache_only_misses", 0
            ) + len(pending)
            return out

        for batch in self._batch(pending):
            payload = "\n\n".join(batch)
            results = self._post(payload, len(batch))
            self.stats["citations_looked_up"] += len(batch)

            by_citation: dict[str, dict] = {}
            for item in results:
                key = (item.get("citation") or "").strip()
                by_citation.setdefault(key, item)

            for citation in batch:
                item = by_citation.get(citation)
                if item is None:
                    # The endpoint did not echo this string back. That is an
                    # unknown outcome, not a finding -- inferring a rejection
                    # here would let a transport quirk accuse a real citation.
                    result = LookupResult(
                        citation=citation,
                        status=STATUS_NO_RESPONSE,
                        error_message=(
                            "CourtListener did not return a result for this "
                            "citation string"
                        ),
                    )
                else:
                    result = LookupResult(
                        citation=citation,
                        status=int(item.get("status", 404)),
                        normalized_citations=[
                            x for x in (item.get("normalized_citations") or [])
                            if isinstance(x, str)
                        ],
                        clusters=_clean_clusters(item.get("clusters")),
                        error_message=item.get("error_message"),
                    )
                self.cache.put(result)
                out[citation] = result

        return out


    def fetch_court(self, docket_id: Optional[int]) -> Optional[str]:
        """Resolve a cluster's court id via its docket.

        The citation-lookup response carries no court field -- ``court`` and
        ``court_id`` are both null on every cluster it returns -- so the court
        dimension is unverifiable from that endpoint alone. The docket has it,
        and ``?fields=court_id`` keeps the response tiny.

        Returns None on any failure. A court we could not fetch is reported
        NOT_CHECKABLE upstream; it never becomes a verdict.
        """
        if not docket_id:
            return None

        cache_key = f"docket:{docket_id}"
        cached = self.court_cache.get(cache_key)
        if cached is not None:
            return cached.normalized_citations[0] if cached.normalized_citations else None

        if not self.has_token:
            return None

        court_id = None
        for attempt in range(3):
            self.request_bucket.acquire(1)
            try:
                response = self.session.get(
                    f"{DOCKETS_URL}/{docket_id}/",
                    params={"fields": "court_id"},
                    headers={
                        "Authorization": f"Token {self.token}",
                        "User-Agent": self.user_agent,
                    },
                    timeout=self.timeout,
                )
                self.stats["requests"] += 1
            except requests.RequestException:
                self.sleeper(min(2**attempt, 20))
                continue

            if response.status_code == 429:
                self.stats["throttle_waits"] += 1
                self.sleeper(_detail_retry_delay(response))
                continue
            if response.status_code != 200:
                return None
            try:
                court_id = response.json().get("court_id")
            except ValueError:
                return None
            break
        else:
            return None

        self.court_cache.put(_court_result(docket_id, court_id))
        return court_id


_DETAIL_WAIT_RE = re.compile(r"available in (\d+) second", re.IGNORECASE)


def _detail_retry_delay(response) -> float:
    header = response.headers.get("Retry-After")
    if header and str(header).isdigit():
        return min(float(header) + 1.0, 300.0)
    try:
        match = _DETAIL_WAIT_RE.search(response.json().get("detail", ""))
        if match:
            return min(float(match.group(1)) + 1.0, 300.0)
    except (ValueError, AttributeError):
        pass
    return 30.0


def _court_result(docket_id: int, court_id: Optional[str]) -> LookupResult:
    """Reuse LookupResult as the cache record for a docket's court."""
    return LookupResult(
        citation=f"docket:{docket_id}",
        status=200 if court_id else 404,
        normalized_citations=[court_id] if court_id else [],
    )


def _is_json(response) -> bool:
    return "application/json" in (response.headers.get("Content-Type") or "")
