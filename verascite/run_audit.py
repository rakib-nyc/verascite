"""CLI entry point for the deterministic verification core (M1).

Runs ingest -> extract -> resolve -> existence -> metadata and writes a
checkpointed ``ledger.json``. Every stage here is deterministic: no model is
in the loop, and the same document produces the same verdicts every run.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

if __package__ in (None, ""):  # allow `python verascite/run_audit.py`
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from verascite.clients.courtlistener import CourtListenerClient, CourtListenerError
from verascite.clients.opinions import OpinionClient
from verascite.extract import extract
from verascite.ingest import ingest
from verascite.ledger import Ledger
from verascite.resolve import resolve
from verascite.verdicts import CheckResult, Overall, Verdict
from verascite.verify_existence import verify_existence
from verascite.verify_metadata import verify_metadata
from verascite.annotate import AnnotationError, annotate_docx
from verascite.report import write_report
from verascite.safety import UnsafeDocument
from verascite.verify_proposition import verify_proposition
from verascite.verify_treatment import verify_treatment
from verascite.verify_quote import verify_quote

DISCLOSURE = """\
This is an evidence package, not a certification. It does not certify any
filing, does not replace an editorial citator (Shepard's, KeyCite, BCite),
and is not legal advice. The reviewing attorney retains full responsibility
under Rule 11 and the applicable rules of professional conduct.

UNVERIFIED does not mean fabricated. It means the sources consulted do not
contain the authority. That is routine for recent decisions, unpublished
dispositions, state trial courts, and Westlaw/Lexis-only identifiers."""

BANNER = {
    Overall.FLAGGED: "FLAGGED    contradicted by a retrieved source -- fix before filing",
    Overall.UNVERIFIED: "UNVERIFIED absent from the sources consulted -- check manually, NOT a fabrication finding",
    Overall.REVIEW: "REVIEW     needs a human read",
    Overall.PENDING: "PENDING    not checked",
    Overall.VERIFIED: "VERIFIED   confirmed on every applicable dimension",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="verascite",
        description="Verify legal citations in a document against primary sources.",
        epilog="Verdicts are evidence for a human decision. This tool certifies nothing.",
    )
    parser.add_argument("document", type=Path, help="brief, motion, memo (.docx/.pdf/.txt/.md)")
    parser.add_argument("--out", type=Path, default=Path("verascite-out"), help="output directory")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="local checks only; no citation string leaves the machine",
    )
    parser.add_argument(
        "--deterministic-only",
        action="store_true",
        default=True,
        help="run only the no-model pipeline (M1 default; model layer lands in M5)",
    )
    parser.add_argument("--cache-dir", type=Path, default=Path(".verascite-cache"))
    parser.add_argument("--no-cache", action="store_true", help="bypass the disk cache")
    parser.add_argument("--token", default=None, help="CourtListener API token (else $COURTLISTENER_API_TOKEN)")
    parser.add_argument(
        "--no-court-lookup",
        action="store_true",
        help=(
            "skip the docket fetch that resolves each authority's court "
            "(one request per authority against a 5/min ceiling)"
        ),
    )
    parser.add_argument(
        "--no-quotes",
        action="store_true",
        help="skip quote verification (avoids fetching full opinion text)",
    )
    parser.add_argument(
        "--no-annotate",
        action="store_true",
        help="skip writing the annotated copy of a .docx source",
    )
    parser.add_argument("--resume", action="store_true", help="reuse an existing ledger.json in --out")
    parser.add_argument("--quiet", action="store_true")
    return parser


def summarize(ledger: Ledger, stream=sys.stdout) -> None:
    counts = ledger.counts()
    write = stream.write

    write("\n" + "=" * 76 + "\n")
    write(f"verascite -- citation audit of {ledger.document_path}\n")
    write("=" * 76 + "\n")
    write(DISCLOSURE + "\n")
    write("-" * 76 + "\n")

    authorities = ledger.run_meta.get("distinct_authorities", 0)
    write(
        f"\n{len(ledger)} citation{'' if len(ledger) == 1 else 's'} extracted, "
        f"{authorities} distinct "
        f"{'authority' if authorities == 1 else 'authorities'}.\n\n"
    )
    for overall in (Overall.FLAGGED, Overall.UNVERIFIED, Overall.REVIEW,
                    Overall.PENDING, Overall.VERIFIED):
        write(f"  {counts[overall.value]:4d}  {BANNER[overall]}\n")

    for note in ledger.run_meta.get("extraction_notes", []):
        write(f"\n  note: {note}")
    for warning in ledger.run_meta.get("document_warnings", []):
        write(f"\n  warning: {warning}")
    write("\n")

    interesting = [e for e in ledger.sorted_by_severity()
                   if e.overall in (Overall.FLAGGED, Overall.UNVERIFIED, Overall.REVIEW)]
    if not interesting:
        return

    write("\n" + "-" * 76 + "\n")
    for entry in interesting:
        loc = entry.citation.location
        where = f"page {loc.page}" if loc.page else f"char {loc.char_start}"
        if loc.in_footnote:
            where += ", footnote"
        write(f"\n[{entry.overall.value}] {entry.citation.raw_text}\n")
        write(f"    {entry.citation_id} @ {where}\n")
        for dimension, check in entry.checks.items():
            if check.verdict in (Verdict.PASS, Verdict.NA, Verdict.PENDING):
                continue
            detail = check.evidence or check.reason or ""
            write(f"    - {dimension}: {check.verdict.value}\n")
            if detail:
                for line in _wrap(detail, 68):
                    write(f"        {line}\n")
    write("\n")


def _verify_quotes(ledger, client, ledger_path, quiet: bool = False) -> None:
    """Fetch opinion text once per authority and check every quote against it."""
    opinion_client = OpinionClient(client)
    needs_text = [e for e in ledger if e.citation.quoted_language]
    by_cluster: dict[int, list] = {}
    for entry in needs_text:
        if entry.resolved_to and entry.resolved_to.cluster_id:
            by_cluster.setdefault(entry.resolved_to.cluster_id, []).append(entry)
        else:
            verify_quote(entry, [])

    for cluster_id, entries in by_cluster.items():
        opinions = opinion_client.fetch_for_cluster(cluster_id)
        for entry in entries:
            verify_quote(entry, opinions)
        ledger.checkpoint(ledger_path)  # long runs must survive interruption

    checked = sum(
        len(e.citation.quoted_language)
        for e in ledger
        if e.checks.get("quote") is not None
        and e.checks["quote"].verdict in (Verdict.PASS, Verdict.FAIL)
    )
    ledger.run_meta.setdefault("quotes", {})["checked_against_source"] = checked
    ledger.run_meta["quote_check"] = {
        "authorities_with_quotes": len(by_cluster),
        "opinion_versions_fetched": opinion_client.stats["fetched"],
        "cache_hits": opinion_client.stats["cache_hits"],
        "failed": opinion_client.stats["failed"],
    }
    ledger.checkpoint(ledger_path)


def _wrap(text: str, width: int) -> list[str]:
    import textwrap
    return textwrap.wrap(" ".join(text.split()), width=width) or [""]


#: Exit codes. Distinct because a pipeline needs to tell "this brief has a
#: problem" from "this tool could not run".
EXIT_OK = 0
EXIT_FLAGGED = 1
EXIT_INPUT_ERROR = 2
EXIT_INTERRUPTED = 130


def main(argv: list[str] | None = None) -> int:
    """Entry point with human-readable failures.

    A traceback is a reasonable thing to show a developer and an unreasonable
    thing to show a lawyer on a filing deadline. Expected failures -- a missing
    file, an unreadable format, a hostile document -- print one line and exit
    with a code that distinguishes them from findings.
    """
    try:
        return _run(build_parser().parse_args(argv))
    except FileNotFoundError as exc:
        print(f"error: no such file: {exc}", file=sys.stderr)
        return EXIT_INPUT_ERROR
    except UnsafeDocument as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_INPUT_ERROR
    except PermissionError as exc:
        print(f"error: permission denied: {exc}", file=sys.stderr)
        return EXIT_INPUT_ERROR
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_INPUT_ERROR
    except KeyboardInterrupt:
        print("\ninterrupted. Re-run with --resume to continue from the ledger.",
              file=sys.stderr)
        return EXIT_INTERRUPTED


def _run(args) -> int:
    args.out.mkdir(parents=True, exist_ok=True)
    ledger_path = args.out / "ledger.json"

    if args.resume and ledger_path.exists():
        ledger = Ledger.load(ledger_path)
        if not args.quiet:
            print(f"resumed {len(ledger)} citation(s) from {ledger_path}")
    else:
        document = ingest(args.document)
        citations, notes = extract(document)
        ledger = resolve(document, citations, notes)
        ledger.checkpoint(ledger_path)

    client = None
    if not args.offline:
        client = CourtListenerClient(
            token=args.token,
            cache_dir=None if args.no_cache else args.cache_dir,
        )
        if not client.can_serve and not args.quiet:
            print(
                "note: COURTLISTENER_API_TOKEN is not set. Running local checks "
                "only; existence will be reported NOT_CHECKABLE rather than "
                "guessed.",
                file=sys.stderr,
            )

    ledger.sources_available = ["reporters_db", "courts_db"] + (
        ["courtlistener_citation_lookup"] if client and client.has_token else []
    )

    try:
        verify_existence(
            ledger, client, offline=args.offline,
            fetch_courts=not args.no_court_lookup,
        )
    except CourtListenerError as exc:
        # P6: an API failure is never a verdict. Record it and keep the
        # citations unverified rather than passing or failing them.
        ledger.run_meta["existence_lookup_error"] = str(exc)
        print(f"error: {exc}", file=sys.stderr)
    ledger.checkpoint(ledger_path)

    verify_metadata(ledger)
    ledger.checkpoint(ledger_path)

    if not args.offline and not args.no_quotes and client and client.has_token:
        _verify_quotes(ledger, client, ledger_path, quiet=args.quiet)
    else:
        for entry in ledger:
            if entry.citation.quoted_language:
                entry.set_check(
                    "quote",
                    CheckResult(
                        Verdict.NOT_CHECKABLE,
                        reason=(
                            "quote verification requires retrieving the opinion text, "
                            "which was not performed in this run"
                        ),
                    ),
                )
        ledger.checkpoint(ledger_path)

    # Proposition support needs a model doing grounded reading. The CLI has
    # none by design -- the deterministic layer must stay runnable with no
    # model at all -- so the dimension is recorded as unchecked rather than
    # quietly omitted. An agent session running the packaged skill supplies the
    # reader and fills these in.
    for entry in ledger:
        if entry.citation.kind.value in ("full_case", "short_case", "id", "supra"):
            verify_proposition(entry, [], ask=None)
            verify_treatment(entry)
    ledger.checkpoint(ledger_path)

    report_path = args.out / "report.md"
    write_report(ledger, report_path)

    annotated_path = None
    if not args.no_annotate and args.document.suffix.lower() == ".docx":
        annotated_path = args.out / f"annotated-{args.document.name}"
        try:
            count = annotate_docx(args.document, ledger, annotated_path)
        except (AnnotationError, KeyError, OSError) as exc:
            # The report is the deliverable; annotation is a convenience and
            # must never take the run down with it.
            print(f"note: could not annotate the document: {exc}", file=sys.stderr)
            annotated_path = None
        else:
            if count == 0:
                annotated_path = None

    if not args.quiet:
        summarize(ledger)
    print(f"ledger  {ledger_path}")
    print(f"report  {report_path}")
    if annotated_path:
        print(f"marked  {annotated_path}")

    return EXIT_FLAGGED if ledger.by_overall(Overall.FLAGGED) else EXIT_OK


def audit_document(
    document: Path,
    out_dir: Path = Path("verascite-out"),
    *,
    offline: bool = False,
    deterministic_only: bool = False,
    token: str | None = None,
    cache_dir: Path | None = Path(".verascite-cache"),
    quotes: bool = True,
    annotate: bool = True,
    quiet: bool = True,
) -> Ledger:
    """Audit one document and return the ledger. The library entry point.

    Writes ``report.md`` and ``ledger.json`` into ``out_dir``, exactly as the
    command line does, and returns the in-memory ledger so a caller can act on
    the verdicts without re-reading the JSON.

    ``offline=True`` performs local checks only: no citation string leaves the
    machine. Existence is then reported NOT_CHECKABLE rather than guessed --
    absence of a lookup is not evidence about the citation.
    """
    args = build_parser().parse_args([str(document), "--out", str(out_dir)])
    args.offline = offline
    args.deterministic_only = deterministic_only
    args.token = token
    args.no_cache = cache_dir is None
    args.cache_dir = cache_dir or Path(".verascite-cache")
    args.no_quotes = not quotes
    args.no_annotate = not annotate
    args.quiet = quiet
    args.resume = False
    _run(args)
    return Ledger.load(out_dir / "ledger.json")


if __name__ == "__main__":
    raise SystemExit(main())
