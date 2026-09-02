"""CourtListener client: batching, throttling, caching, and status mapping.

No live calls. The point of these tests is that the code which decides how
hard to hit a small nonprofit's servers -- and which turns HTTP statuses into
accusations about a lawyer's brief -- is exercised without a network.
"""

from __future__ import annotations

import json

import pytest

from verascite.clients.courtlistener import (
    MAX_CITATIONS_PER_REQUEST,
    MAX_TEXT_CHARS,
    CourtListenerClient,
    CourtListenerError,
    LookupResult,
    MissingToken,
    _TokenBucket,
)
from verascite.ledger import Ledger
from verascite.models import CiteKind, Document
from verascite.extract import extract
from verascite.resolve import resolve
from verascite.verdicts import Overall, Verdict
from verascite.verify_existence import verify_existence


class FakeClock:
    def __init__(self, start: float = 1000.0):
        self.t = start

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else []
        self.headers = headers or {"Content-Type": "application/json"}
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, data=None, headers=None, timeout=None):
        self.calls.append({"url": url, "data": data, "headers": headers})
        return self.responses.pop(0)


def client_with(responses, **kwargs):
    clock = FakeClock()
    return (
        CourtListenerClient(
            token="test-token",
            session=FakeSession(responses),
            sleeper=clock.sleep,
            clock=clock,
            **kwargs,
        ),
        clock,
    )


# --- rate limiting ------------------------------------------------------------


def test_token_bucket_meters_citations_not_requests():
    clock = FakeClock()
    bucket = _TokenBucket(clock=clock, sleeper=clock.sleep)
    assert bucket.acquire(60) == 0.0
    assert bucket.acquire(1) == pytest.approx(60.05)
    assert bucket.acquire(1) == 0.0


def test_token_bucket_terminates_when_the_clock_does_not_advance():
    """A sleeper that does not move time must not spin forever."""
    bucket = _TokenBucket(clock=lambda: 5.0, sleeper=lambda _: None)
    bucket.acquire(60)
    assert bucket.acquire(1) == pytest.approx(60.05)


def test_batches_respect_both_the_citation_and_character_caps():
    client, _ = client_with([])
    citations = [f"{i} F.2d 100" for i in range(1, 601)]
    assert [len(b) for b in client._batch(citations)] == [250, 250, 100]

    long_cites = ["X" * 5000] * 30
    for batch in client._batch(long_cites):
        assert len(batch) <= MAX_CITATIONS_PER_REQUEST
        assert sum(len(c) + 2 for c in batch) <= MAX_TEXT_CHARS


def test_throttle_response_honors_the_servers_wait_util():
    from datetime import datetime, timedelta, timezone

    resume = (datetime.now(timezone.utc) + timedelta(seconds=42)).isoformat().replace(
        "+00:00", "Z"
    )
    responses = [
        FakeResponse(429, {"wait_util": resume}),
        FakeResponse(200, [{"citation": "1 F.2d 1", "status": 200, "clusters": []}]),
    ]
    client, clock = client_with(responses)
    before = clock.t
    client.lookup(["1 F.2d 1"])
    assert clock.t - before > 40
    assert client.stats["throttle_waits"] == 1


def test_persistent_throttling_raises_rather_than_inventing_a_verdict():
    client, _ = client_with([FakeResponse(429, {})] * 4)
    with pytest.raises(CourtListenerError, match="No verdict is inferred"):
        client.lookup(["1 F.2d 1"])


def test_missing_token_is_an_explicit_error(monkeypatch):
    monkeypatch.delenv("COURTLISTENER_API_TOKEN", raising=False)
    client = CourtListenerClient(token="")
    with pytest.raises(MissingToken, match="COURTLISTENER_API_TOKEN"):
        client.lookup(["1 F.2d 1"])


# --- privacy ------------------------------------------------------------------


def test_only_citation_strings_are_transmitted():
    """Documents may be privileged; the payload must carry no prose."""
    responses = [
        FakeResponse(
            200,
            [
                {"citation": "457 U.S. 800", "status": 200, "clusters": []},
                {"citation": "550 U.S. 544", "status": 200, "clusters": []},
            ],
        )
    ]
    client, _ = client_with(responses)
    client.lookup(["457 U.S. 800", "550 U.S. 544"])
    sent = client.session.calls[0]["data"]["text"]
    assert sent == "457 U.S. 800\n\n550 U.S. 544"
    assert "v." not in sent


# --- caching ------------------------------------------------------------------


