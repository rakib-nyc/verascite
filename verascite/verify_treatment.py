"""Stage 4e: has this authority been treated badly by later courts?

**This is not a citator and this module does not pretend to be one.** It
reports what free sources can establish and states plainly what they cannot.

The spec proposes triage: pull citing opinions, scan them for negative-signal
language, and flag hits for review. That was measured before being built, and
it does not work. Of the 14,623 opinions citing *Bell Atlantic Corp. v.
Twombly*, **2,489 contain "overruled", "abrogated", "no longer good law", or
"recede from" somewhere in their text** -- a seventeen percent baseline. The
words are in those opinions; they are almost never about the case being
checked. A dimension that fires on one citation in six, with no ability to say
which, is worse than no dimension: it would train a reader to dismiss it, and
that habit would carry over to the checks that do work.

Distinguishing real negative treatment would mean fetching each citing
opinion's text and reading the passage around the citation. At two requests per
opinion against a 125-request daily ceiling, that is one authority per day.

So this reports three facts and no verdict: how heavily the authority is cited,
what its precedential status is, and where to look. Whether it remains good law
is left explicitly unanswered, which is the truthful answer and the one a
reader can act on.
"""

from __future__ import annotations

from typing import Optional

from .models import LedgerEntry
from .verdicts import CheckResult, Verdict

TREATMENT_SOURCE = "courtlistener_citation_network"

#: Language that would indicate negative treatment if it could be attributed to
#: the passage discussing the cited case. Retained for a future implementation
#: against a source that supports passage-level retrieval -- Free Law Project's
#: citator, when it ships -- and deliberately unused here.
NEGATIVE_SIGNALS = (
    "overrul", "abrogat", "supersed", "revers", "vacat", "no longer good law",
    "we now hold", "recede from", "disapprov", "called into question",
)


def citing_opinions_url(cluster_id: Optional[int]) -> Optional[str]:
    if not cluster_id:
        return None
    return (
        "https://www.courtlistener.com/?q=cites%3A%28"
        f"{cluster_id}%29&type=o&order_by=dateFiled+desc"
    )


def verify_treatment(entry: LedgerEntry, citation_count: Optional[int] = None) -> None:
    """Set the ``treatment`` dimension. Always NOT_CHECKABLE, always explained."""
    resolution = entry.resolved_to
    if resolution is None or not resolution.cluster_id:
        entry.try_set_check(
            "treatment",
            CheckResult(
                Verdict.NOT_CHECKABLE,
                reason=(
                    "no record was retrieved, so later treatment of this authority "
                    "could not be looked at. Whether it remains good law is "
                    "unknown; check a citator."
                ),
            ),
        )
        return

    parts = ["this tool is not a citator and did not determine whether this "
             "authority is still good law"]
    if citation_count:
        parts.append(f"CourtListener records {citation_count:,} later citing opinion(s)")
    if resolution.precedential_status:
        parts.append(f"precedential status is {resolution.precedential_status!r}")
    url = citing_opinions_url(resolution.cluster_id)
    if url:
        parts.append(f"the citing opinions are listed at {url}")

    entry.try_set_check(
        "treatment",
        CheckResult(
            Verdict.NOT_CHECKABLE,
            reason=". ".join(p[0].upper() + p[1:] for p in parts)
            + ". Overruled, reversed, vacated, abrogated, and superseded "
            "authority cannot be detected from free sources; confirm in "
            "Shepard's, KeyCite, or BCite before relying on this.",
            sources_consulted=[TREATMENT_SOURCE],
            detail={
                "cluster_id": resolution.cluster_id,
                "citation_count": citation_count,
                "citing_opinions_url": url,
            },
        ),
    )
