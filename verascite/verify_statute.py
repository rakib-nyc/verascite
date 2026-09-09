"""Verdicts for statutory and regulatory citations.

Every statutory citation in a document used to be `OUT_OF_SCOPE`: seen,
counted, set aside. This module decides them, and the decisions it is allowed
to reach are narrower than the sources would technically support, for the same
reason every other check in this project is narrower than it could be.

**The one finding worth making.** A fabricated subsection of a real statute --
``42 U.S.C. § 1983(a)(2)``, where § 1983 has no subsections at all -- is
common, damaging, and close to invisible to a human reviewer, because
everything before the parenthesis is correct. It is also *provable*: the
official structural text of the title either contains that identifier or it
does not, and we hold the file.

**Why absence is treated differently here than elsewhere, and why that is not
an exception to the rule.** The governing rule forbids treating *failure to
find* as evidence of fabrication. A parsed title file that does not contain a
subsection is not a failure to find; it is a retrieved source that contradicts
the document, exactly as a retrieved opinion that lacks a quoted sentence is.
The distinction is carried by the client's three states and is never inferred
here: only `ABSENT` -- which the client emits from three specific affirmative
observations -- can become a `FAIL`. `UNVERIFIED` becomes `NOT_FOUND` and
names what was consulted, and no amount of it ever accumulates into a finding.

**What is deliberately not decided.** Currency. Whether the version in force on
the date the brief was filed contained the provision is a real question and
the sources can nearly answer it, but "nearly" is not good enough for a check
that would otherwise tell an attorney their correct citation is wrong. The
dimension is reported `NOT_CHECKABLE` with the release point the check *was*
made against, so the reader knows what date the answer describes.
"""

from __future__ import annotations

from typing import Optional

from .clients.statutes import ABSENT, EXISTS, StatuteClient
from .models import LedgerEntry
from .statutes import CFR, PUBLIC_LAW, STATUTES_AT_LARGE, USC, StatuteRef, parse_statute
from .verdicts import CheckResult, Verdict


def verify_statute(
    entry: LedgerEntry,
    client: Optional[StatuteClient] = None,
    offline: bool = False,
) -> None:
    """Decide the statutory dimensions for one ledger entry."""
    ref = parse_statute(entry.citation)
    if ref is None:
        return  # not a statutory citation

    entry.notes.append(f"statutory citation parsed as {ref.describe()}")

    if not ref.checkable:
        entry.try_set_check("existence", CheckResult(
            Verdict.OUT_OF_SCOPE,
            reason=ref.declined,
        ))
        return

    if offline or client is None:
        entry.try_set_check("existence", CheckResult(
            Verdict.NOT_CHECKABLE,
            reason=(
                "statutory existence requires consulting the government's "
                "published text, which this run did not do"
            ),
        ))
        return

    _check_existence(entry, ref, client)
    _check_subsection(entry, ref, client)
    _note_currency(entry, ref)


def _check_existence(entry: LedgerEntry, ref: StatuteRef, client: StatuteClient) -> None:
    if ref.code == USC:
        result = client.usc_section(ref.title, ref.section, ref.year)
        cited = f"{ref.title} U.S.C. § {ref.section}"
    elif ref.code == CFR:
        result = client.cfr_section(ref.title, ref.part, ref.section, ref.year)
        cited = f"{ref.title} C.F.R. § {ref.section}"
    elif ref.code == PUBLIC_LAW:
        result = client.public_law(ref.congress, ref.law_number)
        cited = f"Public Law {ref.congress}-{ref.law_number}"
    elif ref.code == STATUTES_AT_LARGE:
        result = client.statute_at_large(ref.volume, ref.page)
        cited = f"{ref.volume} Stat. {ref.page}"
    else:
        return

    if result.state == EXISTS:
        entry.try_set_check("existence", CheckResult(
            Verdict.PASS,
            evidence=result.detail,
            sources_consulted=result.sources_consulted,
            confidence=result.confidence,
        ))
        return

    if result.state == ABSENT:
        # A retrieved source affirmatively reports the provision is not there.
        # That is contradiction, not absence of evidence.
        entry.try_set_check("existence", CheckResult(
            Verdict.FAIL,
            evidence=(
                f"{cited}: {result.detail}. Confirm the citation against the "
                "official text before filing"
            ),
            sources_consulted=result.sources_consulted,
            confidence=result.confidence,
        ))
        return

    entry.try_set_check("existence", CheckResult(
        Verdict.NOT_FOUND,
        reason=(
            f"{cited} was not confirmed. {result.detail}. This is not a finding "
            "that the provision does not exist"
        ),
        sources_consulted=result.sources_consulted or ["govinfo_link_service"],
    ))


def _check_subsection(entry: LedgerEntry, ref: StatuteRef, client: StatuteClient) -> None:
    """The check this module exists for."""
    if not ref.subsection:
        entry.try_set_check("statute_parts", CheckResult(
            Verdict.NA,
            reason="the citation names no subsection",
        ))
        return

    existence = entry.checks.get("existence")
    if existence is not None and existence.verdict is Verdict.FAIL:
        # The section itself is contradicted. Reporting the subsection
        # separately would count one defect twice and bury the row that
        # actually needs acting on.
        entry.try_set_check("statute_parts", CheckResult(
            Verdict.NOT_CHECKABLE,
            reason="the section itself was not confirmed, so its subsections were not checked",
            suppressed_by="existence",
        ))
        return

    cited = ref.describe()
    if ref.code == USC:
        result = client.usc_subsection(ref.title, ref.section, ref.subsection)
    elif ref.code == CFR:
        result = client.cfr_subsection(ref.title, ref.part, ref.section,
                                       ref.subsection, ref.year)
    else:
        entry.try_set_check("statute_parts", CheckResult(
            Verdict.NA,
            reason="subsection structure is not checked for this authority type",
        ))
        return

    if result.state == EXISTS:
        entry.try_set_check("statute_parts", CheckResult(
            Verdict.PASS,
            evidence=result.detail,
            sources_consulted=result.sources_consulted,
            confidence=result.confidence,
        ))
        return

    if result.state == ABSENT:
        entry.try_set_check("statute_parts", CheckResult(
            Verdict.FAIL,
            evidence=(
                f"{cited}: {result.detail}. A subsection that the official text "
                "does not contain is the most easily overlooked citation defect "
                "there is, because everything before the parenthesis is correct"
            ),
            sources_consulted=result.sources_consulted,
            confidence=result.confidence,
        ))
        return

    entry.try_set_check("statute_parts", CheckResult(
        Verdict.NOT_CHECKABLE,
        reason=result.detail,
        sources_consulted=result.sources_consulted,
    ))


def _note_currency(entry: LedgerEntry, ref: StatuteRef) -> None:
    """Say what date the answer describes, and decline to date it further."""
    existence = entry.checks.get("existence")
    as_of = ""
    for check in (existence, entry.checks.get("statute_parts")):
        if check is not None and isinstance(check.detail, dict):
            as_of = check.detail.get("as_of", "") or as_of
    entry.try_set_check("statute_currency", CheckResult(
        Verdict.NOT_CHECKABLE,
        reason=(
            "whether this provision read the same way on the date it matters "
            "was not checked. The answer above describes the text currently in "
            "force" + (f", as of {as_of}" if as_of else "") + ". A provision "
            "amended or repealed since the events in issue will still be "
            "reported as existing"
        ),
        sources_consulted=["statutory_currency_not_examined"],
    ))