def test_cache_prevents_a_second_request(tmp_path):
    payload = [{"citation": "457 U.S. 800", "status": 200, "clusters": [{"id": 1}]}]
    client, _ = client_with([FakeResponse(200, payload)], cache_dir=tmp_path)
    first = client.lookup(["457 U.S. 800"])
    assert not first["457 U.S. 800"].from_cache

    client2, _ = client_with([], cache_dir=tmp_path)
    second = client2.lookup(["457 U.S. 800"])
    assert second["457 U.S. 800"].from_cache
    assert second["457 U.S. 800"].status == 200
    assert client2.session.calls == []


def test_throttled_results_are_never_cached(tmp_path):
    client, _ = client_with([], cache_dir=tmp_path)
    client.cache.put(LookupResult(citation="1 F.2d 1", status=429))
    assert client.cache.get("1 F.2d 1") is None


# --- status mapping: the P1 firewall -----------------------------------------


def _ledger_for(text: str) -> Ledger:
    document = Document(
        text=text, source_path="t", source_format="txt", page_map=[(0, len(text), 1)]
    )
    citations, notes = extract(document)
    return resolve(document, citations, notes)


def _run(text: str, status: int, **extra) -> Ledger:
    ledger = _ledger_for(text)
    normalized = next(e.citation.normalized for e in ledger if e.citation.normalized)
    payload = [{"citation": normalized, "status": status, **extra}]
    client, _ = client_with([FakeResponse(200, payload)])
    verify_existence(ledger, client)
    return ledger


BRIEF = "Harlow v. Fitzgerald, 457 U.S. 800, 818 (1982)."


def test_status_200_passes_existence():
    entry = next(iter(_run(BRIEF, 200, clusters=[{"id": 7, "case_name": "Harlow v. Fitzgerald"}])))
    assert entry.checks["existence"].verdict is Verdict.PASS
    assert entry.resolved_to.cluster_id == 7


def test_status_404_is_not_found_and_never_flagged():
    """A real reporter whose volume/page is absent is a coverage gap."""
    entry = next(iter(_run(BRIEF, 404)))
    check = entry.checks["existence"]
    assert check.verdict is Verdict.NOT_FOUND
    assert check.sources_consulted == ["courtlistener_citation_lookup"]
    assert "not a finding that the case does not exist" in check.reason
    assert entry.overall is Overall.UNVERIFIED
    assert entry.overall is not Overall.FLAGGED


def test_status_400_is_kept_structurally_separate_from_404():
    """The two must never share a branch: 400 questions the reporter, 404 is absence."""
    four_hundred = next(iter(_run(BRIEF, 400, error_message="Reporter not found")))
    four_oh_four = next(iter(_run(BRIEF, 404)))
    assert four_hundred.checks["existence"].verdict is Verdict.NOT_CHECKABLE
    assert four_oh_four.checks["existence"].verdict is Verdict.NOT_FOUND
    assert four_hundred.overall is not four_oh_four.overall


def test_status_300_with_a_matching_candidate_resolves_to_it():
    """The document names a case; that usually settles which candidate it is."""
    clusters = [
        {"id": 1, "case_name": "Harlow v. Fitzgerald", "date_filed": "1982-06-24",
         "precedential_status": "Published", "absolute_url": "/opinion/1/x/"},
        {"id": 2, "case_name": "Unrelated v. Party", "date_filed": "1955-01-01"},
    ]
    entry = next(iter(_run(BRIEF, 300, clusters=clusters)))
    assert entry.checks["existence"].verdict is Verdict.PASS
    assert "match exactly one" in entry.checks["existence"].evidence
    assert entry.resolved_to.cluster_id == 1


def test_status_300_with_no_matching_candidate_is_a_name_mismatch():
    clusters = [
        {"id": 1, "case_name": "Alpha v. Beta", "date_filed": "1982-06-24"},
        {"id": 2, "case_name": "Gamma v. Delta", "date_filed": "1982-06-24"},
    ]
    entry = next(iter(_run(BRIEF, 300, clusters=clusters)))
    assert entry.checks["existence"].verdict is Verdict.AMBIGUOUS
    assert entry.checks["case_name"].verdict is Verdict.FAIL
    assert entry.overall is Overall.FLAGGED


def test_status_300_that_cannot_be_settled_suppresses_downstream_checks():
    """Confirming metadata against an arbitrarily chosen candidate is unearned."""
    clusters = [
        {"id": 1, "case_name": "Harlow v. Fitzgerald", "date_filed": "1982-06-24"},
        {"id": 2, "case_name": "Harlow v. Fitzgerald", "date_filed": "1982-11-01"},
    ]
    entry = next(iter(_run(BRIEF, 300, clusters=clusters)))
    assert entry.checks["existence"].verdict is Verdict.AMBIGUOUS
    for dimension in ("case_name", "court", "year"):
        assert entry.checks[dimension].verdict is Verdict.NOT_CHECKABLE
        assert entry.checks[dimension].suppressed_by == "existence"
    assert entry.overall is Overall.REVIEW


