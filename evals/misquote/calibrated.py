"""Does calibrating to the document's own agreement level rescue the check?

The threshold sweep in this directory establishes two things. Archive OCR is
**not** the variable that matters: the non-OCR and OCR subsets track each other
at every noise level. What matters is how closely the quoting document and the
archive agree, and a fixed global threshold cannot know that -- it is applied to
a brief whose quotations agree at 0.999 and to one whose quotations agree at
0.95 with the same number, and it is wrong for one of them.

A document, however, carries the evidence needed to calibrate itself. A brief
quotes many authorities. If nine of its quotations match their sources at 0.999
and the tenth matches at 0.977, the tenth is anomalous *for this document*. If
all ten match at 0.95, the document is noisy and nothing can be concluded about
any of them.

This measures whether that works: build synthetic documents of N quotations at
a given noise level, plant one alteration, and ask whether an outlier rule
finds it without flagging the rest. It is measured before it is built, and if
the numbers do not support it the rejection stands.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from build_and_measure import (  # noqa: E402
    add_noise, alter, load_opinions, match_ratio, passages,
)

#: A document with fewer quotations than this cannot calibrate: one or two
#: ratios establish no distribution, and treating them as one is how a check
#: starts inventing findings on thin evidence.
MIN_QUOTES_TO_CALIBRATE = 6


#: A document whose own quotations agree with their sources less well than
#: this is too noisy to calibrate against. The check declines rather than
#: guessing: below this floor the misquote and correct distributions overlap,
#: and every finding would be a coin toss dressed as evidence.
MIN_DOCUMENT_AGREEMENT = 0.99


def outlier_flags(ratios: list[float], margin: float,
                  floor: float = MIN_DOCUMENT_AGREEMENT) -> list[bool]:
    """Which ratios sit far enough below the document's own level to be odd.

    The reference point is the median rather than the mean, because a document
    containing a real misquote should not have its baseline dragged down by it.
    """
    if len(ratios) < MIN_QUOTES_TO_CALIBRATE:
        return [False] * len(ratios)
    baseline = statistics.median(ratios)
    if baseline < floor:
        # The document does not agree with its own sources well enough for a
        # one-word difference to mean anything. Declining is the finding.
        return [False] * len(ratios)
    return [r < baseline - margin for r in ratios]


def build_documents(cache, ocr, noise, per_doc, n_docs, seed):
    """Synthetic documents: per_doc quotations, exactly one of them altered."""
    rng = random.Random(seed)
    records = load_opinions(cache, ocr=ocr)
    rng.shuffle(records)
    pool = []
    for record in records:
        for passage in passages(record, rng, 3):
            pool.append((record, passage))
    rng.shuffle(pool)

    documents = []
    index = 0
    while len(documents) < n_docs and index + per_doc <= len(pool):
        chunk = pool[index:index + per_doc]
        index += per_doc
        planted = rng.randrange(per_doc)
        rows = []
        for position, (record, passage) in enumerate(chunk):
            if position == planted:
                altered, swaps = alter(passage, rng.choice((1, 2)), rng)
                if not swaps:
                    rows = []
                    break
                text, is_misquote = add_noise(altered, rng, noise), True
            else:
                text, is_misquote = add_noise(passage, rng, noise), False
            ratio = match_ratio(text, record)
            if ratio < 0:
                rows = []
                break
            rows.append({"ratio": ratio, "misquote": is_misquote})
        if rows:
            documents.append(rows)
    return documents


def score(documents, margin, floor=MIN_DOCUMENT_AGREEMENT):
    tp = fp = fn = 0
    declined = 0
    for rows in documents:
        ratios = [r["ratio"] for r in rows]
        flags = outlier_flags(ratios, margin, floor)
        if not any(flags) and statistics.median(ratios) < floor:
            declined += 1
        for row, flagged in zip(rows, flags):
            if flagged and row["misquote"]:
                tp += 1
            elif flagged and not row["misquote"]:
                fp += 1
            elif not flagged and row["misquote"]:
                fn += 1
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    return {
        "tp": tp, "fp": fp, "fn": fn, "documents_declined": declined,
        "precision": round(precision, 4) if precision is not None else None,
        "recall": round(recall, 4) if recall is not None else None,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path,
                        default=Path.home() / "Documents/verascite/.verascite-cache")
    parser.add_argument("--per-doc", type=int, default=10)
    parser.add_argument("--documents", type=int, default=40)
    parser.add_argument("--seed", type=int, default=20260909)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    result = {"per_doc": args.per_doc, "documents": args.documents, "runs": []}
    for noise in (0.0, 0.02, 0.05, 0.10, 0.20):
        docs = build_documents(args.cache, False, noise, args.per_doc,
                               args.documents, args.seed)
        for margin in (0.005, 0.01, 0.02, 0.03, 0.05):
            row = {"noise": noise, "margin": margin, "documents": len(docs),
                   **score(docs, margin)}
            result["runs"].append(row)
            print(f"noise={noise:<5} margin={margin:<6} docs={len(docs):<3} "
                  f"P={row['precision']} R={row['recall']} "
                  f"(tp={row['tp']} fp={row['fp']} fn={row['fn']} "
                  f"declined={row['documents_declined']})", flush=True)
    args.out.write_text(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
