"""Render results.json into the README table.

Generated rather than transcribed: a benchmark number retyped by hand is a
benchmark number that can drift from the file it came from.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

LABELS = {
    "non_existent_citation": "Non-existent citation",
    "case_name_mismatch": "Case name mismatch",
    "wrong_pincite": "Incorrect pincite",
    "misquote": "Verbatim misquote",
    "content_misrepresentation": "Content misrepresentation",
}


def render(path: Path) -> str:
    r = json.loads(path.read_text())
    counts = r["counts"]
    fpr = r.get("false_positive_rate_on_absent_negatives")
    lines = [
        "| Metric | Value |",
        "|---|---:|",
        f"| Precision | **{r['precision']:.1%}** |",
        f"| Recall | {r['recall']:.1%} |",
        f"| F1 | {r['f1']:.1%} |",
        (
            f"| **False-positive rate on correct-but-absent citations** | "
            f"**{fpr:.1%}** ({r['absent_negatives_flagged']}/"
            f"{r['absent_negatives_subset_size']}) |"
            if fpr is not None
            else "| False-positive rate on correct-but-absent citations | n/a |"
        ),
        f"| True positives / false positives / false negatives | "
        f"{counts['tp']} / {counts['fp']} / {counts['fn']} |",
        "",
        "Per hallucination type:",
        "",
        "| Type | Gold | Detected | Recall |",
        "|---|---:|---:|---:|",
    ]
    for key, label in LABELS.items():
        data = r["per_category_recall"][key]
        recall = data["recall"]
        lines.append(
            f"| {label} | {data['gold']} | {data['detected']} | "
            + (f"{recall:.1%} |" if recall is not None else "n/a |")
        )
    lines += [
        "",
        f"Deterministic layer only, thresholds version `{r['thresholds_version']}`, "
        f"{r['excerpts_scored']} excerpts scored, {r['excerpts_excluded']} excluded "
        f"per protocol. Run took {r['wall_clock_seconds'] / 3600:.1f} hours.",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1
                  else Path(__file__).parent / "results" / "results.json")
    print(render(target))