def test_a_citation_the_endpoint_ignores_is_not_an_accusation():
    """An unechoed citation teaches us nothing; it must not flag a real case."""
    ledger = _ledger_for(BRIEF)
    client, _ = client_with([FakeResponse(200, [])])
    verify_existence(ledger, client)
    entry = next(iter(ledger))
    assert entry.checks["existence"].verdict is Verdict.NOT_CHECKABLE
    assert entry.checks["reporter_valid"].verdict is Verdict.PASS
    assert entry.overall is Overall.REVIEW


def test_status_400_on_a_real_reporter_is_a_source_disagreement_not_a_finding():
    """reporters-db recognizes U.S.; a lone CL 400 must not FLAG Harlow."""
    entry = next(iter(_run(BRIEF, 400, error_message="Reporter not found")))
    check = entry.checks["reporter_valid"]
    assert check.verdict is Verdict.NOT_CHECKABLE
    assert "sources disagree" in check.reason
    assert entry.overall is Overall.REVIEW
    assert entry.overall is not Overall.FLAGGED


def test_status_400_flags_only_when_both_sources_agree():
    ledger = _ledger_for("Bogus v. Nobody, 33 Umbrella 422 (2020).")
    client, _ = client_with([FakeResponse(200, [])])
    verify_existence(ledger, client)
    entry = next(iter(ledger))
    assert entry.checks["reporter_valid"].verdict is Verdict.FAIL
    assert entry.overall is Overall.FLAGGED


def test_short_forms_inherit_the_antecedents_existence_verdict():
    text = "Harlow v. Fitzgerald, 457 U.S. 800 (1982). Id. at 819. Harlow, 457 U.S. at 820."
    ledger = _ledger_for(text)
    payload = [{"citation": "457 U.S. 800", "status": 200, "clusters": [{"id": 7}]}]
    client, _ = client_with([FakeResponse(200, payload)])
    verify_existence(ledger, client)
    assert [e.checks["existence"].verdict for e in ledger] == [Verdict.PASS] * 3
    assert all(e.resolved_to.cluster_id == 7 for e in ledger)


def test_known_coverage_gaps_never_reach_the_api():
    """A WL identifier cannot succeed, so it must not consume a lookup."""
    ledger = _ledger_for("See Tinch v. Video Indus. Servs., Inc., 2019 WL 1396975 (E.D. Mich. 2019).")
    client, _ = client_with([])
    verify_existence(ledger, client)
    assert client.session.calls == []
    assert next(iter(ledger)).checks["existence"].verdict is Verdict.OUT_OF_SCOPE


# --- the undocumented request limit -------------------------------------------
#
# Measured 2026-08-31: citation-lookup throttles at the fifth *request* in a
# minute, regardless of how few citations those requests carry. Metering only
# citations was a bug that crashed a benchmark run four minutes in.


def test_requests_are_metered_not_just_citations():
    clock = FakeClock()
    client = CourtListenerClient(
        token="t",
        session=FakeSession([
            FakeResponse(200, [{"citation": f"{i} U.S. 1", "status": 404}])
            for i in range(6)
        ]),
        sleeper=clock.sleep,
        clock=clock,
    )
    start = clock.t
    for i in range(5):
        client.lookup([f"{i} U.S. 1"])
    # Five single-citation lookups is 5 citations -- nowhere near the 60/min
    # citation ceiling -- but it is 5 requests, which is the real ceiling.
    assert clock.t - start == 0.0
    client.lookup(["99 U.S. 1"])
    assert clock.t - start > 59, "the sixth request should have waited out the window"


def test_request_budget_is_shared_with_the_opinion_endpoints():
    """Conservative reading: one budget across the API, not one per endpoint."""
    clock = FakeClock()
    client = CourtListenerClient(token="t", session=FakeSession([]), sleeper=clock.sleep, clock=clock)
    assert client.detail_bucket is client.request_bucket


def test_batching_is_what_makes_the_documented_rate_reachable():
    """250 citations in one request costs one request; 250 requests cost 50 min."""
    client, _ = client_with([])
    batches = list(client._batch([f"{i} F.2d 1" for i in range(1, 251)]))
    assert len(batches) == 1 and len(batches[0]) == 250
