"""Stage 4a: does this authority exist in the sources we can consult?

This module owns the highest-signal deterministic result in the system, and
also its most dangerous one. The mapping from CourtListener status to verdict
is where P1 is either honored or broken:

    400  reporter not recognized   -> reporter_valid FAIL   (evidence-backed)
    404  valid reporter, absent    -> existence NOT_FOUND    (NOT fabricated)
    300  multiple clusters         -> existence AMBIGUOUS
    200  matched                   -> existence PASS

The 400/404 split is never collapsed. A 400 means the reporter abbreviation
does not exist in American law -- an affirmative fact about the citation. A
404 means a real reporter whose volume/page is not in this database, which is
routine for recent decisions, unpublished dispositions, and state trial
courts. Reporting the second as the first is the failure that makes lawyers
stop trusting the tool.
"""

from __future__ import annotations

from typing import Optional

from .clients.courtlistener import (
    COURT_SOURCE_NAME,
    SOURCE_NAME,
    STATUS_NO_RESPONSE,
    CourtListenerClient,
    LookupResult,
)
from .config import NAME_PASS_THRESHOLD
from .extract import is_non_case_source, reporter_is_known
from .ledger import Ledger, utcnow
from .models import CiteKind, LedgerEntry, Resolution
from .verdicts import CheckResult, Verdict
from .verify_metadata import name_similarity

LOCAL_SOURCE = "reporters_db"


def check_reporter_locally(entry: LedgerEntry) -> Optional[CheckResult]:
    """Offline reporter validity, from reporters-db alone.

    This runs with no network and no token. It is the check that catches an
    invented reporter, and it is the reason the shadow scan in ``extract``
    exists at all.
    """
    # reporters-db catalogues *case reporters*. Running it against a statutory
    # or journal citation would report "U.S.C." as an unknown reporter and
    # FLAG a perfectly correct citation to 42 U.S.C. 1983 -- precisely the
    # false positive P1 exists to prevent.
    if entry.citation.kind not in (
        CiteKind.FULL_CASE,
        CiteKind.SHORT_CASE,
        CiteKind.UNRECOGNIZED,
    ):
        return None

    reporter = entry.citation.reporter
    if not reporter:
        return None

    # eyecite classifies some non-case citations as case short forms --
    # "80 Fed. Reg. at 64,545" parses as a ShortCaseCitation. The Federal
    # Register is not in reporters-db because it is not a case reporter, and
    # reporting it as an invented reporter flags a real citation as fabricated.
    if is_non_case_source(reporter):
        return None
    if reporter_is_known(reporter):
        return CheckResult(
            Verdict.PASS,
            evidence=f"reporter {reporter!r} present in reporters-db",
            sources_consulted=[LOCAL_SOURCE],
        )
    return CheckResult(
        Verdict.FAIL,
        evidence=(
            f"reporter abbreviation {reporter!r} does not appear in reporters-db, "
            "which catalogues every reporter recognized in American law "
            "(1,342 editions and 2,282 variant abbreviations). No such reporter "
            "exists."
        ),
        sources_consulted=[LOCAL_SOURCE],
    )


def _parallel_citations(cluster: dict) -> list[str]:
    """Every reporter citation the cluster records, as "volume reporter page".

    Skips vendor-neutral LEXIS and WL identifiers: no free archive holds them,
    so they cannot be used to reach the text.
    """
    out: list[str] = []
    for cite in cluster.get("citations") or []:
        volume, reporter, page = (
            cite.get("volume"), cite.get("reporter"), cite.get("page")
        )
        if not (volume and reporter and page):
            continue
        if "LEXIS" in reporter or reporter.strip() in {"WL", "U.S. LEXIS"}:
            continue
        out.append(f"{volume} {reporter} {page}")
    return out


def _resolution_from(result: LookupResult) -> Optional[Resolution]:
    if not result.clusters:
        return None
    cluster = result.clusters[0]
    absolute = cluster.get("absolute_url") or ""
    return Resolution(
        source="courtlistener",
        cluster_id=cluster.get("id"),
        url=f"https://www.courtlistener.com{absolute}" if absolute else None,
        case_name=cluster.get("case_name") or cluster.get("case_name_full"),
        case_name_short=cluster.get("case_name_short"),
        case_name_full=cluster.get("case_name_full"),
        court_id=cluster.get("court_id") or _court_id_from(cluster),
        docket_id=cluster.get("docket_id"),
        date_filed=cluster.get("date_filed"),
        precedential_status=cluster.get("precedential_status"),
        parallel_citations=_parallel_citations(cluster),
        retrieved_at=result.retrieved_at,
        candidates=[
            {
                "id": c.get("id"),
                "case_name": c.get("case_name"),
                "date_filed": c.get("date_filed"),
                "absolute_url": c.get("absolute_url"),
            }
            for c in result.clusters
        ]
        if len(result.clusters) > 1
        else [],
    )


