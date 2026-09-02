"""Score the deterministic layer against LePhantomCite.

Scoring follows PREREGISTRATION.md exactly. Nothing in this file may encode a
scoring decision not written there; if a case arises the protocol does not
cover, the run stops rather than improvising a rule that happens to help.

    COURTLISTENER_API_TOKEN=... python evals/lephantomcite/run_benchmark.py \
        --out evals/lephantomcite/results
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from verascite import config
from verascite.clients.cap import CapClient
from verascite.clients.courtlistener import CourtListenerClient, CourtListenerError
from verascite.clients.opinions import OpinionClient
from verascite.extract import extract
from verascite.ingest import _normalize
from verascite.models import Document
from verascite.resolve import resolve
from verascite.verdicts import Overall, Verdict
from verascite.verify_existence import verify_existence
from verascite.verify_metadata import verify_metadata
from verascite.verify_pincite import verify_pincite_by_page
from verascite.verify_quote import verify_quote

#: PREREGISTRATION.md: only FLAGGED counts as a positive detection.
POSITIVE = {Overall.FLAGGED}

CATEGORIES = (
    "non_existent_citation",
    "case_name_mismatch",
    "wrong_pincite",
    "misquote",
    "content_misrepresentation",
)


def load(path: Path) -> tuple[list[dict], list[dict]]:
    """Return (scored rows, excluded rows) per the protocol."""
    scored, excluded = [], []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if "list_hallucination_types" not in row:
            # Clean rows are scored; unattributable ones are excluded.
            if row.get("list_hallucinations"):
                excluded.append(row)
                continue
            row = dict(row, list_hallucination_types={})
        scored.append(row)
    return scored, excluded


def gold_spans(row: dict) -> list[tuple[int, int, str, list[str]]]:
    """(start, end, citation, types) for each hallucinated citation."""
    out = []
    text = _normalize(row["text"])
    for citation, types in (row.get("list_hallucination_types") or {}).items():
        start = text.find(_normalize(citation))
        if start == -1:
            continue
        out.append(
            (start, start + len(citation), citation,
             types if isinstance(types, list) else [types])
        )
    return out


def negative_spans(row: dict) -> list[tuple[int, int, str]]:
    """Citations in the segment that are not labelled hallucinated."""
    hallucinated = set(row.get("list_hallucination_types") or {})
    out = []
    text = _normalize(row["text"])
    for citation in row.get("citations_in_segment") or []:
        if citation in hallucinated:
            continue
        start = text.find(_normalize(citation))
        if start == -1:
            continue
        out.append((start, start + len(citation), citation))
    return out


def overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def cap_opinions(entry, cap_client, ledger=None):
    """Opinion text from the Caselaw Access Project, if it holds this case.

    A short form addresses a page *inside* the case -- "755 N.E.2d at 598" --
    while a case is indexed by the page it begins on. Looking a short form up
    by its own page therefore never matches, and short forms carry most of the
    quotation and pincite labels in this corpus. The antecedent's page is what
    identifies the case.
    """
    if cap_client is None:
        return [], None

    citation = entry.citation
    volume, reporter, page = citation.volume, citation.reporter, citation.page
    if ledger is not None and entry.antecedent_id:
        head, hops = entry, 0
        while head.antecedent_id and hops < 10:
            parent = ledger.get(head.antecedent_id)
            if parent is None:
                break
            head, hops = parent, hops + 1
        if head.citation.volume and head.citation.page:
            volume, reporter, page = (
                head.citation.volume, head.citation.reporter, head.citation.page
            )
    pincite = False
    if not entry.antecedent_id and citation.kind.value == "short_case":
        # A short form whose antecedent is not in the document -- ordinary in
        # an excerpt that begins after the full citation. Its page addresses
        # somewhere inside the case, so a lookup by first page cannot match;
        # the volume's page ranges can still say which case contains it.
        pincite = True

    if not (volume and reporter and page):
        return [], None
    try:
        found = cap_client.opinions_for(volume, reporter, page, page_is_pincite=pincite)
    except Exception:
        found = ([], None)
    if found[0]:
        return found

    # The same decision is printed in several reporters, and CAP does not hold
    # them all: it has U.S. but not S. Ct., so "137 S. Ct. 1045" is unreachable
    # under its own reporter and reachable as "581 U.S. 1". The pincite cannot
    # travel with it -- page 1045 of S. Ct. is not page 1045 of U.S. -- so the
    # parallel is used only to reach the text, never to locate a page in it.
    head_entry = entry
    if ledger is not None and entry.antecedent_id:
        parent = ledger.get(entry.antecedent_id)
        if parent is not None:
            head_entry = parent
    resolution = getattr(head_entry, "resolved_to", None)
    for parallel in getattr(resolution, "parallel_citations", None) or []:
        parts = parallel.rsplit(" ", 1)
        if len(parts) != 2:
            continue
        head, other_page = parts
        other_volume, _, other_reporter = head.partition(" ")
        if other_reporter.strip() == (reporter or "").strip():
            continue
        try:
            opinions, case = cap_client.opinions_for(
                other_volume, other_reporter, other_page
            )
        except Exception:
            continue
        if opinions:
            return opinions, case
    return [], None


def audit(text: str, client, opinion_client, do_quotes: bool, cap_client=None):
    # The same normalisation ingest() applies to a real document. It is
    # length-preserving, so gold spans located by offset in the raw text stay
    # valid against the normalised text.
    text = _normalize(text)
    document = Document(
        text=text, source_path="benchmark", source_format="txt",
        page_map=[(0, len(text), 1)],
    )
    citations, notes = extract(document)
    ledger = resolve(document, citations, notes)
    verify_existence(ledger, client, offline=client is None, fetch_courts=False)
    # Names the Caselaw Access Project holds for the same citations. An
    # independent second source turns a disagreement with CourtListener from a
    # finding into a reason to look.
    corroboration = {}
    if cap_client is not None:
        for entry in ledger:
            citation = entry.citation
            if not (citation.volume and citation.reporter and citation.page):
                continue
            if entry.antecedent_id:
                continue
            try:
                case = cap_client.find(citation.volume, citation.reporter, citation.page)
            except Exception:
                case = None
            if case:
                corroboration[entry.citation_id] = [
                    n for n in (case.name_abbreviation, case.name) if n
                ]
    verify_metadata(ledger, corroboration)

    # Pincite against star-paginated page text. Independent of quotation
    # checking: it asks only whether the quoted words sit on the page the
    # citation names, which is exactly what a pincite asserts.
    if cap_client is not None:
        for entry in ledger:
            if not (entry.citation.pin_cite and entry.citation.quoted_language):
                continue
            _ops, case = cap_opinions(entry, cap_client, ledger)
            if case is None:
                continue
            # The text may have been reached through a parallel reporter. Page
            # 1045 of S. Ct. is not page 1045 of U.S., so a pincite compared
            # against another reporter's pagination is guaranteed to disagree
            # and the disagreement means nothing.
            # A short form carries no reporter of its own -- "Id. at 2586"
            # inherits it from the citation it follows -- so the antecedent
            # chain is walked to find the reporter actually being cited.
            head, hops = entry, 0
            while head.citation.reporter is None and head.antecedent_id and hops < 10:
                parent = ledger.get(head.antecedent_id)
                if parent is None:
                    break
                head, hops = parent, hops + 1
            try:
                wanted_slug = cap_client.reporter_slug(head.citation.reporter)
            except Exception:
                wanted_slug = None
            if wanted_slug and case.reporter_slug != wanted_slug:
                continue
            try:
                verify_pincite_by_page(entry, cap_client.page_texts_for(case))
            except Exception:
                pass

    if do_quotes:
        by_cluster: dict[int, list] = {}
        for entry in ledger:
            if not entry.citation.quoted_language:
                continue
            # The Caselaw Access Project is tried first: it is unmetered, so it
            # can supply text for every authority rather than the handful a
            # 125-request daily budget allows.
            opinions, _case = cap_opinions(entry, cap_client, ledger)
            if opinions:
                verify_quote(entry, opinions)
            elif entry.resolved_to and entry.resolved_to.cluster_id and opinion_client:
                by_cluster.setdefault(entry.resolved_to.cluster_id, []).append(entry)
            else:
                verify_quote(entry, [])
        for cluster_id, entries in by_cluster.items():
            opinions = opinion_client.fetch_for_cluster(cluster_id)
            for entry in entries:
                verify_quote(entry, opinions)
    return ledger


def prewarm_lookups(rows: list[dict], client) -> dict:
    """Look up every distinct citation in the whole eval set, up front.

    The first attempt at this harness called the pipeline per excerpt, which
    issued 390 separate lookup requests and was throttled to death in four
    minutes. Batching is not an optimisation here: 1,240 citations in five
    250-citation requests costs five requests, while the same citations sent a
    few at a time costs 390. The per-excerpt pass afterwards is served entirely
    from cache.
    """
    distinct: dict[str, None] = {}
    for row in rows:
        text = _normalize(row["text"])
        document = Document(
            text=text, source_path="benchmark", source_format="txt",
            page_map=[(0, len(text), 1)],
        )
        citations, notes = extract(document)
        ledger = resolve(document, citations, notes)
        for entry in ledger:
            if entry.antecedent_id or not entry.citation.normalized:
                continue
            if entry.citation.kind.value in ("law", "journal"):
                continue
            if CourtListenerClient.is_vendor_only(entry.citation.reporter):
                continue
            distinct.setdefault(entry.citation.normalized, None)

    citations = list(distinct)
    print(f"pre-warming {len(citations)} distinct citations "
          f"in {(len(citations) + 249) // 250} batched request(s)...", flush=True)
    started = time.time()
    try:
        results = client.lookup(citations)
    except CourtListenerError as exc:
        # Proceed on whatever is cached rather than losing the run; the gap is
        # reported in the results file.
        print(f"  pre-warm incomplete: {exc}", flush=True)
        results = {}
    print(f"  done in {time.time() - started:.0f}s, "
          f"{client.stats['requests']} request(s)", flush=True)
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path,
                        default=Path(__file__).parent / "eval.jsonl")
    parser.add_argument("--out", type=Path,
                        default=Path(__file__).parent / "results")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--no-quotes", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--cache-dir", type=Path, default=Path(".verascite-cache"))
    parser.add_argument(
        "--no-cap", action="store_true",
        help="do not consult the Caselaw Access Project",
    )
    parser.add_argument(
        "--cache-only", action="store_true",
        help="serve everything from the local cache; make no requests",
    )
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    rows, excluded = load(args.data)
    if args.limit:
        rows = rows[: args.limit]

    cap_client = None if args.no_cap else CapClient(cache_dir=args.cache_dir)
    client = (
        None if args.offline
        else CourtListenerClient(cache_dir=args.cache_dir, cache_only=args.cache_only)
    )
    opinion_client = (
        None if (args.offline or args.no_quotes or not client or not client.can_serve)
        else OpinionClient(client, cache_dir=args.cache_dir)
    )

    if client is not None and client.can_serve:
        prewarm_lookups(rows, client)

    tp = fp = fn = 0
    per_category = {c: {"gold": 0, "detected": 0} for c in CATEGORIES}
    verdicts = collections.Counter()
    absent_negatives = 0
    absent_negatives_flagged = 0
    records = []
    failed: list[dict] = []
    started = time.time()

    for index, row in enumerate(rows, start=1):
        try:
            ledger = audit(row["text"], client, opinion_client, not args.no_quotes,
                           cap_client)
        except CourtListenerError as exc:
            # P6: an API failure is not a verdict. Record the excerpt as
            # unprocessed rather than scoring it on partial evidence.
            failed.append({"excerpt": index, "error": str(exc)})
            continue
        # V2_PROTOCOL.md item 3, as corrected in V3_PROTOCOL.md.
        #
        # Each flagged citation keeps its OWN span. Collapsing back-references
        # onto their antecedent was wrong: the benchmark labels short forms
        # too, so remapping a detection made on `755 N.E.2d at 598` onto its
        # antecedent both lost the detection and created a false positive from
        # the same finding.
        #
        # Double counting is instead avoided at the false-positive step: a
        # flagged back-reference whose antecedent was itself matched is part of
        # that one detection, not a separate error.
        flagged = [
            (e.citation.location.char_start, e.citation.location.char_end, e)
            for e in ledger
            if e.overall in POSITIVE
        ]

        for entry in ledger:
            verdicts[entry.overall.value] += 1

        golds = gold_spans(row)
        matched_predictions = set()
        for start, end, citation, types in golds:
            hit = None
            for pindex, (pstart, pend, entry) in enumerate(flagged):
                if overlaps((start, end), (pstart, pend)):
                    hit = pindex
                    break
            for category in types:
                if category in per_category:
                    per_category[category]["gold"] += 1
                    if hit is not None:
                        per_category[category]["detected"] += 1
            if hit is not None:
                tp += 1
                matched_predictions.add(hit)
            else:
                fn += 1

        negatives = negative_spans(row)
        for start, end, citation in negatives:
            entry = next(
                (e for e in ledger
                 if overlaps((start, end),
                             (e.citation.location.char_start,
                              e.citation.location.char_end))),
                None,
            )
            if entry is None:
                continue
            existence = entry.checks.get("existence")
            if existence is not None and existence.verdict in (
                Verdict.NOT_FOUND, Verdict.OUT_OF_SCOPE
            ):
                absent_negatives += 1
                if entry.overall in POSITIVE:
                    absent_negatives_flagged += 1

        matched_ids = {flagged[i][2].citation_id for i in matched_predictions}
        for pindex, (pstart, pend, entry) in enumerate(flagged):
            if pindex in matched_predictions:
                continue
            # Walk the antecedent chain: an inherited flag on `id.` is the same
            # finding as the flag on the citation it points at.
            head, hops, inherited = entry, 0, False
            while head.antecedent_id and hops < 10:
                if head.antecedent_id in matched_ids:
                    inherited = True
                    break
                head = ledger.get(head.antecedent_id)
                if head is None:
                    break
                hops += 1
            if inherited:
                continue
            fp += 1
            records.append({
                "excerpt": index,
                "false_positive": entry.citation.raw_text,
                "why": {
                    n: (c.evidence or c.reason)
                    for n, c in entry.checks.items()
                    if c.verdict is Verdict.FAIL
                },
            })

        if index % 10 == 0:
            print(f"  {index}/{len(rows)} excerpts  tp={tp} fp={fp} fn={fn}  "
                  f"{time.time()-started:.0f}s", flush=True)

        if index % 25 == 0:
            (args.out / "progress.json").write_text(json.dumps({
                "excerpts_done": index, "of": len(rows),
                "tp": tp, "fp": fp, "fn": fn,
                "elapsed_seconds": round(time.time() - started, 1),
            }, indent=2))

    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0

    results = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "protocol": "evals/lephantomcite/PREREGISTRATION.md",
        "thresholds_version": config.THRESHOLDS_VERSION,
        "thresholds": config.snapshot(),
        "layer_under_test": "deterministic only (no model-assisted checks)",
        "quotes_checked": not args.no_quotes,
        "offline": args.offline,
        "excerpts_scored": len(rows),
        "excerpts_excluded": len(excluded),
        "excerpts_failed_on_api_error": len(failed),
        "counts": {"tp": tp, "fp": fp, "fn": fn},
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "false_positive_rate_on_absent_negatives": (
            round(absent_negatives_flagged / absent_negatives, 4)
            if absent_negatives else None
        ),
        "absent_negatives_subset_size": absent_negatives,
        "absent_negatives_flagged": absent_negatives_flagged,
        "per_category_recall": {
            name: {
                "gold": data["gold"],
                "detected": data["detected"],
                "recall": round(data["detected"] / data["gold"], 4) if data["gold"] else None,
            }
            for name, data in per_category.items()
        },
        "verdict_distribution": dict(verdicts),
        "wall_clock_seconds": round(time.time() - started, 1),
        "requests": client.stats["requests"] if client else 0,
        "cap_requests": cap_client.stats["requests"] if cap_client else 0,
        "cache_only": bool(args.cache_only),
        "citations_not_looked_up": (
            client.stats.get("cache_only_misses", 0) if client else 0
        ),
    }

    (args.out / "results.json").write_text(json.dumps(results, indent=2))
    (args.out / "false_positives.json").write_text(json.dumps(records, indent=2))
    print(json.dumps({k: v for k, v in results.items()
                      if k not in ("thresholds", "verdict_distribution")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
