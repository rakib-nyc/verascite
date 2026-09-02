"""Audit a brief and print what a reviewer needs to act on.

    python examples/01_check_a_brief.py path/to/brief.pdf

The point of this example is the separation in `report()`: FLAGGED items are
findings backed by retrieved text, UNVERIFIED items are not findings at all.
"""

import sys
from pathlib import Path

from verascite.run_audit import audit_document


def report(ledger) -> None:
    flagged = [e for e in ledger if e.overall.value == "FLAGGED"]
    unverified = [e for e in ledger if e.overall.value == "UNVERIFIED"]

    if flagged:
        print(f"\n{len(flagged)} citation(s) contradicted by a retrieved source:\n")
        for entry in flagged:
            print(f"  {entry.citation.raw_text}")
            for name, check in entry.checks.items():
                if check.verdict.value == "FAIL":
                    print(f"      {name}: {check.evidence}")

    if unverified:
        # Say what this means, every time. A caller that prints "fabricated"
        # here has turned a neutral observation into a false accusation.
        print(
            f"\n{len(unverified)} citation(s) were NOT FOUND in the sources "
            f"consulted. This is not a finding of fabrication -- recent, "
            f"unpublished, and vendor-only authorities are routinely absent. "
            f"Check these by hand:\n"
        )
        for entry in unverified:
            print(f"  {entry.citation.raw_text}")

    if not flagged and not unverified:
        print("\nNo contradictions found. This is not a certification.")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    ledger = audit_document(Path(sys.argv[1]), out_dir=Path("./audit"))
    report(ledger)
    print("\nEvidence package: ./audit/report.md")
    return 2 if any(e.overall.value == "FLAGGED" for e in ledger) else 0


if __name__ == "__main__":
    raise SystemExit(main())
