"""Expose VeraScite to an AI assistant as a tool.

Shows the tool schema, the handler, and -- most importantly -- how to phrase
results back to a user without turning absence into an accusation.
"""

from pathlib import Path

from verascite.run_audit import audit_document

TOOL_SCHEMA = {
    "name": "verify_citations",
    "description": (
        "Verify legal citations in a document against public caselaw archives. "
        "Returns an evidence package with a verdict per citation. IMPORTANT: a "
        "verdict of UNVERIFIED means the citation was not found in the sources "
        "consulted -- it does NOT mean the citation is fabricated. Recent, "
        "unpublished, and vendor-only authorities are routinely UNVERIFIED."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to .pdf, .docx, .md or .txt"},
            "offline": {
                "type": "boolean",
                "default": False,
                "description": "Local checks only; no citation leaves the machine",
            },
        },
        "required": ["path"],
    },
}

#: Paste into the system prompt of any assistant that calls the tool.
SYSTEM_PROMPT = """\
You have a citation verification tool. When you report its results:

1. NEVER describe an UNVERIFIED citation as fabricated, fake, hallucinated, or
   non-existent. Say it was not found in the sources consulted, and name them.
2. Only FLAGGED means a source was retrieved and contradicts the document.
   Quote the retrieved evidence, not just the conclusion.
3. Never upgrade a verdict.
4. Never present the output as a certification or as legal advice. Rule 11
   responsibility remains with the filing attorney.
5. The tool is not a citator and cannot say whether an authority is good law.
"""


def verify_citations(path: str, offline: bool = False) -> dict:
    """Tool handler. Returns the ledger as a plain dict."""
    ledger = audit_document(Path(path), out_dir=Path("./audit"), offline=offline)
    return ledger.to_dict()


def summarize_for_user(ledger_dict: dict) -> str:
    """Phrase the result the way a caller is required to phrase it."""
    lines = []
    for entry in ledger_dict["entries"]:
        cite = entry["citation"]["raw_text"]
        if entry["overall"] == "FLAGGED":
            evidence = [
                c.get("evidence", "")
                for c in entry.get("checks", {}).values()
                if c.get("verdict") == "FAIL"
            ]
            lines.append(f"CONTRADICTED -- {cite}: {' '.join(evidence)}")
        elif entry["overall"] == "UNVERIFIED":
            lines.append(
                f"NOT FOUND in the sources consulted -- {cite}. "
                f"This is not a finding of fabrication; verify manually."
            )
    return "\n".join(lines) or "No contradictions found. This is not a certification."


if __name__ == "__main__":
    import json

    print(json.dumps(TOOL_SCHEMA, indent=2))
    print("\n--- system prompt ---\n")
    print(SYSTEM_PROMPT)
