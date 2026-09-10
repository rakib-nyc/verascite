"""The false-accusation rate: what a verifier does with a citation it cannot find.

**Why this is the measurement that matters.** Roughly one citation in ten in a
real appellate brief is absent from the free archives -- not fabricated, not
defective, simply not there. Recent decisions, unpublished dispositions, state
trial court orders and vendor-only identifiers are missing as a matter of
routine. A verifier that treats absence as fabrication therefore does not fail
occasionally; it fails on a tenth of a lawyer's citations, and the failures land
on citations that were fine.

Published figures for that failure are stark. Confronted with real citations
their corpus did not contain, one frontier model flagged **65.9%** of them as
hallucinated and another flagged **25%**. Those are not detection errors. They
are accusations against sound authority, which is the expensive mistake in legal
practice and the one this project exists to avoid.

**What is measured here.** Every citation in the benchmark corpus that carries
no defect label is a citation that is fine. The subset of those that the sources
consulted do not contain is the test set: for each one, the correct behaviour is
to report it absent and name what was searched, and the incorrect behaviour is
to report a finding. The rate of the latter is the number.

**Why it is reported rather than the recall.** Recall on this set is undefined,
because there is nothing to detect. The set contains no defects. Any flag raised
here is wrong by construction, which is what makes it a clean measurement --
there is no judgement call about whether a given flag was justified.

**Cost.** Two passes. The first resolves every distinct citation in batches and
fills the cache; the second scores against the cache and makes no request.
Re-running afterwards is free.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from verascite.extract import extract  # noqa: E402
from verascite.models import Document  # noqa: E402
from verascite.resolve import resolve  # noqa: E402
from verascite.verdicts import Overall, Verdict  # noqa: E402
from verascite.verify_existence import verify_existence  # noqa: E402
from verascite.verify_metadata import verify_metadata  # noqa: E402

#: Statuses meaning the sources consulted do not contain the citation. 404 is a
#: valid citation the database lacks; the no-response codes mean nothing came
#: back at all. Both are "absent from what was searched" and neither is
#: evidence about the citation.
ABSENT_STATUSES = {404}


def clean_citations(path: Path) -> list[dict]:
    """Every benchmark citation carrying no defect label, deduplicated.

    ``list_hallucination_types`` maps a citation string to its labels and is
    used rather than ``list_hallucinations``, which also keys on quoted
    passages and category names that are not citations at all.
    """
    seen: dict[str, dict] = {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        labelled = set((row.get("list_hallucination_types") or {}).keys())
        for citation in (row.get("citations_in_segment") or []):
            if citation in labelled or citation in seen:
                continue
            seen[citation] = {"citation": citation, "filename": row.get("filename", "")}
    return list(seen.values())


def check(citation: str, client) -> dict:
    """Run the deterministic stages over one citation in a neutral carrier."""
    text = f"The court considered {citation} in reaching its decision."
    document = Document(text=text, source_path="abstention", source_format="md")
    cites, notes = extract(document)
    ledger = resolve(document, cites, notes)
    if not len(ledger):
        return {"parsed": False}
    verify_existence(ledger, client, offline=False, fetch_courts=False)
    verify_metadata(ledger)
    entry = next(iter(ledger))
    checks = {
        name: c.verdict.value
        for name, c in entry.checks.items()
        if c.verdict is not Verdict.PENDING
    }
    existence = entry.checks.get("existence")
    # Two verdicts mean "the sources consulted cannot speak to this": NOT_FOUND
    # (searched, not held) and OUT_OF_SCOPE (a vendor-only identifier or an
    # authority type no free archive carries). They are one category from the
    # reader's side -- nobody confirmed it -- and both must abstain rather than
    # accuse, so both belong in the test set.
    unreachable = {Verdict.NOT_FOUND, Verdict.OUT_OF_SCOPE}
    return {
        "parsed": True,
        "overall": entry.overall.value,
        "checks": checks,
        "flagged": entry.overall is Overall.FLAGGED,
        "absent": existence is not None and existence.verdict in unreachable,
        "absent_kind": existence.verdict.value if existence is not None else None,
        "names_sources": bool(existence and existence.sources_consulted),
        "evidence": next(
            (c.evidence for c in entry.checks.values()
             if c.verdict is Verdict.FAIL and c.evidence), ""),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--eval", type=Path,
                        default=Path.home() / "Documents/verascite/evals/lephantomcite/eval.jsonl")
    parser.add_argument("--cache-dir", type=Path, default=Path(".verascite-cache"))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    rows = clean_citations(args.eval)
    if args.limit:
        rows = rows[: args.limit]
    print(f"clean (unlabelled) citations in corpus: {len(rows)}", flush=True)

    token = os.environ.get("COURTLISTENER_API_TOKEN")
    if not token:
        print("error: this measurement requires COURTLISTENER_API_TOKEN", file=sys.stderr)
        return 2
    from verascite.clients.courtlistener import CourtListenerClient
    client = CourtListenerClient(token=token, cache_dir=args.cache_dir)

    # Pass one: fill the cache in as few batched requests as the endpoint allows.
    distinct = []
    for row in rows:
        document = Document(text=f"The court considered {row['citation']} in reaching its decision.",
                            source_path="x", source_format="md")
        cites, notes = extract(document)
        ledger = resolve(document, cites, notes)
        entry = next(iter(ledger), None) if len(ledger) else None
        if entry is None:
            continue
        normalized = (entry.citation.normalized or entry.citation.raw_text or "").strip()
        if normalized and normalized not in distinct:
            distinct.append(normalized)
    print(f"resolving {len(distinct)} distinct citation(s) in batches", flush=True)
    client.lookup(distinct)
    print(f"  requests: {client.stats.get('requests', 0)}  "
          f"cache hits: {client.cache.hits}", flush=True)

    records = []
    for index, row in enumerate(rows, start=1):
        records.append({**row, **check(row["citation"], client)})
        if index % 200 == 0:
            print(f"  {index}/{len(rows)}", flush=True)

    summary = score(records)
    summary["requests_made"] = client.stats.get("requests", 0)
    args.out.write_text(json.dumps({"summary": summary, "records": records}, indent=2))
    print(json.dumps(summary, indent=2))
    return 0


def score(records: list[dict]) -> dict:
    scored = [r for r in records if r.get("parsed")]
    absent = [r for r in scored if r.get("absent")]
    found = [r for r in scored if not r.get("absent")]

    false_accusations = [r for r in absent if r.get("flagged")]
    flagged_on_found = [r for r in found if r.get("flagged")]

    return {
        "clean_citations_tested": len(records),
        "unparsed": len(records) - len(scored),
        "scored": len(scored),
        "absent_from_sources": len(absent),
        "absent_share": round(len(absent) / len(scored), 4) if scored else None,
        # The headline. Every flag on this subset is wrong by construction.
        "false_accusations_on_absent": len(false_accusations),
        "false_accusation_rate": (
            round(len(false_accusations) / len(absent), 4) if absent else None
        ),
        "every_absent_names_its_sources": all(r.get("names_sources") for r in absent),
        # Flags on citations the corpus DID contain are a different quantity: the
        # benchmark labels only injected defects, so a genuine pre-existing error
        # in a filed brief scores against the tool that finds it. Reported apart.
        "flagged_among_found": len(flagged_on_found),
        "flagged_among_found_note": (
            "Not false accusations by construction. The benchmark labels only "
            "injected errors, so an error already present in the filed brief "
            "carries no label. Verify by hand before counting these either way."
        ),
        "absent_by_kind": dict(Counter(r.get("absent_kind") for r in absent)),
        "absent_result_distribution": dict(Counter(r.get("overall") for r in absent)),
        "found_result_distribution": dict(Counter(r.get("overall") for r in found)),
        "reference_points": {
            "gemini_false_flag_rate": 0.659,
            "gpt5_false_flag_rate": 0.25,
            "source": "Liu, Stammbach & Henderson, arXiv:2606.21155, on 132 "
                      "real-but-absent citations. Different test set: comparable "
                      "in task, not item-for-item.",
        },
    }


if __name__ == "__main__":
    raise SystemExit(main())
