"""Expose verification to the systems that generate the citations.

**Why this surface and not another.** The documents in which courts find
fabricated authority are increasingly written with help from a model, and the
model has no way to check its own output: it produces a citation that is
correctly formatted and may be entirely invented, and formatting is exactly
what a human reviewer uses to judge whether a citation looks real. The useful
place to intervene is therefore inside the drafting system, before anything
reaches a filing — not in a separate application a person has to remember to
open afterwards.

**What this adds that a lookup endpoint does not.** Free lookup APIs already
exist and answer "is there a case at this citation." The thing they do not do,
and the thing that matters most, is *decline properly*. Roughly one citation in
ten in a real brief is absent from every free archive, and a caller that reads
absence as fabrication will accuse sound authority a tenth of the time.
Published figures for that failure run from 25% to 66%. This server returns the
same three-state answer the rest of the tool does, so a calling system inherits
the discipline instead of having to reimplement it:

``contradicted``
    A source was retrieved and it disagrees with the document. Evidence attached.
``unverified``
    Not in the sources consulted, which are named. **Not a fabrication finding.**
``confirmed``
    Checked against a retrieved source on every dimension that applied.

**Protocol.** JSON-RPC 2.0 over stdio, line-delimited, no dependencies beyond
the standard library. Run it with ``python -m verascite.mcp_server``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .coverage import measure as measure_coverage
from .extract import extract
from .ingest import ingest
from .models import Document
from .resolve import resolve
from .verdicts import Overall, Verdict

PROTOCOL_VERSION = "2024-11-05"

#: The wire vocabulary. Deliberately not the internal enum names: a calling
#: system that sees "NOT_FOUND" will render it as "not found", and a person
#: reading that in a chat window hears "this case does not exist". The whole
#: point of the tool is that those are different statements.
WIRE = {
    Overall.FLAGGED: "contradicted",
    Overall.UNVERIFIED: "unverified",
    Overall.REVIEW: "needs_review",
    Overall.VERIFIED: "confirmed",
    Overall.PENDING: "unchecked",
}

#: Sent with every result. A calling model will paraphrase whatever it is given,
#: so the constraint has to travel with the payload rather than sit in docs.
CONTRACT = (
    "Rules for reporting these results. 'unverified' means the citation was not "
    "found in the sources named for it. It does NOT mean the citation is "
    "fabricated, fake, or hallucinated, and it must never be described that way: "
    "recent decisions, unpublished dispositions, state trial court orders and "
    "vendor-only identifiers are absent from free archives as a matter of "
    "routine. Only 'contradicted' means a source was retrieved and disagrees "
    "with the document; quote the attached evidence rather than the label. Never "
    "upgrade a result. This is not legal advice and it certifies nothing; "
    "responsibility for the filing remains with the person filing it."
)

TOOLS = [
    {
        "name": "verify_citations",
        "description": (
            "Verify the legal citations in a document or a block of text against "
            "free public caselaw archives and the government's published statutory "
            "text. Returns one result per citation with the sources consulted. "
            "IMPORTANT: a result of 'unverified' means the citation was not found "
            "in the sources consulted -- it does NOT mean the citation is "
            "fabricated. Recent, unpublished and vendor-only authorities are "
            "routinely unverified."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Document text to check."},
                "path": {"type": "string", "description": "Path to a .pdf/.docx/.md/.txt file."},
                "offline": {
                    "type": "boolean", "default": False,
                    "description": "Local checks only; no citation string leaves the machine.",
                },
            },
        },
    },
    {
        "name": "extract_citations",
        "description": (
            "Extract the citations from text without checking them. Useful for "
            "counting or for deciding what to verify. Makes no network request."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
]


def _entry_result(entry) -> dict:
    """One citation, in the wire vocabulary, with its evidence."""
    sources: list[str] = []
    evidence: list[dict] = []
    for name, check in (entry.checks or {}).items():
        for source in (check.sources_consulted or []):
            if source not in sources:
                sources.append(source)
        if check.verdict is Verdict.FAIL and check.evidence:
            evidence.append({"check": name, "finding": check.evidence})

    out: dict[str, Any] = {
        "citation": " ".join((entry.citation.raw_text or "").split()),
        "result": WIRE.get(entry.overall, "unchecked"),
        "sources_consulted": sources,
    }
    if evidence:
        out["evidence"] = evidence
    if entry.overall is Overall.UNVERIFIED:
        out["note"] = (
            "Not found in the sources consulted. This is not a finding that the "
            "citation is fabricated."
        )
    if entry.resolved_to is not None:
        resolution = entry.resolved_to
        out["resolved_to"] = {
            k: v for k, v in (
                ("case_name", resolution.case_name),
                ("url", resolution.url),
                ("retrieved_at", resolution.retrieved_at),
            ) if v
        }
    return out


def verify(text: str = "", path: str = "", offline: bool = False) -> dict:
    """Run the pipeline over text or a file and return wire-shaped results."""
    if path:
        document = ingest(Path(path))
    elif text:
        document = Document(text=text, source_path="<text>", source_format="txt")
    else:
        raise ValueError("supply either 'text' or 'path'")

    # Imported here so the module loads without a network stack present.
    from .run_audit import _run_pipeline_for_mcp

    ledger = _run_pipeline_for_mcp(document, offline=offline)
    coverage = measure_coverage(ledger)
    counts = ledger.counts()
    return {
        "summary": {
            "citations": len(ledger),
            "contradicted": counts.get(Overall.FLAGGED.value, 0),
            "unverified": counts.get(Overall.UNVERIFIED.value, 0),
            "needs_review": counts.get(Overall.REVIEW.value, 0),
            "confirmed": counts.get(Overall.VERIFIED.value, 0),
        },
        "coverage": coverage.statement(),
        "citations": [_entry_result(e) for e in ledger.sorted_by_severity()],
        "how_to_report_this": CONTRACT,
        "tool_version": __version__,
    }


def extract_only(text: str) -> dict:
    document = Document(text=text, source_path="<text>", source_format="txt")
    cites, notes = extract(document)
    ledger = resolve(document, cites, notes)
    return {
        "citations": [
            {
                "citation": " ".join((e.citation.raw_text or "").split()),
                "kind": e.citation.kind.value,
            }
            for e in ledger
        ],
        "count": len(ledger),
    }


# --- JSON-RPC plumbing --------------------------------------------------------

def _result(request_id, payload):
    return {"jsonrpc": "2.0", "id": request_id, "result": payload}


def _error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def handle(request: dict) -> dict | None:
    """One request in, one response out. None for a notification."""
    method = request.get("method")
    request_id = request.get("id")
    params = request.get("params") or {}

    if method == "initialize":
        return _result(request_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "verascite", "version": __version__},
            "instructions": CONTRACT,
        })
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "tools/list":
        return _result(request_id, {"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        try:
            if name == "verify_citations":
                payload = verify(
                    text=args.get("text", ""), path=args.get("path", ""),
                    offline=bool(args.get("offline", False)),
                )
            elif name == "extract_citations":
                payload = extract_only(args.get("text", ""))
            else:
                return _error(request_id, -32601, f"unknown tool: {name}")
        except Exception as exc:
            # A failure is reported as a failure, never as a verdict.
            return _result(request_id, {
                "content": [{"type": "text", "text": json.dumps({
                    "error": f"{type(exc).__name__}: {exc}",
                    "note": "This is a tool failure, not a finding about any citation.",
                }, indent=2)}],
                "isError": True,
            })
        return _result(request_id, {
            "content": [{"type": "text", "text": json.dumps(payload, indent=2)}]
        })
    if request_id is None:
        return None
    return _error(request_id, -32601, f"unknown method: {method}")


def serve(stdin=None, stdout=None) -> int:
    """Read line-delimited JSON-RPC from stdin, write responses to stdout."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            stdout.write(json.dumps(_error(None, -32700, "parse error")) + "\n")
            stdout.flush()
            continue
        response = handle(request)
        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(serve())
