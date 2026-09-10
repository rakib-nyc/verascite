"""CLI entry point.

Runs ingest -> extract -> resolve -> existence -> metadata -> quotes and writes
a checkpointed ``ledger.json``.

Every stage listed above is deterministic: the same document and the same
sources produce the same verdicts every run. That property is the reason the
tool is usable in an adversarial setting, and it is the default.

One optional stage is not deterministic. Grounded reading -- deciding whether
an opinion supports the proposition it was cited for -- requires a model, and
content misrepresentation is the largest defect class and is invisible without
it. It runs only when ``--model`` names a backend, it is recorded in the ledger
with the backend and model that produced it, and every verdict it can reach is
bounded by the interlocks in ``verify_proposition``. Nothing about the
deterministic layer changes when it is switched on: a model may add a finding
that has retrieved evidence behind it, and it may never clear one.
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
from verascite.clients.opinions import OpinionClient, page_texts_for
from verascite.clients.statutes import StatuteClient
from verascite.coverage import measure as measure_coverage
from verascite.plain import write_plain
from verascite.extract import extract
from verascite.ingest import ingest
from verascite.ledger import Ledger
from verascite.resolve import resolve
from verascite.verdicts import CheckResult, Overall, Verdict
from verascite.verify_existence import verify_existence
from verascite.verify_metadata import verify_metadata
from verascite.annotate import AnnotationError, annotate_docx
from verascite.batch import find_documents, run_batch, write_summary
from verascite.readers import (
    BACKENDS,
    DEFAULT_MAX_TOKENS,
    DEFAULT_TIMEOUT_S,
    DEFAULT_TOKEN_ENV,
    CostRates,
    ReaderError,
    build_reader,
    parse_model_params,
)
from verascite.record import write_record
from verascite.report import write_report
from verascite.safety import UnsafeDocument
from verascite.verify_proposition import verify_proposition
from verascite.verify_treatment import verify_treatment
from verascite.verify_quote import verify_quote
from verascite.verify_agreement import verify_agreement
from verascite.verify_statute import verify_statute

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
    parser.add_argument("document", type=Path,
                        help="brief, motion, memo (.docx/.pdf/.txt/.md), or a directory with --batch")
    parser.add_argument(
        "--batch",
        action="store_true",
        help=(
            "treat the path as a directory and review every document under it, "
            "writing one consolidated report. Runs are sequential and share the "
            "cache, because the archives' rate limits are shared too"
        ),
    )
    parser.add_argument(
        "--no-recurse",
        action="store_true",
        help="with --batch, do not descend into subdirectories",
    )
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
        help=(
            "run only the reproducible no-model pipeline. This is the default; "
            "the flag exists so a script can state the requirement explicitly"
        ),
    )

    reading = parser.add_argument_group(
        "grounded reading (optional, not deterministic)",
        "Reads the retrieved opinion to decide whether it supports the "
        "proposition it was cited for. Off unless --model names a backend. "
        "Use --model local to keep every citation, proposition and opinion on "
        "this machine.",
    )
    reading.add_argument(
        "--model",
        choices=BACKENDS,
        default="none",
        help=(
            "reading backend: 'local' for a loopback endpoint (nothing leaves "
            "the machine), 'api' for a remote one, 'command' for a subprocess, "
            "'none' to disable (default)"
        ),
    )
    reading.add_argument("--model-name", default="", help="model identifier the backend expects")
    reading.add_argument(
        "--model-base-url",
        default="",
        help="chat-completions base URL; required for 'api', defaults to a loopback address for 'local'",
    )
    reading.add_argument(
        "--model-token",
        default=None,
        help=f"bearer token for 'api' (else ${DEFAULT_TOKEN_ENV})",
    )
    reading.add_argument(
        "--model-token-env",
        default=DEFAULT_TOKEN_ENV,
        help="environment variable holding the bearer token",
    )
    reading.add_argument(
        "--model-command",
        default="",
        help="for 'command': a program that reads the prompt on stdin and writes the reply on stdout",
    )
    reading.add_argument(
        "--model-param",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help=(
            "extra key sent in the request body; repeatable. A JSON value is "
            "sent as JSON and a dotted key nests. Use this to suppress a "
            "reasoning scratchpad that would otherwise consume the reply budget"
        ),
    )
    reading.add_argument(
        "--model-max-reads",
        type=int,
        default=0,
        metavar="N",
        help="stop after reading N opinions; 0 means no cap. Reading is the expensive part of a run",
    )
    reading.add_argument(
        "--model-max-tokens", type=int, default=DEFAULT_MAX_TOKENS,
        help="reply budget per reading",
    )
    reading.add_argument(
        "--model-timeout", type=int, default=DEFAULT_TIMEOUT_S, help="seconds to wait for one reading",
    )
    reading.add_argument(
        "--model-rpm", type=float, default=None, metavar="N",
        help="pace readings to at most N per minute",
    )
    reading.add_argument(
        "--model-cost-per-1k-input", type=float, default=None, metavar="RATE",
        help="your rate per 1,000 input tokens, for the cost line in the report",
    )
    reading.add_argument(
        "--model-cost-per-1k-output", type=float, default=None, metavar="RATE",
        help="your rate per 1,000 output tokens",
    )
    reading.add_argument(
        "--model-cost-currency", default="", metavar="CODE",
        help="currency label for the cost estimate",
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
    parser.add_argument(
        "--no-statutes",
        action="store_true",
        help=(
            "skip statutory and regulatory verification against the free "
            "government sources (no credential is required for it)"
        ),
    )
    parser.add_argument(
        "--download-code-titles",
        action="store_true",
        help=(
            "allow downloading a US Code title (~18MB, cached) when a citation "
            "names a subsection. Without this, subsections are reported "
            "unchecked rather than guessed at"
        ),
    )
    parser.add_argument(
        "--plain",
        action="store_true",
        help=(
            "also write plain-english.md, the same findings written for someone "
            "who is not a lawyer. Intended for self-represented litigants, who "
            "file the majority of documents in which courts have found fabricated "
            "citations"
        ),
    )
    parser.add_argument(
        "--no-record",
        action="store_true",
        help=(
            "skip writing verification-record.md, the dated record of what was "
            "checked against what -- the artifact a standing order contemplates"
        ),
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


def _verify_quotes(ledger, client, ledger_path, quiet: bool = False) -> dict:
    """Fetch opinion text once per authority and check every quote against it.

    Returns the text it fetched, keyed by cluster. The reading stage wants the
    same opinions, and CourtListener is a nonprofit on a 5-requests-per-minute
    budget: fetching a cluster twice in one run spends somebody else's
    allowance to learn nothing.
    """
    opinion_client = OpinionClient(client)
    needs_text = [e for e in ledger if e.citation.quoted_language]
    by_cluster: dict[int, list] = {}
    for entry in needs_text:
        if entry.resolved_to and entry.resolved_to.cluster_id:
            by_cluster.setdefault(entry.resolved_to.cluster_id, []).append(entry)
        else:
            verify_quote(entry, [])

    fetched: dict[int, list] = {}
    for cluster_id, entries in by_cluster.items():
        opinions = opinion_client.fetch_for_cluster(cluster_id)
        fetched[cluster_id] = opinions
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
    reading = verify_agreement(ledger)
    ledger.run_meta["quote_agreement"] = reading.to_dict()
    ledger.run_meta["quote_check"] = {
        "authorities_with_quotes": len(by_cluster),
        "opinion_versions_fetched": opinion_client.stats["fetched"],
        "cache_hits": opinion_client.stats["cache_hits"],
        "failed": opinion_client.stats["failed"],
    }
    ledger.checkpoint(ledger_path)
    return fetched


#: A proposition shorter than this is not an assertion the citation is offered
#: for -- it is a signal phrase or a fragment. Mirrors verify_proposition.
_MIN_PROPOSITION_CHARS = 40

#: Citation kinds that can carry a proposition worth reading against.
_READABLE_KINDS = ("full_case", "short_case", "id", "supra")


def _verify_statutes(ledger, ledger_path, args) -> None:
    """Check statutory and regulatory citations against the published text.

    Runs by default and needs no credential: every source consulted here is a
    free federal service. Previously every statutory citation was recorded
    OUT_OF_SCOPE, which meant a fabricated subsection of a real statute passed
    through a citation check untouched.
    """
    from verascite.statutes import parse_statute

    candidates = [e for e in ledger if parse_statute(e.citation) is not None]
    if not candidates:
        return

    client = None
    if not args.offline:
        client = StatuteClient(
            cache_dir=None if args.no_cache else args.cache_dir,
            allow_title_download=args.download_code_titles,
        )
    if not args.quiet:
        print(f"checking {len(candidates)} statutory citation(s)")

    for entry in candidates:
        try:
            verify_statute(entry, client, offline=args.offline)
        except Exception as exc:  # pragma: no cover - defensive
            # A source failure is recorded, never converted into a verdict.
            ledger.run_meta.setdefault("statute_errors", []).append(str(exc))
        ledger.checkpoint(ledger_path)

    ledger.run_meta["statutes"] = {
        "citations": len(candidates),
        "checked": sum(1 for e in candidates if "existence" in e.checks),
        "requests": client.stats["requests"] if client else 0,
        "code_titles_downloaded": bool(args.download_code_titles),
    }
    if not args.download_code_titles:
        ledger.run_meta["statutes"]["subsection_note"] = (
            "subsection structure was not checked: it requires the official "
            "structural text of a US Code title, which is downloaded only with "
            "--download-code-titles"
        )
    ledger.checkpoint(ledger_path)


def _reading_candidates(ledger) -> list:
    """Entries a grounded reading could say something about.

    Filtered here rather than inside the reading stage because every entry
    that survives this filter costs an opinion fetch against a nonprofit's
    rate limit. An entry the reading would decline anyway must not spend one.
    """
    out = []
    for entry in ledger:
        if entry.citation.kind.value not in _READABLE_KINDS:
            continue
        if len((entry.citation.sentence or "").strip()) < _MIN_PROPOSITION_CHARS:
            continue
        if not (entry.resolved_to and entry.resolved_to.cluster_id):
            continue
        # The case-name check has already decided whether what came back is the
        # case the citation names. Reading a proposition against a different
        # case produces a truthful report that the text does not support the
        # claim -- about a citation that is fine. verify_proposition enforces
        # this too; doing it here as well is what saves the fetch.
        name_check = entry.checks.get("case_name") if entry.checks else None
        if name_check is not None and name_check.verdict in (Verdict.FAIL, Verdict.AMBIGUOUS):
            continue
        out.append(entry)
    return out


def _not_read(reason: str) -> CheckResult:
    return CheckResult(Verdict.NOT_CHECKABLE, reason=reason)


def _verify_propositions(ledger, client, reader, fetched, ledger_path, args) -> None:
    """Read each citation's opinion and judge whether it supports the claim.

    The only stage in the pipeline that is not reproducible, and the only one
    that can reach the largest defect class. Its output is bounded on every
    side: a reading may produce FAIL only as CONTRADICTED with a span found
    verbatim in the retrieved text, it may never raise a verdict another stage
    set, and a backend failure produces no verdict at all.
    """
    candidates = _reading_candidates(ledger)
    cap = args.model_max_reads if args.model_max_reads and args.model_max_reads > 0 else None
    selected = candidates[:cap] if cap else candidates
    deferred = candidates[len(selected):]

    if not args.quiet and selected:
        where = "on this machine" if reader.backend in ("local", "command") else "off this machine"
        print(
            f"reading {len(selected)} citation(s) against their opinions "
            f"({reader.backend} backend, {where})"
        )

    opinion_client = OpinionClient(client) if client and client.has_token else None
    read = 0
    unreachable = 0

    for entry in selected:
        cluster_id = entry.resolved_to.cluster_id
        opinions = fetched.get(cluster_id)
        if opinions is None:
            if opinion_client is None:
                entry.try_set_check("proposition", _not_read(
                    "the opinion text needed to read this proposition could not "
                    "be retrieved in this run"))
                unreachable += 1
                continue
            opinions = opinion_client.fetch_for_cluster(cluster_id)
            fetched[cluster_id] = opinions

        if not opinions:
            unreachable += 1
        holding = [o for o in opinions if o.is_court_holding and not o.is_syllabus]
        source = holding[0] if holding else (opinions[0] if opinions else None)
        pages = page_texts_for(source) if source is not None else None

        verify_proposition(entry, opinions, ask=reader, page_texts=pages)
        read += 1
        ledger.checkpoint(ledger_path)  # a long reading run must survive Ctrl-C

    for entry in deferred:
        entry.try_set_check("proposition", _not_read(
            f"not read: this run stopped at the --model-max-reads limit of {cap}. "
            "Whether this authority supports the proposition it is cited for is "
            "unexamined, not disputed."))

    ledger.run_meta["grounded_reading"] = {
        **reader.describe(),
        "candidates": len(candidates),
        "read": read,
        "deferred_by_cap": len(deferred),
        "opinion_text_unavailable": unreachable,
        **reader.stats.to_dict(reader.rates),
        "caveat": (
            "Grounded reading is not reproducible: the same document read again, "
            "or read by a different model, may return different answers. Only a "
            "reading that contradicted the document AND quoted the opinion "
            "verbatim could produce a finding; everything else was routed to a "
            "human. Results depend materially on which model was used."
        ),
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


def _run_pipeline_for_mcp(document, offline: bool = False):
    """Run the deterministic stages over an already-ingested document.

    Factored out for the MCP server, which has a document in hand and no output
    directory to write to. Deliberately the deterministic stages only: a server
    answering a drafting system should be fast, reproducible, and free of any
    model the caller did not ask for.
    """
    citations, notes = extract(document)
    ledger = resolve(document, citations, notes)

    client = None
    if not offline:
        client = CourtListenerClient(cache_dir=Path(".verascite-cache"))
    ledger.sources_available = ["reporters_db", "courts_db"] + (
        ["courtlistener_citation_lookup"] if client and client.has_token else []
    )

    statute_client = None if offline else StatuteClient(cache_dir=Path(".verascite-cache"))
    for entry in ledger:
        try:
            verify_statute(entry, statute_client, offline=offline)
        except Exception:
            pass  # a source failure is never a verdict

    try:
        verify_existence(ledger, client, offline=offline, fetch_courts=False)
    except CourtListenerError as exc:
        ledger.run_meta["existence_lookup_error"] = str(exc)
    verify_metadata(ledger)
    return ledger


def _run_batch(args) -> int:
    """Review every document under a directory, then summarise."""
    documents = find_documents(args.document, recursive=not args.no_recurse)
    if not documents:
        print(f"error: no reviewable documents under {args.document}", file=sys.stderr)
        return EXIT_INPUT_ERROR
    if not args.quiet:
        print(f"reviewing {len(documents)} document(s)", file=sys.stderr)

    def runner(document: Path, out_dir: Path):
        # A fresh namespace per document, so one run's flags and resume state
        # cannot leak into the next.
        import copy
        per = copy.copy(args)
        per.document = document
        per.out = out_dir
        per.batch = False
        per.quiet = True
        _run(per)
        return Ledger.load(out_dir / "ledger.json")

    outcomes = run_batch(documents, args.out, runner, quiet=args.quiet)
    summary = write_summary(outcomes, args.out)

    reviewed = [o for o in outcomes if o.ok]
    flagged = sum(o.flagged for o in reviewed)
    failed = len(outcomes) - len(reviewed)
    print(f"reviewed {len(reviewed)} document(s), {flagged} citation(s) flagged"
          + (f", {failed} could not be read" if failed else ""))
    print(f"report  {summary}")
    return EXIT_FLAGGED if flagged else EXIT_OK


def _reader_from_args(args):
    """Build the grounded-reading backend, or None.

    Called before the document is opened. A misconfigured backend must fail on
    the first line of output, not after forty opinion fetches have been spent
    against a nonprofit's rate limit.
    """
    if getattr(args, "_reader", None) is not None:
        return args._reader  # supplied directly by a library caller
    if getattr(args, "model", "none") in (None, "", "none"):
        return None
    return build_reader(
        args.model,
        model=args.model_name,
        base_url=args.model_base_url,
        token=args.model_token,
        token_env=args.model_token_env,
        command=args.model_command,
        timeout_s=args.model_timeout,
        max_tokens=args.model_max_tokens,
        extra_params=parse_model_params(args.model_param),
        rpm=args.model_rpm,
        rates=CostRates(
            input_per_1k=args.model_cost_per_1k_input,
            output_per_1k=args.model_cost_per_1k_output,
            currency=args.model_cost_currency,
        ),
    )


def _run(args) -> int:
    # Built first, so a bad --model configuration costs nothing.
    args._reader = _reader_from_args(args)
    if args._reader is not None and getattr(args, "offline", False):
        raise ReaderError(
            "--offline and --model conflict: reading a proposition requires the "
            "opinion text, which requires the network. Use --model local if the "
            "concern is that citation text not leave the machine -- with it, "
            "nothing is sent anywhere but the archives the tool already queries."
        )
    args.out.mkdir(parents=True, exist_ok=True)

    if getattr(args, "batch", False):
        return _run_batch(args)

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

    # Statutes are decided before case-law existence, and the order is load
    # bearing. verify_existence records every statutory citation OUT_OF_SCOPE,
    # and verdicts are monotonic -- a later stage may lower a row but never
    # raise one. Running the statute stage afterwards therefore left a
    # perfectly sound statutory citation stuck at OUT_OF_SCOPE, reported to the
    # reader as UNVERIFIED. Deciding first, and letting verify_existence skip
    # what is already decided, is what lets a good statute come back PASS.
    if not args.no_statutes:
        _verify_statutes(ledger, ledger_path, args)

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

    fetched_opinions: dict[int, list] = {}
    if not args.offline and not args.no_quotes and client and client.has_token:
        fetched_opinions = _verify_quotes(ledger, client, ledger_path, quiet=args.quiet)
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

    # Proposition support needs a model doing grounded reading. Without one the
    # dimension is recorded as unchecked rather than quietly omitted, so a
    # report never implies a citation's substance was examined when it was not.
    reader = getattr(args, "_reader", None)
    if reader is not None:
        _verify_propositions(
            ledger, client, reader, fetched_opinions, ledger_path, args,
        )
    else:
        for entry in ledger:
            if entry.citation.kind.value in ("full_case", "short_case", "id", "supra"):
                verify_proposition(entry, [], ask=None)
    for entry in ledger:
        if entry.citation.kind.value in ("full_case", "short_case", "id", "supra"):
            verify_treatment(entry)
    ledger.checkpoint(ledger_path)

    # How much of the document the sources could speak to. Computed last, so it
    # reflects every stage, and recorded before the report is rendered.
    ledger.run_meta["coverage"] = measure_coverage(ledger).to_dict()
    ledger.checkpoint(ledger_path)

    report_path = args.out / "report.md"
    write_report(ledger, report_path)

    record_path = None
    if not args.no_record:
        record_path = args.out / "verification-record.md"
        try:
            write_record(ledger, args.document, record_path)
        except OSError as exc:
            # The report is the deliverable; the record must never take a run down.
            print(f"note: could not write the verification record: {exc}", file=sys.stderr)
            record_path = None

    plain_path = None
    if getattr(args, "plain", False):
        plain_path = args.out / "plain-english.md"
        try:
            write_plain(ledger, args.document, plain_path)
        except OSError as exc:
            print(f"note: could not write the plain-language report: {exc}", file=sys.stderr)
            plain_path = None

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
    if not args.quiet:
        print(f"ledger  {ledger_path}")
        print(f"report  {report_path}")
        if record_path:
            print(f"record  {record_path}")
        if plain_path:
            print(f"plain   {plain_path}")
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
    record: bool = True,
    quiet: bool = True,
    ask=None,
    max_reads: int = 0,
) -> Ledger:
    """Audit one document and return the ledger. The library entry point.

    Writes ``report.md`` and ``ledger.json`` into ``out_dir``, exactly as the
    command line does, and returns the in-memory ledger so a caller can act on
    the verdicts without re-reading the JSON.

    ``offline=True`` performs local checks only: no citation string leaves the
    machine. Existence is then reported NOT_CHECKABLE rather than guessed --
    absence of a lookup is not evidence about the citation.

    ``ask`` supplies the grounded reader: any callable taking a prompt and
    returning the model's reply. Pass one built by ``verascite.readers`` to use
    a configured backend, or your own callable to use a host's model. Without
    one the proposition dimension reports NOT_CHECKABLE and the run stays
    reproducible. ``max_reads`` caps how many opinions are read; 0 means no cap.
    """
    args = build_parser().parse_args([str(document), "--out", str(out_dir)])
    args.offline = offline
    args.deterministic_only = deterministic_only
    args.token = token
    args.no_cache = cache_dir is None
    args.cache_dir = cache_dir or Path(".verascite-cache")
    args.no_quotes = not quotes
    args.no_annotate = not annotate
    args.no_record = not record
    args.plain = False
    args.no_statutes = False
    args.download_code_titles = False
    args.quiet = quiet
    args.resume = False
    args.model_max_reads = max_reads
    args._reader = ask
    _run(args)
    return Ledger.load(out_dir / "ledger.json")


if __name__ == "__main__":
    raise SystemExit(main())