def _court_id_from(cluster: dict) -> Optional[str]:
    court = cluster.get("court")
    if isinstance(court, dict):
        return court.get("id")
    if isinstance(court, str) and court.startswith("http"):
        return court.rstrip("/").rsplit("/", 1)[-1]
    return court


def _candidate_matches(entry: LedgerEntry, cluster: dict) -> bool:
    """Does the document's own assertion pick out this candidate?"""
    asserted_name = entry.citation.case_name
    actual_name = cluster.get("case_name") or cluster.get("case_name_full")
    if not asserted_name or not actual_name:
        return False
    if name_similarity(asserted_name, actual_name) < NAME_PASS_THRESHOLD:
        return False

    year = entry.citation.year
    filed = str(cluster.get("date_filed") or "")[:4]
    if year and filed and abs(int(year) - int(filed)) > 1:
        return False
    return True


def _resolve_ambiguity(entry: LedgerEntry, result: LookupResult, citation: str) -> None:
    """Try to settle a status 300 using what the document itself asserts.

    Reporting AMBIGUOUS while also confirming the case name, court, and year
    against one arbitrarily chosen candidate is confidence the tool has not
    earned -- those verdicts were validated against a guess. But the document
    names a case and a year, and that is usually enough to pick the right
    candidate outright, which turns an unresolvable citation into a verified
    one for free.
    """
    matches = [c for c in result.clusters if _candidate_matches(entry, c)]
    names = ", ".join(
        repr(c.get("case_name") or "?") for c in result.clusters[:4]
    )

    if len(matches) == 1:
        narrowed = LookupResult(
            citation=result.citation,
            status=200,
            normalized_citations=result.normalized_citations,
            clusters=matches,
            retrieved_at=result.retrieved_at,
        )
        entry.resolved_to = _resolution_from(narrowed)
        entry.set_check(
            "existence",
            CheckResult(
                Verdict.PASS,
                evidence=(
                    f"CourtListener returned {len(result.clusters)} candidates for "
                    f"{citation!r} ({names}); the case name and year asserted in the "
                    f"document match exactly one of them, "
                    f"{matches[0].get('case_name')!r}."
                ),
                sources_consulted=[SOURCE_NAME],
            ),
        )
        return

    if not matches and entry.citation.case_name:
        entry.resolved_to = _resolution_from(result)
        entry.set_check(
            "existence",
            CheckResult(
                Verdict.AMBIGUOUS,
                evidence=(
                    f"CourtListener returned {len(result.clusters)} candidates for "
                    f"{citation!r} ({names}), and the case name asserted in the "
                    f"document, {entry.citation.case_name!r}, matches none of them."
                ),
                sources_consulted=[SOURCE_NAME],
            ),
        )
        entry.set_check(
            "case_name",
            CheckResult(
                Verdict.FAIL,
                evidence=(
                    f"the document cites {entry.citation.case_name!r}, but no case "
                    f"reported at {citation} bears that name. Candidates: {names}."
                ),
                sources_consulted=[SOURCE_NAME],
            ),
        )
        return

    entry.resolved_to = _resolution_from(result)
    entry.set_check(
        "existence",
        CheckResult(
            Verdict.AMBIGUOUS,
            evidence=(
                f"CourtListener returned {len(result.clusters)} candidates for "
                f"{citation!r} ({names}) and the document does not distinguish "
                "between them. Disambiguate before relying on this citation."
            ),
            sources_consulted=[SOURCE_NAME],
        ),
    )
    for dimension in ("case_name", "court", "year", "precedential"):
        entry.set_check(
            dimension,
            CheckResult(
                Verdict.NOT_CHECKABLE,
                reason=(
                    "cannot be compared while the citation matches several "
                    "different cases"
                ),
                suppressed_by="existence",
                sources_consulted=[SOURCE_NAME],
            ),
        )


