"""Fail a build when a citation is contradicted -- and only then.

    python examples/02_ci_gate.py generated-memo.md

A gate that also failed on UNVERIFIED would break every time an author cited a
decision too recent for a free archive, which is why it does not.
"""

import sys
from pathlib import Path

from verascite.run_audit import audit_document


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    ledger = audit_document(
        Path(sys.argv[1]), out_dir=Path("./audit"), offline=False, quiet=True
    )

    contradicted = [e for e in ledger if e.overall.value == "FLAGGED"]
    for entry in contradicted:
        failures = [
            f"{name}: {check.evidence}"
            for name, check in entry.checks.items()
            if check.verdict.value == "FAIL"
        ]
        print(f"::error:: {entry.citation.raw_text} -- {'; '.join(failures)}")

    absent = sum(1 for e in ledger if e.overall.value == "UNVERIFIED")
    if absent:
        print(f"::notice:: {absent} citation(s) not in consulted sources (not an error)")

    return 1 if contradicted else 0


if __name__ == "__main__":
    raise SystemExit(main())
