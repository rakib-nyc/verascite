"""Turn a ledger into a reviewer's worklist, most urgent first.

    python examples/04_triage_report.py ./audit/ledger.json
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

#: Severity order used throughout VeraScite. A contradiction outranks an
#: absence, because only one of the two is a finding.
ORDER = ["FLAGGED", "UNVERIFIED", "REVIEW", "PENDING", "VERIFIED"]

HEADINGS = {
    "FLAGGED": "Fix before filing -- contradicted by a retrieved source",
    "UNVERIFIED": "Verify by hand -- absent from consulted sources (NOT fabrication)",
    "REVIEW": "Read these -- something needs judgement",
    "PENDING": "Not yet checked",
    "VERIFIED": "Confirmed on the dimensions checked (not a good-law statement)",
}


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    ledger = json.loads(Path(sys.argv[1]).read_text())
    buckets = defaultdict(list)
    for entry in ledger["entries"]:
        buckets[entry["overall"]].append(entry)

    for result in ORDER:
        entries = buckets.get(result)
        if not entries:
            continue
        print(f"\n## {HEADINGS[result]} ({len(entries)})\n")
        for entry in entries:
            print(f"- {entry['citation']['raw_text']}")
            for name, check in entry.get("checks", {}).items():
                detail = check.get("evidence") or check.get("reason")
                if detail and check.get("verdict") in {"FAIL", "NOT_FOUND"}:
                    print(f"    - {name}: {detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