def _apply_lookup(entry: LedgerEntry, result: LookupResult) -> None:
    """Translate one CourtListener status into verdicts. The P1 firewall."""
    citation = entry.citation.normalized or entry.citation.raw_text

    if result.normalized_citations:
        entry.notes.append(
            f"CourtListener normalized {citation!r} to "
            f"{result.normalized_citations[0]!r}"
        )

    if result.status == 200:
        entry.resolved_to = _resolution_from(result)
        entry.set_check(
            "existence",
            CheckResult(
                Verdict.PASS,
                evidence=(
                    f"CourtListener status 200 for {citation!r}; "
                    f"{len(result.clusters)} cluster(s) returned"
                ),
                sources_consulted=[SOURCE_NAME],
            ),
        )
        return

    if result.status == 300:
        _resolve_ambiguity(entry, result, citation)
        return

    if result.status == STATUS_NO_RESPONSE:
        entry.set_check(
            "existence",
            CheckResult(
                Verdict.NOT_CHECKABLE,
                reason=(
                    f"{result.error_message or 'no result returned'} for "
                    f"{citation!r}. Nothing was learned about this citation "
                    "either way; check it manually."
                ),
                sources_consulted=[SOURCE_NAME],
            ),
        )
        return

    if result.status == 400:
        # FLAGGED is the harshest thing this tool says about a citation, so it
        # requires two independent sources to agree. reporters-db has already
        # been consulted locally; if it recognizes the reporter, a CL 400 is a
        # disagreement between sources, not a finding against the brief.
        local = entry.checks.get("reporter_valid")
        if local is not None and local.verdict is Verdict.PASS:
            entry.set_check(
                "reporter_valid",
                CheckResult(
                    Verdict.NOT_CHECKABLE,
                    reason=(
                        f"sources disagree about {citation!r}: reporters-db "
                        f"recognizes the reporter {entry.citation.reporter!r}, but "
                        f"CourtListener returned status 400 "
                        f"({result.error_message or 'reporter not recognized'}). "
                        "Resolve by hand; this is not a fabrication finding."
                    ),
                    sources_consulted=[LOCAL_SOURCE, SOURCE_NAME],
                ),
            )
        else:
            entry.set_check(
                "reporter_valid",
                CheckResult(
                    Verdict.FAIL,
                    evidence=(
                        f"CourtListener status 400 for {citation!r}: "
                        f"{result.error_message or 'reporter not recognized'}. "
                        "reporters-db does not list this reporter either. Two "
                        "independent sources agree the abbreviation is not one "
                        "used in American law."
                    ),
                    sources_consulted=[LOCAL_SOURCE, SOURCE_NAME],
                ),
            )
        entry.set_check(
            "existence",
            CheckResult(
                Verdict.NOT_CHECKABLE,
                reason=(
                    "existence cannot be evaluated while the reporter itself is "
                    "in question"
                ),
                suppressed_by="reporter_valid",
                sources_consulted=[SOURCE_NAME],
            ),
        )
        return

    # 404 and anything else: absence, and absence only.
    entry.set_check(
        "existence",
        CheckResult(
            Verdict.NOT_FOUND,
            reason=(
                f"CourtListener status {result.status} for {citation!r}: the "
                "reporter is valid but this volume/page is not in the database. "
                "Free databases routinely lack recent decisions, unpublished "
                "dispositions, and state trial court opinions. This is not a "
                "finding that the case does not exist."
            ),
            sources_consulted=[SOURCE_NAME],
        ),
    )


