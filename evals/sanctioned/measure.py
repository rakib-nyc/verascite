"""Run the checker against citations a court has adjudicated to be fabricated.

**What this answers, and what it cannot.** Every performance figure in this
project comes from one benchmark corpus of *injected* defects. This corpus is
different in kind: each citation here was named in a court order as fabricated
or unsupported. The labels are judicial findings rather than an annotator's
judgment.

It is still not the question a lawyer would most like answered. The corpus is
built from *orders*, not from the *filings* that contained the citations, so
the question it settles is "given a citation a court adjudicated to be
fabricated, does the tool flag it" -- not "does the tool catch bad citations in
a brief." `CORPUS.md` states that limitation and the others at length.

**Two things this harness reports that a naive one would hide.**

Vendor-only identifiers. A large share of these citations are of the form
``2019 WL 1396975``. Those are real decisions with no free-archive
identifier, and the governing rule requires them to be `UNVERIFIED` rather
than flagged. Scoring them as misses would understate the tool; scoring them
as catches would reward exactly the behaviour the project forbids. They are
therefore counted and reported **separately**, and excluded from the headline.

What could not be run. Existence resolution needs a CourtListener token. When
none is present the run says so and reports only the checks that actually
executed, rather than presenting a partial run as a whole one.

**On spending somebody else's rate limit.** CourtListener is a nonprofit
serving this for free under a measured ceiling of five requests a minute and
roughly 125 a day. Scoring six hundred citations one lookup at a time would
blow through a day's allowance five times over to learn exactly what one
batched pass learns: the endpoint takes many citations per request. So the run
happens in two passes. The first resolves every distinct citation in as few
batched requests as the batch size allows and fills the cache. The second scores
each citation against that cache and makes no request at all. Re-running the
whole corpus afterwards is free.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from verascite.extract import extract  # noqa: E402
from verascite.models import CiteKind, Document  # noqa: E402
from verascite.resolve import resolve  # noqa: E402
from verascite.verdicts import Overall, Verdict  # noqa: E402
from verascite.verify_existence import verify_existence  # noqa: E402
from verascite.verify_metadata import verify_metadata  # noqa: E402

HERE = Path(__file__).resolve().parent

#: Reporters that identify a real decision by a commercial database's own
#: number. No free archive holds them, so the governing rule requires
#: UNVERIFIED. Counted apart from the headline in both directions.
VENDOR_ONLY = {"WL", "LEXIS", "U.S. App. LEXIS", "U.S. Dist. LEXIS", "Westlaw"}

#: Narratives of the form "counsel cited X, which does not exist; the correct
#: case was Y" contain two citations, and only the first is fabricated. The
#: extractor cannot always tell which is which, so a row whose narrative talks
#: about a correction may carry the court's own fix rather than the fabrication
#: -- a real citation wearing a fabricated label.
#:
#: This is not a hypothesis. Of the citations the tool reported VERIFIED,
#: 19.6% sit in such a narrative against a ~9% base rate across the corpus, and
#: hand-reading them finds the court's correction rather than the defect:
#: "Iko v. Shreve, 535 F.3d 225" is real, and the fabrication in that record
#: was "Iko v. Shreve, 122 F.3d 707".
#:
#: Rows are flagged rather than dropped. Dropping them would quietly raise the
#: headline, which is the wrong direction to resolve an uncertainty in.
CORRECTION_LANGUAGE = re.compile(
    r"correct|likely meant|probably meant|intended authority|intended to cite|"
    r"identified only an unrelated|noting a different|should have cited|"
    r"miscit|the real case|actual case",
    re.IGNORECASE,
)


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def normalized_citations(rows: list[dict]) -> list[str]:
    """The distinct reporter citations to resolve, in a stable order.

    Extraction runs first so the lookup is keyed on what eyecite actually
    parsed, not on the curator's prose. A row whose citation does not parse
    contributes nothing to resolve and is reported unparsed later.
    """
    seen: list[str] = []
    for row in rows:
        entry = _extract_one(row["citation"])
        if entry is None:
            continue
        normalized = (entry.citation.normalized or entry.citation.raw_text or "").strip()
        if normalized and normalized not in seen:
            seen.append(normalized)
    return seen


def _extract_one(citation: str):
    """The single ledger entry for one corpus citation, or None."""
    text = f"The court considered {citation} in reaching its decision."
    document = Document(text=text, source_path="corpus", source_format="md")
    cites, notes = extract(document)
    ledger = resolve(document, cites, notes)
    return next(iter(ledger), None) if len(ledger) else None


def check_one(citation: str, offline: bool, client) -> dict:
    """Run the pipeline's deterministic stages over one citation string.

    The citation is embedded in a neutral carrier sentence because the
    extractor works on documents, not on bare strings, and a bare string
    changes what the short-form and antecedent logic sees.
    """
    text = f"The court considered {citation} in reaching its decision."
    document = Document(text=text, source_path="corpus", source_format="md")
    cites, notes = extract(document)
    ledger = resolve(document, cites, notes)
    if not len(ledger):
        return {"parsed": False, "overall": None, "reporter": None}
    verify_existence(ledger, client, offline=offline, fetch_courts=False)
    verify_metadata(ledger)
    entry = next(iter(ledger))
    checks = {
        name: check.verdict.value
        for name, check in entry.checks.items()
        if check.verdict is not Verdict.PENDING
    }
    return {
        "parsed": True,
        "kind": entry.citation.kind.value,
        "reporter": entry.citation.reporter,
        "overall": entry.overall.value,
        "checks": checks,
        "flagged": entry.overall is Overall.FLAGGED,
        "evidence": next(
            (c.evidence for c in entry.checks.values()
             if c.verdict is Verdict.FAIL and c.evidence), ""),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--citations", type=Path,
                        default=HERE / "fabricated_citations.jsonl")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--offline", action="store_true",
                        help="local checks only; no lookup is attempted")
    parser.add_argument("--cache-dir", type=Path, default=Path(".verascite-cache"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    rows = load(args.citations)
    if args.limit:
        rows = rows[: args.limit]

    client = None
    token = os.environ.get("COURTLISTENER_API_TOKEN")
    if not args.offline and token:
        from verascite.clients.courtlistener import CourtListenerClient
        client = CourtListenerClient(token=token, cache_dir=args.cache_dir)

        # Pass one: resolve every distinct citation in as few batched requests
        # as the endpoint allows, filling the cache. Pass two then scores each
        # citation without making a request.
        distinct = normalized_citations(rows)
        print(f"resolving {len(distinct)} distinct citation(s) in batches", flush=True)
        client.lookup(distinct)
        print(f"  requests made: {client.stats.get('requests', 0)}"
              f"  cache hits: {client.cache.hits}", flush=True)

    records = []
    for index, row in enumerate(rows, start=1):
        result = check_one(row["citation"], args.offline or not client, client)
        records.append({
            **{k: row[k] for k in ("citation", "reporter", "jurisdiction")},
            "narrative_mentions_a_correction": bool(
                CORRECTION_LANGUAGE.search(row.get("raw_item", ""))),
            **result,
        })
        if index % 50 == 0:
            print(f"  {index}/{len(rows)}", flush=True)

    summary = score(records, ran_lookup=bool(client))
    if client is not None:
        summary["requests_made"] = client.stats.get("requests", 0)
        summary["citations_looked_up"] = client.stats.get("citations_looked_up", 0)
    (args.out).write_text(json.dumps({"summary": summary, "records": records}, indent=2))
    print(json.dumps(summary, indent=2))
    return 0


def score(records: list[dict], ran_lookup: bool) -> dict:
    vendor = [r for r in records if (r.get("reporter") or "") in VENDOR_ONLY]
    scored = [r for r in records if (r.get("reporter") or "") not in VENDOR_ONLY]
    unparsed = [r for r in records if not r["parsed"]]

    flagged = [r for r in scored if r.get("flagged")]
    # The subset whose narrative does not discuss a correction, and which is
    # therefore less likely to carry the court's own fix in place of the
    # fabrication.
    uncontaminated = [r for r in scored if not r.get("narrative_mentions_a_correction")]
    uncontaminated_flagged = [r for r in uncontaminated if r.get("flagged")]
    return {
        "corpus_size": len(records),
        "lookup_performed": ran_lookup,
        "not_run_note": None if ran_lookup else (
            "No CourtListener credential was present, so existence resolution "
            "did not run. Only checks that need no lookup executed -- chiefly "
            "reporter validity. Every figure below describes that subset and "
            "must not be read as the tool's full recall on this corpus."
        ),
        "unparsed": len(unparsed),
        "vendor_only_excluded": len(vendor),
        "vendor_only_note": (
            "Vendor-database identifiers name real decisions that no free "
            "archive holds. The governing rule requires UNVERIFIED for them, so "
            "they are excluded from the headline in both directions rather than "
            "counted as misses or as catches."
        ),
        "scored": len(scored),
        "flagged": len(flagged),
        "detection_rate_on_scored": (
            round(len(flagged) / len(scored), 4) if scored else None
        ),
        "headline_is_not_recall": (
            "This is the share of corpus citations that were flagged, and it is a "
            "LOWER BOUND on recall rather than recall itself. The corpus labels a "
            "record, not always the citation extracted from it: some rows carry a "
            "real case (the court's correction, or a real case to which a "
            "fabricated quotation was attributed) under a fabricated-case-law tag. "
            "Every such row can only lower this figure, never raise it."
        ),
        "scored_without_correction_language": len(uncontaminated),
        "flagged_without_correction_language": len(uncontaminated_flagged),
        "detection_rate_without_correction_language": (
            round(len(uncontaminated_flagged) / len(uncontaminated), 4)
            if uncontaminated else None
        ),
        "correction_language_by_result": {
            result: round(
                sum(1 for r in scored
                    if r.get("overall") == result
                    and r.get("narrative_mentions_a_correction"))
                / max(1, sum(1 for r in scored if r.get("overall") == result)), 4)
            for result in sorted({r.get("overall") for r in scored} - {None})
        },
        "overall_distribution": dict(Counter(r.get("overall") for r in scored)),
        "vendor_only_distribution": dict(Counter(r.get("overall") for r in vendor)),
        "flagged_by_check": dict(Counter(
            name for r in flagged for name, verdict in (r.get("checks") or {}).items()
            if verdict == "FAIL")),
        "top_reporters_scored": dict(Counter(
            r.get("reporter") for r in scored).most_common(12)),
    }


if __name__ == "__main__":
    raise SystemExit(main())