def verify_existence(
    ledger: Ledger,
    client: Optional[CourtListenerClient] = None,
    offline: bool = False,
    fetch_courts: bool = True,
) -> Ledger:
    """Run reporter validity locally, then existence against CourtListener."""
    lookupable: dict[str, list[LedgerEntry]] = {}

    for entry in ledger:
        cite = entry.citation

        # Reporter validity is always checked; it needs no network.
        local = check_reporter_locally(entry)
        if local is not None:
            entry.set_check("reporter_valid", local)
        else:
            entry.set_check(
                "reporter_valid",
                CheckResult(
                    Verdict.NA,
                    reason="this citation type does not carry a case reporter",
                ),
            )

        # Back-references inherit their antecedent's existence verdict.
        if entry.antecedent_id:
            continue
        if "existence" in entry.checks:
            continue  # dangling back-reference, already NOT_CHECKABLE

        if cite.kind is CiteKind.LAW:
            entry.set_check(
                "existence",
                CheckResult(
                    Verdict.OUT_OF_SCOPE,
                    reason=(
                        "statutory and regulatory citations are not served by the "
                        "CourtListener citation-lookup endpoint; verify against "
                        "GovInfo, eCFR, or the issuing jurisdiction"
                    ),
                ),
            )
            continue

        if cite.kind is CiteKind.JOURNAL:
            entry.set_check(
                "existence",
                CheckResult(
                    Verdict.OUT_OF_SCOPE,
                    reason="secondary sources are outside this pipeline's coverage",
                ),
            )
            continue

        if cite.reporter and is_non_case_source(cite.reporter):
            entry.set_check(
                "existence",
                CheckResult(
                    Verdict.OUT_OF_SCOPE,
                    reason=(
                        f"{cite.reporter} is a journal, statutory compilation, or "
                        "government publication rather than a case reporter, and is "
                        "not served by the citation-lookup endpoint. Verify against "
                        "the issuing source."
                    ),
                ),
            )
            continue

        vendor_reason = CourtListenerClient.is_vendor_only(cite.reporter)
        if vendor_reason:
            # Known coverage gap (spec 7.3): route to manual check instead of
            # burning a lookup that structurally cannot succeed.
            entry.set_check(
                "existence",
                CheckResult(
                    Verdict.OUT_OF_SCOPE,
                    reason=(
                        f"{vendor_reason}; these identifiers exist only in the "
                        "vendor's database and are absent from every free source. "
                        "Verify in Westlaw or Lexis directly. This is a known "
                        "coverage gap, not a fabrication signal."
                    ),
                ),
            )
            continue

        if entry.checks.get("reporter_valid", CheckResult(Verdict.PENDING)).verdict is Verdict.FAIL:
            entry.set_check(
                "existence",
                CheckResult(
                    Verdict.NOT_CHECKABLE,
                    reason=(
                        "existence cannot be evaluated because the reporter itself "
                        "is not a recognized reporter"
                    ),
                    suppressed_by="reporter_valid",
                ),
            )
            continue

        key = cite.normalized
        if not key:
            entry.set_check(
                "existence",
                CheckResult(
                    Verdict.NOT_CHECKABLE,
                    reason="citation lacks a volume/reporter/page triple to look up",
                ),
            )
            continue

        lookupable.setdefault(key, []).append(entry)

    if offline or client is None or not client.can_serve:
        reason = (
            "offline mode: no external lookup was performed"
            if offline
            else "no COURTLISTENER_API_TOKEN configured, so no external lookup was performed"
        )
        for entries in lookupable.values():
            for entry in entries:
                entry.set_check("existence", CheckResult(Verdict.NOT_CHECKABLE, reason=reason))
        ledger.run_meta["existence_lookup"] = reason
    else:
        results = client.lookup(list(lookupable))
        for key, entries in lookupable.items():
            result = results.get(key)
            for entry in entries:
                if result is None:
                    entry.set_check(
                        "existence",
                        CheckResult(
                            Verdict.NOT_CHECKABLE,
                            reason="lookup did not complete for this citation",
                            sources_consulted=[SOURCE_NAME],
                        ),
                    )
                else:
                    _apply_lookup(entry, result)
        ledger.run_meta["existence_lookup"] = {
            "distinct_citations": len(lookupable),
            "requests": client.stats["requests"],
            "cache_hits": client.cache.hits,
            "cache_misses": client.cache.misses,
            "expired_from_cache": client.cache.expired,
            "throttle_waits": client.stats["throttle_waits"],
            # When the sources were actually read, not when this run happened.
            # A report whose header timestamps the run implies a freshness it
            # does not have (spec 9).
            "oldest_evidence": client.cache.oldest_hit,
            "fetched_now": client.stats["requests"] > 0,
            "run_at": utcnow(),
        }

    if client is not None and client.has_token and not offline and fetch_courts:
        _enrich_courts(ledger, client)

    _propagate_to_backrefs(ledger)
    return ledger


def _enrich_courts(ledger: Ledger, client: CourtListenerClient) -> None:
    """Attach the court id, which citation-lookup does not return.

    One cheap, permanently-cached docket fetch per distinct authority. A
    failure leaves court_id unset, which reads as NOT_CHECKABLE downstream.
    """
    by_docket: dict[int, list] = {}
    for entry in ledger:
        resolution = entry.resolved_to
        if resolution and resolution.docket_id and not resolution.court_id:
            by_docket.setdefault(resolution.docket_id, []).append(resolution)

    for docket_id, resolutions in by_docket.items():
        court_id = client.fetch_court(docket_id)
        if not court_id:
            continue
        for resolution in resolutions:
            resolution.court_id = court_id
    for entry in ledger:
        if entry.resolved_to and entry.resolved_to.court_id:
            if COURT_SOURCE_NAME not in entry.sources_consulted:
                entry.sources_consulted.append(COURT_SOURCE_NAME)
    if by_docket:
        ledger.run_meta["court_lookups"] = len(by_docket)


def _propagate_to_backrefs(ledger: Ledger) -> None:
    """A short form is as verified as the authority it points at."""
    for entry in ledger:
        if not entry.antecedent_id:
            continue
        parent = ledger.get(entry.antecedent_id)
        if parent is None:
            continue
        parent_existence = parent.checks.get("existence")
        if parent_existence is None:
            continue
        entry.set_check(
            "existence",
            CheckResult(
                verdict=parent_existence.verdict,
                evidence=(
                    f"inherited from antecedent {parent.citation_id}: "
                    f"{parent_existence.evidence}"
                )
                if parent_existence.evidence
                else None,
                reason=(
                    f"inherited from antecedent {parent.citation_id}: "
                    f"{parent_existence.reason}"
                )
                if parent_existence.reason
                else None,
                sources_consulted=list(parent_existence.sources_consulted),
            ),
        )
        if parent.resolved_to and not entry.resolved_to:
            entry.resolved_to = parent.resolved_to
